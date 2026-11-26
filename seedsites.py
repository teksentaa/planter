"""Looks up growing details on seed company websites, so no AI service is needed.

Supported companies (keys used by the app):
  bakercreek  rareseeds.com      Shopify search
  eden        edenbrothers.com   Shopify search
  burpee      burpee.com         Shopify search
  victory     victoryseeds.com   Shopify search
  johnnys     johnnyseeds.com    weekly copy of the site's product sitemap (its search is off-limits to tools)

Every request respects the sites' robots.txt rules, waits between requests to the same site,
and caches pages for 30 days, so normal use is one or two page loads per new packet.
"""
import concurrent.futures
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = "Mozilla/5.0 (compatible; SeedPlanner/1.0; personal seed catalog)"
PAGE_TTL = 30 * 86400
SITEMAP_TTL = 7 * 86400

SITES = {
    "bakercreek": {"name": "Baker Creek", "base": "https://www.rareseeds.com", "kind": "shopify",
                   "brands": ["baker creek", "rareseeds", "rare seeds"]},
    "eden": {"name": "Eden Brothers", "base": "https://www.edenbrothers.com", "kind": "shopify",
             "brands": ["eden brothers", "edenbrothers"]},
    "burpee": {"name": "Burpee", "base": "https://www.burpee.com", "kind": "shopify", "brands": ["burpee"]},
    "victory": {"name": "Victory Seeds", "base": "https://victoryseeds.com", "kind": "shopify",
                "brands": ["victory seed", "victoryseeds"]},
    "johnnys": {"name": "Johnny's", "base": "https://www.johnnyseeds.com", "kind": "sitemap",
                "brands": ["johnny", "johnnyseeds"]},
}

# Typical timing in weeks relative to the last spring frost (negative = before).
# [name, category, indoorWeeksBefore, transplantWeeks, directSowWeeks]
CROPS = [
    ("Tomato", "vegetable", 7, 2, None), ("Pepper", "vegetable", 9, 2, None), ("Eggplant", "vegetable", 9, 3, None),
    ("Broccoli", "vegetable", 7, -2, None), ("Cabbage", "vegetable", 7, -3, None), ("Cauliflower", "vegetable", 6, -1, None),
    ("Kale", "vegetable", 6, -3, -4), ("Lettuce", "vegetable", 6, -2, -4), ("Spinach", "vegetable", None, None, -6),
    ("Pea", "vegetable", None, None, -6), ("Radish", "vegetable", None, None, -4), ("Carrot", "vegetable", None, None, -3),
    ("Beet", "vegetable", None, None, -2), ("Swiss chard", "vegetable", None, None, -2), ("Onion", "vegetable", 10, -4, None),
    ("Bean", "vegetable", None, None, 1), ("Cucumber", "vegetable", 3, 2, 2), ("Zucchini", "vegetable", None, None, 2),
    ("Squash", "vegetable", None, None, 2), ("Pumpkin", "vegetable", None, None, 2), ("Melon", "fruit", 3, 2, None),
    ("Watermelon", "fruit", 3, 2, None), ("Corn", "vegetable", None, None, 1), ("Basil", "herb", 6, 2, None),
    ("Cilantro", "herb", None, None, -2), ("Parsley", "herb", 9, -2, None), ("Dill", "herb", None, None, 0),
    ("Marigold", "flower", 6, 1, None), ("Zinnia", "flower", 4, 1, 1), ("Sunflower", "flower", None, None, 1),
    ("Cosmos", "flower", None, None, 1), ("Nasturtium", "flower", None, None, 1), ("Sweet pea", "flower", None, None, -6),
    ("Petunia", "flower", 10, 2, None), ("Snapdragon", "flower", 8, -2, None), ("Calendula", "flower", None, None, -2),
    ("Morning glory", "flower", None, None, 1), ("Tomatillo", "vegetable", 7, 2, None), ("Okra", "vegetable", 4, 3, 2),
    ("Leek", "vegetable", 10, -2, None), ("Arugula", "vegetable", None, None, -4), ("Collard", "vegetable", 6, -2, -3),
    ("Bok choy", "vegetable", 5, -2, -3), ("Turnip", "vegetable", None, None, -3), ("Kohlrabi", "vegetable", 6, -2, -3),
    ("Brussels sprouts", "vegetable", 7, -2, None), ("Celery", "vegetable", 10, 2, None), ("Chive", "herb", 8, -2, -2),
    ("Oregano", "herb", 8, 1, None), ("Thyme", "herb", 8, 1, None), ("Sage", "herb", 8, 1, None), ("Lavender", "herb", 10, 2, None),
    ("Chamomile", "herb", 6, -1, -2), ("Fennel", "herb", 5, 0, 0), ("Cowpea", "vegetable", None, None, 2),
    ("Poppy", "flower", None, None, -6), ("Celosia", "flower", 6, 2, None), ("Aster", "flower", 6, 0, None),
    ("Bachelor button", "flower", None, None, -4), ("Larkspur", "flower", None, None, -6), ("Salvia", "flower", 8, 1, None),
    ("Strawflower", "flower", 6, 1, None), ("Gomphrena", "flower", 6, 2, None), ("Rudbeckia", "flower", 8, 0, None),
    ("Echinacea", "flower", 10, 0, None), ("Alyssum", "flower", 6, 0, -1), ("Pansy", "flower", 10, -4, None),
    ("Hollyhock", "flower", None, None, 0), ("Cleome", "flower", 6, 1, None), ("Statice", "flower", 8, 0, None),
]
ALIASES = {"chard": "Swiss chard", "courgette": "Zucchini", "green bean": "Bean", "pole bean": "Bean", "bush bean": "Bean",
           "snap pea": "Pea", "snow pea": "Pea", "cantaloupe": "Melon", "muskmelon": "Melon", "sweet corn": "Corn",
           "coriander": "Cilantro", "collards": "Collard", "pak choi": "Bok choy", "black eyed susan": "Rudbeckia",
           "coneflower": "Echinacea", "cornflower": "Bachelor button", "bachelor's button": "Bachelor button",
           "viola": "Pansy", "pepper": "Pepper", "chili": "Pepper", "chile": "Pepper", "southern pea": "Cowpea"}
