"""Reads seed packet photos on this server, with no cloud service.

Two engines, tried in this order:
  1. A local vision model through Ollama (optional). Set VISION_MODEL in /etc/seed-planner.env,
     e.g. VISION_MODEL=qwen2.5vl:7b. Best on decorative fronts and curved, glossy packets.
  2. Tesseract text recognition (installed by install.sh). Works on any CPU; best on the flat,
     printed back of the packet. Its text goes through the same parsing rules as seed-company pages.

read(images) returns {"engine", "seed", "stated", "text"}: "stated" lists the fields the packet
itself gave (as opposed to typical values for the plant), so the app knows which to trust.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request

import seedsites

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
VISION_MODEL = os.environ.get("VISION_MODEL", "").strip()
NUM_FIELDS = ["spacingIn", "depthIn", "germDays", "dtm", "indoorWeeks", "transplantWeeks", "directWeeks"]
TIMING = ["indoorWeeks", "transplantWeeks", "directWeeks"]

BRANDS = ["Burpee", "Baker Creek", "Ferry-Morse", "Ferry Morse", "Botanical Interests", "Johnny's", "Eden Brothers",
          "Victory Seed", "Seed Savers", "Territorial", "High Mowing", "Renee's Garden", "Hart's", "Lake Valley",
          "American Seed", "Livingston", "Park Seed", "Sow True", "Southern Exposure", "Hudson Valley Seed",
          "Burpee Organic", "Seeds of Change", "Mr. Fothergill", "Thompson & Morgan", "Pinetree", "Fedco", "Hometown Seeds",
          "Plantation Products", "NK Lawn", "Gurney's", "Jung", "Harris Seeds", "Hirt's"]


def status():
    return {"ocr": bool(shutil.which("tesseract")), "vision": VISION_MODEL}


# ------------------------------------------------------------------ vision model

PROMPT = """You read garden seed packets for a planting app. The images show one seed packet (front and/or back).
Use only what is printed on the packet. If something isn't printed, use null.
Answer with JSON only:
{"name":"common plant name, e.g. Tomato","variety":"variety name or empty","brand":"seed company or empty",
"category":"vegetable|flower|herb|fruit","sun":"short phrase or null","spacingIn":number|null,"depthIn":number|null,
"germDays":number|null,"dtm":days to maturity|null,
"indoorWeeks":weeks BEFORE last frost to start indoors (positive) or null,
"transplantWeeks":weeks relative to last frost to transplant (negative = before) or null,
"directWeeks":weeks relative to last frost to sow outdoors (negative = before) or null,
"year":packed-for or sell-by year or null,
"instructions":"the packet's sowing and growing directions, copied word for word as one paragraph, or empty",
"notes":"the packet's short description of the plant (flavor, size, uses), or empty"}
Convert fractions like 1/4 inch to 0.25. For ranges like 6-8 weeks use the middle."""


def read_vision(images_b64):
    body = json.dumps({"model": VISION_MODEL, "stream": False, "format": "json", "options": {"temperature": 0},
                       "messages": [{"role": "user", "content": PROMPT, "images": images_b64}]}).encode()
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as r:
        j = json.load(r)
    text = (j.get("message") or {}).get("content", "")
    a, b = text.find("{"), text.rfind("}")
    v = json.loads(text[a:b + 1])
    seed = {}
    for k in ["name", "variety", "brand", "category", "sun", "notes", "instructions"]:
        if isinstance(v.get(k), str) and v[k].strip():
            seed[k] = v[k].strip()
    for k in NUM_FIELDS + ["year"]:
        try:
            seed[k] = None if v.get(k) in (None, "") else float(v[k])
            if seed[k] is not None and seed[k] == int(seed[k]):
                seed[k] = int(seed[k])
        except (TypeError, ValueError):
            seed[k] = None
    if seed.get("category") not in ("vegetable", "flower", "herb", "fruit"):
        crop = seedsites.match_crop(seed.get("name", ""))
        seed["category"] = crop[1] if crop else "vegetable"
    stated = [k for k, x in seed.items() if x not in (None, "")]
    return {"engine": f"vision model ({VISION_MODEL})", "seed": seed, "stated": stated, "text": ""}


# ------------------------------------------------------------------ Tesseract

def _prep(path):
    """Grayscale, upscale small photos and boost contrast; skipped if Pillow isn't installed."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return [path]
    out = []
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("L")
        if max(im.size) < 2000:
            k = 2000 / max(im.size)
            im = im.resize((int(im.width * k), int(im.height * k)))
        im = ImageOps.autocontrast(im, cutoff=1)
        p1 = path + ".g.png"
        im.save(p1)
        out.append(p1)
    return out


