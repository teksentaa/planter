#!/usr/bin/env python3
"""Seed Packet Planner sync server.

Serves the web app and stores the seed catalog in SQLite so every device sees the same data.
Python standard library only. Listens on 127.0.0.1; Tailscale Serve provides HTTPS and
limits access to your own devices.

Environment (set in /etc/seed-planner.env):
  ANTHROPIC_API_KEY  optional; lets Claude read packet photos for every device
  CLAUDE_MODEL       default claude-sonnet-5-5
  PORT               default 8787
  SEED_DB            database path, default ./seeds.db
"""
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import packetreader
import seedsites

BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, "public")
DB_PATH = os.environ.get("SEED_DB", os.path.join(BASE, "seeds.db"))
PHOTO_DIR = os.environ.get("SEED_PHOTOS", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "photos"))
PHOTO_ID = re.compile(r"^p-[a-z0-9]{4,40}$")
MAX_PHOTO = 8 * 1024 * 1024
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
PORT = int(os.environ.get("PORT", "8787"))
MAX_BODY = 12 * 1024 * 1024  # room for a packet photo

_lock = threading.Lock()


def connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS records ("
        " id TEXT PRIMARY KEY, updated REAL NOT NULL, body TEXT NOT NULL)"
    )
    con.execute("CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, fetched REAL NOT NULL, body TEXT NOT NULL)")
    return con


def cache_get(url, ttl):
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute("SELECT fetched, body FROM pages WHERE url=?", (url,)).fetchone()
    finally:
        con.close()
    return row[1] if row and time.time() - row[0] < ttl else None


def cache_put(url, body):
    with _lock:
        con = sqlite3.connect(DB_PATH)
        try:
            con.execute("INSERT OR REPLACE INTO pages (url, fetched, body) VALUES (?, ?, ?)", (url, time.time(), body))
            con.commit()
        finally:
            con.close()


SITES = seedsites.SeedSites(seedsites.Fetcher(cache_get, cache_put))