CROP_BY_NAME = {c[0].lower(): c for c in CROPS}


def match_crop(text):
    t = " " + re.sub(r"[^a-z' ]", " ", (text or "").lower()) + " "
    best = None
    for c in CROPS:
        n = c[0].lower()
        if re.search(r"\b" + re.escape(n) + r"(e?s)?\b", t) and (best is None or len(n) > len(best[0])):
            best = c
    for alias, name in ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"s?\b", t) and (best is None or len(alias) > len(best[0])):
            best = CROP_BY_NAME[name.lower()]
    return best


# ---------------------------------------------------------------- fetching

class Fetcher:
    """Polite HTTP client: one request at a time per site, a pause between them, and a cache."""

    def __init__(self, cache_get, cache_put, delay=1.5, timeout=15):
        self.cache_get, self.cache_put = cache_get, cache_put
        self.delay, self.timeout = delay, timeout
        self._locks, self._last, self._guard = {}, {}, threading.Lock()

    def get(self, url, ttl=PAGE_TTL, accept="text/html,application/json"):
        hit = self.cache_get(url, ttl)
        if hit is not None:
            return hit
        host = urllib.parse.urlparse(url).netloc
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            wait = self.delay - (time.time() - self._last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read(4_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
            finally:
                self._last[host] = time.time()
        self.cache_put(url, body)
        return body


# ---------------------------------------------------------------- page text

BLOCK = {"p", "div", "li", "tr", "td", "th", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "br", "section",
         "article", "ul", "ol", "table", "summary", "details", "label", "strong", "b", "span"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.cur, self.skip, self.h1, self.in_h1, self.meta = [], [], 0, "", False, {}

    def flush(self):
        line = re.sub(r"\s+", " ", "".join(self.cur)).strip()
        if line:
            self.lines.append(line)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "template", "iframe"):
            self.skip += 1
        if tag == "meta":
            a = dict(attrs)
            k = a.get("property") or a.get("name")
            if k and a.get("content"):
                self.meta[k.lower()] = a["content"]
        if tag == "h1" and not self.h1:
            self.in_h1 = True
        if tag in BLOCK:
            self.flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "template", "iframe") and self.skip:
            self.skip -= 1
        if tag in BLOCK:
            self.flush()
        if tag == "h1" and self.in_h1:
            self.in_h1 = False

    def handle_data(self, data):
        if self.skip:
            return
        self.cur.append(data)
        if self.in_h1:
            self.h1 += data


def page_text(raw):
    p = _Text()
    p.feed(raw)
    p.flush()
    # Merge "Label:" lines with the value that follows on the next line
    return p.lines, re.sub(r"\s+", " ", p.h1).strip(), p.meta


# ---------------------------------------------------------------- value parsing

FRAC = {"⅛": .125, "¼": .25, "⅜": .375, "½": .5, "⅝": .625, "¾": .75, "⅞": .875}
DASH = re.compile(r"\s*(?:-|–|—|to)\s*")


def to_num(s):
    """'1/4' -> .25, '1 1/2' -> 1.5, '⅛' -> .125, '0.5' -> .5"""
    s = s.strip()
    m = re.match(r"^(\d+)?\s*([⅛¼⅜½⅝¾⅞])$", s)
    if m:
        return (int(m.group(1)) if m.group(1) else 0) + FRAC[m.group(2)]
    m = re.match(r"^(?:(\d+)\s+)?(\d+)/(\d+)$", s)
    if m:
        return (int(m.group(1)) if m.group(1) else 0) + int(m.group(2)) / int(m.group(3))
    try:
        return float(s)
    except ValueError:
        return None


NUMTOK = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d*[⅛¼⅜½⅝¾⅞]|\d+(?:\.\d+)?)"