def ocr(path):
    texts = []
    for p in _prep(path):
        for psm in ("3", "11"):  # 3: normal page layout, 11: scattered text (decorative fronts)
            try:
                r = subprocess.run(["tesseract", p, "stdout", "--psm", psm, "-l", "eng"], capture_output=True, text=True, timeout=90)
                texts.append(r.stdout)
            except Exception:
                pass
    return texts


PACKET_PATTERNS = {
    "dtm": [r"(\d{2,3})\s*(?:-|–|to)?\s*(?:\d{2,3})?\s*days?\s*(?:to|until)\s*(?:maturity|harvest|bloom)",
            r"(?:maturity|harvest|matures in|days to maturity)\D{0,12}(\d{2,3})",
            r"(?:blooms?|flowers?|ready|harvest)\s+in\D{0,8}(\d{2,3})\s*(?:-|–|to)?\s*\d{0,3}\s*days",
            r"(?<![\d-])([3-9]\d|1\d\d)\s*(?:-|–|to)?\s*\d{0,3}\s*days\b(?!\s*(?:to\s+)?(?:germinat|emerg|sprout))"],
    "germDays": [r"(?:germinat\w*|emerge\w*|sprouts?|seedlings appear)\D{0,25}(\d{1,2})\s*(?:-|–|to)?\s*\d{0,2}\s*days",
                 r"(\d{1,2})\s*(?:-|–|to)\s*\d{1,2}\s*days\s*to\s*(?:germinat|emerg|sprout)"],
    # Most specific wording first: '1" deep' before 'depth ... 1"'.
    "depthIn": [rf"({seedsites.NUMTOK})\s*(?:\"|”|″|in\.?|inch(?:es)?)\s*deep",
                rf"(?:depth|cover)\D{{0,15}}({seedsites.NUMTOK})\s*(?:\"|”|″|in\b|inch)"],
    "spacingIn": [rf"({seedsites.NUMTOK})\s*(?:\"|”|″|in\.?|inch(?:es)?)\s*apart",
                  rf"(?:spacing|space plants|thin(?:\s+seedlings)?\s+to)\D{{0,15}}({seedsites.NUMTOK})\s*(?:\"|”|″|in\b|inch)"],
}


HEADINGS = re.compile(r"^(?:sowing|planting|how to (?:grow|sow|plant)|growing(?: instructions| tips)?|directions|to grow|"
                      r"sowing (?:instructions|directions)|planting (?:instructions|directions)|culture|cultivation)\b\s*[:.-]?\s*", re.I)
STOP_LINE = re.compile(r"packed for|sell by|net\s*wt|lot\b|www\.|\.com\b|©|seed count|germination\s*(?:rate|test)|tested|\b\d{11,13}\b", re.I)
HOW_WORDS = re.compile(r"\b(sow|plant(?:ing)?|transplant|thin|harden|start (?:seeds )?indoors|keep (?:the )?soil|water|cover|"
                       r"set out|space|germinat|seedlings|frost|mulch|stake|pinch|harvest)\b", re.I)