def upc_lookup(code):
    req = urllib.request.Request(
        f"https://api.upcitemdb.com/prod/trial/lookup?upc={code}",
        headers={"User-Agent": "SeedPlanner/1", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        j = json.load(r)
    item = (j.get("items") or [None])[0]
    return {"title": item.get("title", ""), "brand": item.get("brand", "")} if item else None


def read_state(con):
    seeds, settings = [], None
    for (body,) in con.execute("SELECT body FROM records ORDER BY id DESC"):
        rec = json.loads(body)
        if rec.get("id") == "settings":
            settings = rec
        else:
            seeds.append(rec)
    return {"seeds": seeds, "settings": settings, "claude": bool(API_KEY), "reader": packetreader.status(), "time": time.time() * 1000}


def merge(con, records):
    """Newest updatedAt wins, per record. Deleted seeds stay as {id, deleted, updatedAt} markers."""
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rid = str(rec.get("id", ""))
        if not rid or len(rid) > 80 or not (rid == "settings" or rid.startswith("s-")):
            continue
        try:
            updated = float(rec.get("updatedAt") or 0)
        except (TypeError, ValueError):
            continue
        row = con.execute("SELECT updated FROM records WHERE id=?", (rid,)).fetchone()
        if row is None or updated > row[0]:
            con.execute(
                "INSERT OR REPLACE INTO records (id, updated, body) VALUES (?, ?, ?)",
                (rid, updated, json.dumps(rec)),
            )
    con.commit()


class Handler(SimpleHTTPRequestHandler):
    server_version = "SeedPlanner/1"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=PUBLIC, **kw)

    def log_request(self, code="-", size="-"):  # quieter journal: only errors
        try:
            if int(code) >= 400:
                super().log_request(code, size)
        except (TypeError, ValueError):
            pass

    def end_headers(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/sw.js", "/manifest.webmanifest") or path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    # ---- helpers
    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            raise ValueError("bad length")
        return json.loads(self.rfile.read(n))

    # ---- routes
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/state":
            with _lock:
                con = connect()
                try:
                    return self.send_json(read_state(con))
                finally:
                    con.close()
        if url.path == "/api/upc":
            code = "".join(ch for ch in urllib.parse.parse_qs(url.query).get("code", [""])[0] if ch.isdigit())[:14]
            if not code:
                return self.send_json({"error": "missing code"}, 400)
            try:
                item = upc_lookup(code)
                return self.send_json(item) if item else self.send_json({"error": "not found"}, 404)
            except Exception:
                return self.send_json({"error": "lookup failed"}, 502)
        if url.path == "/api/lookup":
            qs = urllib.parse.parse_qs(url.query)
            arg = lambda k: (qs.get(k) or [""])[0].strip()
            try:
                if arg("url"):
                    return self.send_json({"result": SITES.product(arg("url"), arg("site") or None), "candidates": []})
                q, brand, product = arg("q")[:200], arg("brand")[:80], None
                code = "".join(ch for ch in arg("code") if ch.isdigit())[:14]
                if code and not q:
                    try:
                        product = upc_lookup(code)
                    except Exception:
                        product = None
                    if product:
                        q, brand = product["title"], brand or product["brand"]
                if not q:
                    return self.send_json({"result": None, "candidates": [], "product": product, "error": "nothing to search for"})
                out = SITES.lookup(q, arg("site") or None, brand)
                out["product"] = product
                return self.send_json(out)
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            except Exception as e:
                return self.send_json({"error": "lookup failed", "detail": str(e)[:200]}, 502)
        if url.path.startswith("/api/photos/"):
            pid = url.path.rsplit("/", 1)[-1]
            path = os.path.join(PHOTO_DIR, pid + ".jpg")
            if not PHOTO_ID.match(pid) or not os.path.isfile(path):
                return self.send_json({"error": "not found"}, 404)
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            super().end_headers()
            self.wfile.write(data)
            return
        if url.path.startswith("/api/"):
            return self.send_json({"error": "not found"}, 404)
        return super().do_GET()

    def do_PUT(self):
        url = urllib.parse.urlparse(self.path)
        if not url.path.startswith("/api/photos/"):
            return self.send_json({"error": "not found"}, 404)
        pid = url.path.rsplit("/", 1)[-1]
        n = int(self.headers.get("Content-Length") or 0)
        if not PHOTO_ID.match(pid) or n <= 0 or n > MAX_PHOTO:
            return self.send_json({"error": "bad photo"}, 400)
        data = self.rfile.read(n)
        if not data.startswith(b"\xff\xd8"):
            return self.send_json({"error": "photos must be JPEG"}, 415)
        os.makedirs(PHOTO_DIR, exist_ok=True)
        path = os.path.join(PHOTO_DIR, pid + ".jpg")
        if not os.path.exists(path):  # photos never change; first upload wins
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        return self.send_json({"ok": True, "id": pid})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        try:
            body = self.read_json()
        except Exception:
            return self.send_json({"error": "bad request"}, 400)

        if url.path == "/api/sync":
            records = body.get("records") if isinstance(body, dict) else None
            if not isinstance(records, list):
                return self.send_json({"error": "records must be a list"}, 400)
            with _lock:
                con = connect()
                try:
                    merge(con, records)
                    return self.send_json(read_state(con))
                finally:
                    con.close()

        if url.path == "/api/read-packet":
            imgs = body.get("images") if isinstance(body, dict) else None
            if not isinstance(imgs, list) or not imgs or not all(isinstance(x, str) for x in imgs):
                return self.send_json({"error": "send images as a list of base64 JPEGs"}, 400)
            try:
                return self.send_json(packetreader.read(imgs[:3]))
            except Exception as e:
                return self.send_json({"error": str(e)[:300]}, 503)

        if url.path == "/api/claude":
            if not API_KEY:
                return self.send_json({"error": "no API key on server"}, 503)
            payload = {
                "model": MODEL,
                "max_tokens": min(int(body.get("max_tokens") or 900), 2000),
                "messages": [{"role": "user", "content": body.get("content")}],
            }
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload).encode(),
                headers={"content-type": "application/json", "x-api-key": API_KEY, "anthropic-version": "2023-06-01"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    return self.send_json(json.load(r))
            except urllib.error.HTTPError as e:
                return self.send_json({"error": f"Claude request failed ({e.code})"}, e.code)
            except Exception:
                return self.send_json({"error": "Claude could not be reached"}, 502)

        return self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--read-packet":
        # Command-line test:  python3 server.py --read-packet back.jpg [front.jpg]
        import base64
        out = packetreader.read([base64.b64encode(open(f, "rb").read()).decode() for f in sys.argv[2:]])
        print(json.dumps({k: v for k, v in out.items() if k != "text"}, indent=2))
        print("\n--- text read from the photo ---\n" + out.get("text", "")[:1500])
        sys.exit(0)
    if len(sys.argv) > 2 and sys.argv[1] == "--lookup":
        # Command-line test:  python3 server.py --lookup "cherokee purple tomato" [bakercreek|eden|burpee|victory|johnnys]
        #                     python3 server.py --lookup https://www.rareseeds.com/products/tomato-cherokee-purple
        connect().close()
        target = sys.argv[2]
        if target.startswith("http"):
            print(json.dumps(SITES.product(target), indent=2))
        else:
            out = SITES.lookup(target, sys.argv[3] if len(sys.argv) > 3 else None)
            print(json.dumps(out, indent=2))
        sys.exit(0)
    connect().close()
    os.makedirs(PHOTO_DIR, exist_ok=True)
    print(f"Seed Planner on http://127.0.0.1:{PORT}  db={DB_PATH}  claude={'on' if API_KEY else 'off'}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