def first_range(s):
    m = re.search(rf"({NUMTOK})(?:\s*(?:-|–|—|to)\s*({NUMTOK}))?", s)
    if not m:
        return None
    a = to_num(m.group(1))
    b = to_num(m.group(2)) if m.group(2) else None
    return (a, b) if a is not None else None


def clean_num(v):
    if v is None:
        return None
    return int(v) if abs(v - round(v)) < 1e-9 else round(v, 3)


LABELS = {
    "dtm": ["days to maturity", "days to harvest", "days to bloom", "maturity"],
    "germDays": ["days to germinate", "days to germination", "days to emerge", "days to emergence", "germination"],
    "depthIn": ["planting depth", "seed depth", "sowing depth", "sow depth", "depth"],
    "spacingIn": ["plant spacing", "spacing", "space plants"],
    "sun": ["sun requirements", "sun requirement", "light requirement", "light preference", "sun exposure", "exposure", "sun"],
    "method": ["best sowing method", "sowing method", "sow method"],
}


ALL_LABELS = {l for ls in LABELS.values() for l in ls}


def find_label(lines, labels, want_digit, check=None):
    for i, line in enumerate(lines):
        low = line.lower()
        for lab in labels:
            if low.startswith(lab) and (len(low) == len(lab) or not low[len(lab)].isalpha()):
                rest = line[len(lab):].lstrip(" :–-*").strip()
                cands = [rest] + lines[i + 1:i + 4]
                for c in cands:
                    if not c or len(c) > 60:
                        continue
                    if want_digit and not re.search(r"\d|[⅛¼⅜½⅝¾⅞]", c):
                        continue
                    if not want_digit and not re.search(r"[a-z]", c.lower()):
                        continue
                    cl = c.lower().strip(" :*")
                    if c is not rest and (c.rstrip().endswith(":") or (cl in ALL_LABELS and cl != "sun")):
                        break  # ran into the next label
                    if check and not check(c):
                        continue
                    return c
    return None


SUN_WORDS = re.compile(r"\b(full sun|part(?:ial)? sun|part(?:ial)? shade|full shade|shade|sun)\b", re.I)