def extract_instructions(lines):
    """The packet's sowing directions as one paragraph: the block under a 'Sowing'/'How to grow' heading,
    or else the sentences that give directions."""
    def tidy(parts):
        t = " ".join(parts)
        t = re.sub(r"(\w)- (\w)", r"\1\2", t)          # re-join words hyphenated across lines
        t = re.sub(r"\s+", " ", t).strip()
        return t[:900]

    for i, l in enumerate(lines):
        if HEADINGS.match(l):
            block = [HEADINGS.sub("", l).strip()]
            for nxt in lines[i + 1:i + 16]:
                if STOP_LINE.search(nxt) or (HEADINGS.match(nxt) and len(block) > 2):
                    break
                block.append(nxt)
            text = tidy([b for b in block if b])
            if len(text) > 25:
                return text
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(l for l in lines if not STOP_LINE.search(l)))
    picked = [x for x in sentences if HOW_WORDS.search(x) and 20 < len(x) < 300 and not re.match(r"^(days|seed depth|plant spacing|sun)\b", x, re.I)]
    return tidy(picked[:5]) if picked else ""


def parse_packet_text(texts):
    text = "\n".join(t for t in texts if t)
    text = text.replace("|", " ").replace("¼", " 1/4").replace("½", " 1/2").replace("⅛", " 1/8").replace("¾", " 3/4")
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if len(l) > 1]
    joined = "\n".join(lines)
    seed, stated = {}, []

    # Plant and variety: the line naming the crop, preferring short lines (titles) over sentences.
    crop_lines = [l for l in lines if seedsites.match_crop(l)]
    crop_lines.sort(key=lambda l: (len(l) > 40, len(l)))
    crop = seedsites.match_crop(crop_lines[0]) if crop_lines else seedsites.match_crop(joined)
    if crop:
        seed["name"], seed["category"] = crop[0], crop[1]
        stated.append("name")
        if crop_lines and len(crop_lines[0]) <= 40:
            _, variety = seedsites.split_title(crop_lines[0], crop)
            variety = re.sub(r"[^A-Za-z0-9' &.-]", " ", variety)
            variety = re.sub(r"\s+", " ", variety).strip(" -.&'\"‘’“”")
            if 2 < len(variety) <= 30 and not re.search(r"\b(seeds?|packet|days?|net|wt|organic|heirloom|hybrid)\b", variety, re.I):
                seed["variety"] = variety.title() if variety.isupper() else variety
                stated.append("variety")
    # Variety on its own line next to a bare plant name ("ZINNIA" / "State Fair Mix").
    if crop and "variety" not in seed and crop_lines:
        bare = crop_lines[0]
        if not re.sub(r"(?i)\b(seeds?|" + re.escape(crop[0]) + r"(e?s)?)\b", "", bare).strip(" -:'\"‘’"):
            i = lines.index(bare)
            for cand in (lines[i + 1:i + 2] + lines[max(0, i - 1):i]):
                c = cand.strip(" '\"‘’“”")
                if 3 <= len(c) <= 30 and not re.search(r"\d", c) and not seedsites.match_crop(c) and \
                        not any(b.lower() in c.lower() for b in BRANDS) and \
                        not re.search(r"\b(seeds?|packet|organic|heirloom|hybrid|days?|annual|perennial|net|wt)\b", c, re.I):
                    seed["variety"] = c.title() if c.isupper() else c
                    stated.append("variety")
                    break
    for b in BRANDS:
        if re.search(r"\b" + re.escape(b).replace("\\ ", r"\s*") + r"\b", joined, re.I):
            seed["brand"] = b.replace("Ferry Morse", "Ferry-Morse")
            stated.append("brand")
            break
    m = re.search(r"(?:packed\s+for|sell\s+by|for\s+sale\s+in|season)\D{0,12}((?:19|20)\d\d)", joined, re.I) or \
        re.search(r"\b(?:0?[1-9]|1[0-2])/((?:20)\d\d)\b", joined)
    if m:
        seed["year"] = int(m.group(1))
        stated.append("year")

    # Labeled values ("Days to Maturity: 80"), then packet wording ("Harvest in 62 days").
    limits = {"dtm": (10, 400), "germDays": (1, 60), "depthIn": (0, 6), "spacingIn": (0.5, 120)}
    for k, (lo, hi) in limits.items():
        def ok(c, lo=lo, hi=hi):
            r = seedsites.first_range(c)
            return "%" not in c and bool(r and r[0] is not None and lo <= r[0] <= hi)
        v = seedsites.find_label(lines, seedsites.LABELS[k], True, ok)
        r = seedsites.first_range(v) if v else None
        if r and r[0] is not None:
            val = r[0] * 12 if k == "spacingIn" and re.search(r"\b(ft|feet|foot)\b|'", v) else r[0]
            seed[k] = seedsites.clean_num(val)
            stated.append(k)
    v = seedsites.find_label(lines, seedsites.LABELS["sun"], False, lambda c: bool(seedsites.SUN_WORDS.search(c)))
    if v:
        seed["sun"] = seedsites.SUN_WORDS.search(v).group(1).capitalize()
        stated.append("sun")
    for k, pats in PACKET_PATTERNS.items():
        if k in seed:
            continue
        for p in pats:
            m = re.search(p, joined, re.I)
            if m:
                v = seedsites.to_num(m.group(1).strip())
                lo, hi = limits[k]
                if v is not None and lo <= v <= hi:
                    seed[k] = seedsites.clean_num(v)
                    stated.append(k)
                    break
    if "sun" not in seed:
        m = seedsites.SUN_WORDS.search(joined)
        if m:
            seed["sun"] = m.group(1).capitalize()
            stated.append("sun")

    # Timing: typical for the crop, overridden by anything the packet states.
    if crop:
        seed["indoorWeeks"], seed["transplantWeeks"], seed["directWeeks"] = crop[2], crop[3], crop[4]
    t = seedsites.parse_timing(joined)
    for k in TIMING:
        if k in t:
            seed[k] = t[k]
            stated.append(k)
    if "_indoorBeforeTransplant" in t and "indoorWeeks" not in t:
        tw = seed.get("transplantWeeks") if seed.get("transplantWeeks") is not None else 1
        seed["indoorWeeks"], seed["transplantWeeks"] = t["_indoorBeforeTransplant"] - tw, tw
        stated += ["indoorWeeks", "transplantWeeks"]
    if t["_noDirect"]:
        seed["method"], seed["directWeeks"] = "indoor", None
    if "directWeeks" not in t and not seed.get("indoorWeeks") and (t["_afterFrost"] or t["_soilWorked"]) and seed.get("directWeeks") is None:
        seed["directWeeks"] = 1 if t["_afterFrost"] else -4
        stated.append("directWeeks")
    # Tesseract reads each photo twice (two layouts); use the first pass's lines for the instructions.
    first = [re.sub(r"\s+", " ", l).strip() for l in (texts[0] if texts else "").splitlines()]
    instr = extract_instructions([l for l in first if len(l) > 1] or lines)
    if instr:
        seed["instructions"] = instr
        stated.append("instructions")
    return {"engine": "Tesseract text recognition", "seed": seed, "stated": sorted(set(stated)), "text": joined[:4000]}


def read_ocr(images_b64):
    if not shutil.which("tesseract"):
        raise RuntimeError("Tesseract is not installed. Run: sudo apt install tesseract-ocr")
    texts = []
    with tempfile.TemporaryDirectory() as d:
        for i, b64 in enumerate(images_b64):
            p = os.path.join(d, f"p{i}.jpg")
            with open(p, "wb") as f:
                f.write(base64.b64decode(b64))
            texts += ocr(p)
    return parse_packet_text(texts)


def read(images_b64):
    """images_b64: list of base64 JPEGs (front and/or back). Raises if no engine can run."""
    errors = []
    if VISION_MODEL:
        try:
            return read_vision(images_b64)
        except Exception as e:
            errors.append(f"vision model: {e}")
    try:
        out = read_ocr(images_b64)
        if errors:
            out["note"] = "; ".join(errors) + " — used Tesseract instead"
        return out
    except Exception as e:
        errors.append(str(e))
        raise RuntimeError("; ".join(errors))