TIMING = {
    "before_last": re.compile(
        r"(\d+)(?:\s*(?:-|–|—|to)\s*(\d+))?\s*weeks?\s+(?:prior to|before)\s+(?:the\s+|your\s+)?"
        r"(?:average\s+|expected\s+|anticipated\s+|final\s+)*(?:date\s+of\s+(?:the\s+|your\s+)?)?last\s+(?:spring\s+|expected\s+|average\s+)*frost", re.I),
    "after_last": re.compile(
        r"(\d+)(?:\s*(?:-|–|—|to)\s*(\d+))?\s*weeks?\s+after\s+(?:the\s+|your\s+)?(?:average\s+|expected\s+)*last\s+(?:spring\s+)?frost", re.I),
    "before_transplant": re.compile(
        r"(\d+)(?:\s*(?:-|–|—|to)\s*(\d+))?\s*weeks?\s+(?:before|prior to)\s+(?:setting (?:them |plants )?out|transplant\w*|planting out)", re.I),
    "after_frost": re.compile(r"after\s+(?:all\s+)?(?:the\s+)?(?:danger|risk|chance)\s+of\s+frost|after\s+(?:the\s+)?last\s+frost|frost danger has passed|once frost has passed|after frost", re.I),
    "soil_worked": re.compile(r"as soon as (?:the\s+)?(?:soil|ground) can be worked|early spring", re.I),
    "no_direct": re.compile(r"do not direct sow|not recommended for direct sow", re.I),
}


def mid(a, b):
    return int(round((a + b) / 2)) if b else int(a)


def context(text, start, width=90):
    return text[max(0, start - width):start].lower()


def parse_timing(text):
    out = {}
    for m in TIMING["before_last"].finditer(text):
        n = mid(int(m.group(1)), int(m.group(2)) if m.group(2) else None)
        ctx = re.sub(r"(?:do not|don't|not recommended (?:to|for))\s+direct\s*sow[^.;)]*", " ", context(text, m.start()))
        i_in = ctx.rfind("indoor")
        i_out = max(ctx.rfind(w) for w in ("direct", "outdoor", "outside", "in the garden", "in the ground"))
        if i_out > i_in:
            out.setdefault("directWeeks", -n)
        else:
            out.setdefault("indoorWeeks", n)
    for m in TIMING["after_last"].finditer(text):
        n = mid(int(m.group(1)), int(m.group(2)) if m.group(2) else None)
        ctx = context(text, m.start())
        if re.search(r"transplant|set (?:plants |them )?out|plant out|move", ctx):
            out.setdefault("transplantWeeks", n)
        else:
            out.setdefault("directWeeks", n)
    for m in TIMING["before_transplant"].finditer(text):
        n = mid(int(m.group(1)), int(m.group(2)) if m.group(2) else None)
        out.setdefault("_indoorBeforeTransplant", n)
    low = text.lower()
    out["_afterFrost"] = bool(TIMING["after_frost"].search(text))
    out["_soilWorked"] = bool(TIMING["soil_worked"].search(text))
    out["_noDirect"] = bool(TIMING["no_direct"].search(text))
    out["_mentionsIndoor"] = bool(re.search(r"(?:start|sow)\w*\s+(?:seeds?\s+)?indoors", low))
    return out


def split_title(title, crop):
    """'Tomato Seeds, Cherokee Purple' / 'Basil Seeds (Organic) - Genovese' / 'Burpee's Big Boy Tomato' -> (name, variety)."""
    t = re.sub(r"\s+", " ", title).strip()
    m = re.match(r"^(.+?)\s+seeds?(?:\s*\([^)]*\))?\s*(?:,|-|–|:)\s*(.+)$", t, re.I)
    if m:
        name, variety = m.group(1).strip(), m.group(2).strip()
    else:
        t2 = re.sub(r"\b(seeds?|plants?|packet|organic|pelleted)\b", "", t, flags=re.I)
        t2 = re.sub(r"\s*\([^)]*\)", "", t2)
        t2 = re.sub(r"\s+-\s+", " ", t2)
        t2 = re.sub(r"(?:\s+(?:&|and))+\s*$", "", re.sub(r"\s+", " ", t2)).strip(" -,&")
        name = crop[0] if crop else ""
        variety = t2
        if crop:
            for word in [crop[0]] + [a for a, n in ALIASES.items() if n == crop[0]]:
                variety = re.sub(r"\b" + re.escape(word) + r"(e?s)?\b", "", variety, flags=re.I)
            variety = re.sub(r"\s+", " ", variety).strip(" -,")
        if not name:
            name, variety = t2, ""
    if crop and name.lower() != crop[0].lower() and match_crop(name) == crop:
        name = crop[0]
    return name, variety


def parse_product(raw, url, site_key):
    lines, h1, meta = page_text(raw)
    text = "\n".join(lines)
    og = re.split(r"\s+[|–]\s+|\s+-\s+(?=[^-]*(?:company|seeds?\b(?!.*-)|co\.?)$)", html.unescape(meta.get("og:title", "")).strip())[0]
    desc = html.unescape(meta.get("og:description") or meta.get("description") or "")
    title = h1 or og
    if h1 and not match_crop(h1) and match_crop(og):
        title = html.unescape(meta.get("og:title", "")).split("|")[0].strip()  # e.g. Johnny's: h1 "Winner", og "Winner - Basil Seed"
    crop = match_crop(title) or match_crop(desc)
    name, variety = split_title(title, crop)
    res = {"name": name, "variety": variety, "brand": SITES[site_key]["name"], "sourceUrl": url, "sourceTitle": title,
           "category": crop[1] if crop else ("flower" if re.search(r"\bflower", text[:3000], re.I) else "vegetable")}

    def in_range(lo, hi):
        def ok(c):
            if "%" in c:
                return False
            r = first_range(c)
            return bool(r and r[0] is not None and lo <= r[0] <= hi)
        return ok

    v = find_label(lines, LABELS["dtm"], True, in_range(10, 400))
    r = first_range(v) if v else None
    if not r:
        m = re.search(r"\b(\d{2,3})\s*(?:(?:-|–|to)\s*(\d{2,3})\s*)?days\b", desc + "\n" + text)
        r = (float(m.group(1)), None) if m else None
    if r:
        res["dtm"] = clean_num(r[0])
    v = find_label(lines, LABELS["germDays"], True, in_range(1, 60))
    r = first_range(v) if v else None
    if not r:
        m = re.search(r"(?:germinat\w*|sprout\w*|emerge\w*)\D{0,30}?(\d+)\s*(?:(?:-|–|to)\s*(\d+)\s*)?days", text, re.I)
        r = (float(m.group(1)), None) if m else None
    if r:
        res["germDays"] = clean_num(r[0])
    v = find_label(lines, LABELS["depthIn"], True, in_range(0, 6))
    r = first_range(v) if v else None
    if not r:
        m = re.search(rf"(?:plant|sow)\w*\s+(?:seeds?\s+)?(?:at\s+a\s+depth\s+of\s+)?({NUMTOK})\s*(?:\"|”|″|in\.?|inch(?:es)?)\s*deep", text, re.I)
        r = (to_num(m.group(1)), None) if m else None
    if r and r[0] is not None and r[0] <= 6:
        res["depthIn"] = clean_num(r[0])
    v = find_label(lines, LABELS["spacingIn"], True, in_range(0.5, 120))
    r = first_range(v) if v else None
    if r and r[0] is not None:
        val = r[0] * 12 if v and re.search(r"\b(ft|feet|foot|')", v) else r[0]
        res["spacingIn"] = clean_num(val)
    v = find_label(lines, LABELS["sun"], False, lambda c: bool(SUN_WORDS.search(c) or re.search(r"\d\s*(?:hr|hour)", c, re.I)))
    if v:
        m = SUN_WORDS.search(v)
        hrs = re.search(r"(\d+\s*(?:-|–|to)\s*\d+|\d+)\s*(?:hr|hour)", v)
        res["sun"] = (m.group(1).capitalize() if m else "Full sun") + (f" ({hrs.group(0)}.)" if hrs and not m else "")
    method = (find_label(lines, LABELS["method"], False, lambda c: bool(re.search(r"indoor|direct|outdoor|transplant", c, re.I))) or "").lower()

    # Timing: start from typical values for the crop, then use what the page says.
    if crop:
        res["indoorWeeks"], res["transplantWeeks"], res["directWeeks"] = crop[2], crop[3], crop[4]
    else:
        res["indoorWeeks"] = res["transplantWeeks"] = res["directWeeks"] = None
    t = parse_timing(text)
    if "indoorWeeks" in t:
        res["indoorWeeks"] = t["indoorWeeks"]
    if "directWeeks" in t:
        res["directWeeks"] = t["directWeeks"]
    if "transplantWeeks" in t:
        res["transplantWeeks"] = t["transplantWeeks"]
    if "_indoorBeforeTransplant" in t and res.get("indoorWeeks") is None:
        tw = res["transplantWeeks"] if res["transplantWeeks"] is not None else 1
        res["indoorWeeks"] = t["_indoorBeforeTransplant"] - tw
        res["transplantWeeks"] = tw
    if res.get("indoorWeeks") is not None and res.get("transplantWeeks") is None:
        res["transplantWeeks"] = 1 if t["_afterFrost"] else 0
    if t["_noDirect"] or "indoor" in method and "direct" not in method:
        res["method"] = "indoor"
        if t["_noDirect"]:
            res["directWeeks"] = None
        if res.get("indoorWeeks") is None:
            res["indoorWeeks"], res["transplantWeeks"] = 6, 1
    elif "direct" in method and "indoor" not in method:
        res["method"] = "direct"
        if res.get("directWeeks") is None:
            res["directWeeks"] = 1 if t["_afterFrost"] else (-4 if t["_soilWorked"] else 0)
    if res.get("indoorWeeks") is None and res.get("directWeeks") is None and (t["_afterFrost"] or t["_soilWorked"]):
        res["directWeeks"] = 1 if t["_afterFrost"] else -4
    if desc:
        res["notes"] = re.split(r"(?<=[.!?])\s", desc.strip(), maxsplit=2)[0][:300]
    res["found"] = [k for k in ("dtm", "germDays", "depthIn", "spacingIn", "sun") if res.get(k) not in (None, "")]
    return res


# ---------------------------------------------------------------- searching

STOP = {"seed", "seeds", "packet", "pkt", "organic", "heirloom", "non", "gmo", "nongmo", "pack", "of", "the", "and", "for",
        "with", "garden", "vegetable", "flower", "herb", "plant", "plants", "variety", "hybrid", "f1", "open", "pollinated",
        "pelleted", "multi", "seeds,", "co", "company", "inc", "llc", "s"}


def tokens(s):
    s = re.sub(r"'s\b", "", (s or "").lower())
    return [w for w in re.findall(r"[a-z0-9]+", s) if w not in STOP and not w.isdigit()]


def clean_query(q, brand_words=()):
    q = re.sub(r"\b\d+\s*(?:seeds?|ct|count|pack|g|grams?|oz)\b", " ", q or "", flags=re.I)
    for b in brand_words:
        q = re.sub(re.escape(b), " ", q, flags=re.I)
    q = re.sub(r"[^\w' ]", " ", q)
    return " ".join(w for w in q.split() if w.lower() not in STOP)


def score(query_toks, title, crop=None):
    tt = set(tokens(title))
    if not tt or not query_toks:
        return 0
    qs = set(query_toks)
    hit = len(qs & tt)
    s = 0.7 * hit / len(qs) + 0.3 * hit / len(tt)
    if crop and not match_crop(title) == crop:
        s *= 0.5
    return round(s, 3)


class SeedSites:
    def __init__(self, fetcher):
        self.f = fetcher

    def site_for_brand(self, brand):
        b = (brand or "").lower()
        for k, s in SITES.items():
            if any(w in b for w in s["brands"]):
                return k
        return None

    # -- Shopify predictive search (allowed by these sites' robots.txt)
    def search_shopify(self, key, q):
        base = SITES[key]["base"]
        url = f"{base}/search/suggest.json?" + urllib.parse.urlencode(
            {"q": q, "resources[type]": "product", "resources[limit]": "8"})
        data = json.loads(self.f.get(url, ttl=PAGE_TTL, accept="application/json"))
        prods = ((data.get("resources") or {}).get("results") or {}).get("products") or data.get("products") or []
        out = []
        for p in prods:
            href = (p.get("url") or "").split("?")[0]
            if href:
                out.append({"site": key, "title": html.unescape(p.get("title", "")), "url": urllib.parse.urljoin(base, href)})
        return out

    # -- Johnny's: product URLs from the published sitemap, refreshed weekly
    def johnnys_index(self):
        base = SITES["johnnys"]["base"]
        idx = self.f.get(base + "/sitemap_index.xml", ttl=SITEMAP_TTL, accept="application/xml")
        maps = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", idx)
        prod_maps = [m for m in maps if "product" in m.lower()] or maps[:12]
        urls = []
        for m in prod_maps[:12]:
            try:
                xml = self.f.get(html.unescape(m), ttl=SITEMAP_TTL, accept="application/xml")
            except Exception:
                continue
            urls += [html.unescape(u) for u in re.findall(r"<loc>\s*([^<\s]+\.html)\s*</loc>", xml)]
        return urls

    def search_johnnys(self, q):
        qt = tokens(q)
        out = []
        for u in self.johnnys_index():
            slug = u.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            slug = re.sub(r"-[0-9a-z]*\d[0-9a-z]*$", "", slug)  # drop product id like -4505 or -3188p
            title = slug.replace("-", " ").title()
            if set(qt) & set(tokens(title)):
                out.append({"site": "johnnys", "title": title, "url": u})
        return out

    def search(self, key, q):
        if SITES[key]["kind"] == "shopify":
            return self.search_shopify(key, q)
        return self.search_johnnys(q)

    def lookup(self, query="", site=None, brand=""):
        """Returns {'result': parsed product or None, 'candidates': [...], 'errors': {...}}"""
        site = site if site in SITES else self.site_for_brand(brand)
        brand_words = [w for s in SITES.values() for w in s["brands"]] + ["burpee's", "johnny's"]
        q = clean_query(query, brand_words)
        crop = match_crop(q)
        qt = tokens(q)
        keys = [site] if site else list(SITES)
        cands, errors = [], {}

        def run(k):
            try:
                return self.search(k, q)
            except Exception as e:
                errors[k] = str(e)[:120]
                return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            for lst in ex.map(run, keys):
                cands += lst
        seen = set()
        ranked = []
        for c in cands:
            if c["url"] in seen:
                continue
            seen.add(c["url"])
            c["score"] = score(qt, c["title"], crop) + (0.05 if c["site"] == site else 0)
            c["siteName"] = SITES[c["site"]]["name"]
            ranked.append(c)
        ranked.sort(key=lambda c: -c["score"])
        ranked = [c for c in ranked if c["score"] >= 0.34][:8]
        result = None
        for c in ranked[:2]:  # read at most two product pages per lookup
            try:
                result = self.product(c["url"], c["site"])
                break
            except Exception as e:
                errors[c["site"]] = str(e)[:120]
        return {"query": q, "result": result, "candidates": ranked, "errors": errors}

    def product(self, url, site=None):
        host = lambda u: urllib.parse.urlparse(u).netloc.replace("www.", "")
        if site in SITES and host(url) != host(SITES[site]["base"]):
            raise ValueError("that page is not on " + SITES[site]["name"])
        if site not in SITES:
            host = urllib.parse.urlparse(url).netloc.replace("www.", "")
            site = next((k for k, s in SITES.items() if urllib.parse.urlparse(s["base"]).netloc.replace("www.", "") == host), None)
            if not site:
                raise ValueError("not a supported seed company")
        return parse_product(self.f.get(url), url, site)
