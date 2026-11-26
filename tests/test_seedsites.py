"""Tests for seedsites.py using sample pages modeled on each company's product page layout.

Run:  python3 -m unittest discover -s tests      (from the seed-sync folder)
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import seedsites  # noqa: E402

BAKER = """<html><head><meta property="og:title" content="Cherokee Purple Tomato Seeds">
<meta property="og:description" content="Cherokee Purple tomato seeds: pre-1890 heirloom, dusky purple-pink fruit, rich old-time flavor. 80 days, indeterminate, 25 seeds."></head>
<body><h1>Tomato Seeds, Cherokee Purple</h1><div>SKU #TP101 · 25 seeds</div>
<div class="plant-details"><div><span>Life Cycle</span><span>Annual</span></div>
<div><span>Sun Requirement</span><span>6–8 hr.</span></div><div><span>Days to Germinate</span><span>7–14</span></div>
<div><span>Days to Maturity</span><span>80</span></div><div><span>Planting Depth</span><span>⅛ in.</span></div>
<div><span>Plant Spacing</span><span>24 in.</span></div><div><span>Frost Hardy</span><span>No</span></div></div>
<h2>When to Grow</h2><p>Start seeds indoors 6-10 weeks before the last expected frost date. Transplant outdoors after the danger of frost has passed.</p>
<script>var x = "Days to Maturity 999";</script></body></html>"""

EDEN = """<html><head><meta property="og:title" content="Basil Seeds - Genovese"><meta name="description" content="Great for use in pesto and salads. Grow it everywhere."></head>
<body><h1>Basil Seeds - Genovese</h1><h2>Plant Details</h2><ul>
<li><strong>BOTANICAL NAME</strong>: Ocimum basilicum</li><li><strong>LIGHT REQUIREMENT</strong>: <span>Full Sun</span></li>
<li><strong>DAYS TO MATURITY</strong>: <span>60 to 90 days</span></li></ul>
<h2>Planting Information</h2><ul><li><strong>PLANTING SEASON</strong>: Spring</li>
<li><strong>SOWING METHOD</strong>: Direct Sow, Start Indoors</li><li><strong>PLANTING DEPTH</strong>: 1/4 inch</li>
<li><strong>PLANT SPACING</strong>: 10 inches</li><li><strong>GERMINATION RATE</strong>: 85%</li></ul></body></html>"""

BURPEE = """<html><head><meta property="og:title" content="Burpee's Big Boy Tomato | Burpee"></head><body>
<h1>Burpee's Big Boy Tomato Seeds &amp; Plant</h1><p>One of the greatest tomatoes of all time and still a best seller.</p>
<h2>Key Attributes</h2>
<div><div>Light Requirement</div><div>The amount of sunlight this product needs daily in order to perform well in the garden. Full sun means 6 hours of direct sun per day.</div><div>Full sun</div></div>
<div><div>Sow Method</div><div>This refers to whether the seed should be sown early indoors and transplanted or sown directly in the garden.</div><div>Indoor</div></div>
<div><div>Days to Maturity</div><div>78</div></div><div><div>Mature Spread</div><div>36 in</div></div></body></html>"""

VICTORY = """<html><head><meta property="og:title" content="Cherokee Purple Tomato - Victory Seed Company">
<meta property="og:description" content="80 days, indeterminate, large dark pink-purple fruit with complex flavor, ideal for slicing."></head><body>
<h1>Cherokee Purple Tomato</h1><p><em>Solanum lycopersicum</em></p><p>SKU: 3400251</p>
<p>80 days, indeterminate - 'Cherokee Purple' was one of the earliest heirloom varieties.</p>
<h3>Growing Instructions</h3><p>Sow seeds indoors (do not direct sow into the garden) <strong>6 to 8 weeks</strong> before your last expected frost date. Plant 1/4" deep. Harden off plants before planting outside.</p></body></html>"""

JOHNNYS = """<html><head><meta property="og:title" content="Winner - Basil Seed"></head><body>
<h1>Winner</h1><div>Basil Seed</div><h2>Quick Facts</h2><dl><dt>Days To Maturity</dt><dd>74 days</dd><dt>Life Cycle</dt><dd>Annual</dd></dl>
<h2>Growing Information</h2><p><b>DAYS TO GERMINATION:</b> 5-10 days at 65-70°F (18-21°C).</p>
<p><b>SOWING:</b> Direct seeding is recommended. Sow 1/4" deep, 2-3 seeds per inch, in rows 18" apart. For transplanting, sow indoors 6 weeks before setting out and keep at 70°F (21°C).</p>
<p><b>LIGHT PREFERENCE:</b> Sun.</p><p><b>PLANT SPACING:</b> 4-8".</p></body></html>"""


class ParseTests(unittest.TestCase):
    def p(self, raw, site, url="https://example.test/p"):
        return seedsites.parse_product(raw, url, site)

    def test_baker_creek(self):
        r = self.p(BAKER, "bakercreek")
        self.assertEqual((r["name"], r["variety"], r["category"]), ("Tomato", "Cherokee Purple", "vegetable"))
        self.assertEqual((r["dtm"], r["germDays"], r["depthIn"], r["spacingIn"]), (80, 7, 0.125, 24))
        self.assertEqual(r["sun"], "Full sun (6–8 hr.)")
        self.assertEqual(r["indoorWeeks"], 8)       # 6-10 weeks -> 8
        self.assertEqual(r["transplantWeeks"], 2)   # typical tomato timing kept
        self.assertEqual(r["brand"], "Baker Creek")
        self.assertTrue(r["instructions"].startswith("Start seeds indoors 6-10 weeks"), r.get("instructions"))

    def test_eden(self):
        r = self.p(EDEN, "eden")
        self.assertEqual((r["name"], r["variety"], r["category"]), ("Basil", "Genovese", "herb"))
        self.assertEqual((r["dtm"], r["depthIn"], r["spacingIn"], r["sun"]), (60, 0.25, 10, "Full sun"))
        self.assertNotEqual(r.get("germDays"), 85)  # "germination rate 85%" is not days

    def test_burpee(self):
        r = self.p(BURPEE, "burpee")
        self.assertEqual((r["name"], r["variety"]), ("Tomato", "Burpee's Big Boy"))
        self.assertEqual((r["dtm"], r["sun"], r["method"]), (78, "Full sun", "indoor"))
        self.assertNotIn("spacingIn", r)  # "Mature Spread" is plant width, not spacing

    def test_victory(self):
        r = self.p(VICTORY, "victory")
        self.assertEqual((r["name"], r["variety"]), ("Tomato", "Cherokee Purple"))
        self.assertEqual((r["dtm"], r["depthIn"], r["indoorWeeks"], r["method"], r["directWeeks"]), (80, 0.25, 7, "indoor", None))
        self.assertIn("Harden off plants", r["instructions"])

    def test_timing_direct_vs_indoor(self):
        t = seedsites.parse_timing("Direct sow outdoors 2 weeks before the last frost.")
        self.assertEqual(t.get("directWeeks"), -2)
        t = seedsites.parse_timing("Start indoors 4-6 weeks before last frost, or direct sow after frost.")
        self.assertEqual(t.get("indoorWeeks"), 5)

    def test_johnnys(self):
        r = self.p(JOHNNYS, "johnnys")
        self.assertEqual(r["name"], "Basil")
        self.assertEqual((r["dtm"], r["germDays"], r["depthIn"], r["spacingIn"], r["sun"]), (74, 5, 0.25, 4, "Sun"))
        self.assertEqual(r["indoorWeeks"], 6)  # typical basil timing; "6 weeks before setting out" agrees

    def test_titles(self):
        crop = seedsites.match_crop("Basil")
        self.assertEqual(seedsites.split_title("Basil Seeds (Organic) - Genovese", crop), ("Basil", "Genovese"))
        self.assertEqual(seedsites.match_crop("Sweet Pea Seeds, Mammoth")[0], "Sweet pea")
        self.assertEqual(seedsites.match_crop("Bright Lights Chard")[0], "Swiss chard")

    def test_numbers(self):
        self.assertEqual(seedsites.to_num("1 1/2"), 1.5)
        self.assertEqual(seedsites.to_num("¼"), 0.25)
        self.assertEqual(seedsites.first_range("60 to 90 days"), (60, 90))


# ---------- end to end against fake seed-company sites

class FakeSites(BaseHTTPRequestHandler):
    PAGES = {"/products/tomato-cherokee-purple": BAKER, "/products/genovese_basil_seeds": EDEN,
             "/products/tomato-burpees-big-boy-hybrid": BURPEE, "/products/cherokee-purple-tomato": VICTORY,
             "/herbs/basil/genovese-basil/winner-basil-seed-4505.html": JOHNNYS}
    SEARCH = {"bakercreek": [("Tomato Seeds, Cherokee Purple", "/products/tomato-cherokee-purple"),
                             ("Tomato Seeds, Cherokee Green", "/products/tomato-cherokee-green")],
              "eden": [("Basil Seeds - Genovese", "/products/genovese_basil_seeds")],
              "burpee": [("Burpee's Big Boy Tomato", "/products/tomato-burpees-big-boy-hybrid")],
              "victory": [("Cherokee Purple Tomato", "/products/cherokee-purple-tomato")]}
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        site, path = self.path.split("/", 2)[1], "/" + self.path.split("/", 2)[2]
        FakeSites.hits.append(self.path)
        body, ctype = None, "text/html"
        if path.startswith("/search/suggest.json"):
            q = path.lower()
            prods = [{"title": t, "url": f"/{site}{u}?_pos=1"} for t, u in self.SEARCH.get(site, [])
                     if any(w in q for w in seedsites.tokens(t))]
            body, ctype = json.dumps({"resources": {"results": {"products": prods}}}), "application/json"
        elif path == "/sitemap_index.xml":
            body = f"<sitemapindex><sitemap><loc>http://{self.headers['Host']}/johnnys/sitemap_0-product.xml</loc></sitemap></sitemapindex>"
        elif path == "/sitemap_0-product.xml":
            host = self.headers["Host"]
            body = "<urlset>" + "".join(f"<url><loc>http://{host}/johnnys{u}</loc></url>" for u in [
                "/herbs/basil/genovese-basil/winner-basil-seed-4505.html",
                "/vegetables/tomatoes/cherokee-purple-tomato-seed-2817.html"]) + "</urlset>"
        elif path in self.PAGES and (site == "johnnys") == path.endswith(".html"):
            body = self.PAGES[path]
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class LookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeSites)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        port = cls.srv.server_address[1]
        cls.saved = {k: dict(v) for k, v in seedsites.SITES.items()}
        for k in seedsites.SITES:
            seedsites.SITES[k]["base"] = f"http://127.0.0.1:{port}/{k}"
        cls.cache = {}
        f = seedsites.Fetcher(lambda u, t: cls.cache.get(u), lambda u, b: cls.cache.__setitem__(u, b), delay=0)
        cls.sites = seedsites.SeedSites(f)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for k, v in cls.saved.items():
            seedsites.SITES[k].update(v)

    def test_barcode_title_goes_to_brand_site(self):
        FakeSites.hits.clear()
        out = self.sites.lookup("Baker Creek Heirloom Seeds Cherokee Purple Tomato 25 Seeds", brand="Baker Creek Heirloom Seed Co.")
        self.assertEqual(out["result"]["variety"], "Cherokee Purple")
        self.assertEqual(out["result"]["brand"], "Baker Creek")
        self.assertTrue(all(h.startswith("/bakercreek/") for h in FakeSites.hits))  # only the brand's own site

    def test_typed_name_searches_all_sites(self):
        out = self.sites.lookup("cherokee purple tomato")
        sites = {c["site"] for c in out["candidates"]}
        self.assertTrue({"bakercreek", "victory", "johnnys"} <= sites)
        self.assertEqual(out["result"]["name"], "Tomato")
        self.assertNotIn("cherokee-green", " ".join(c["url"] for c in out["candidates"][:3]))

    def test_specific_site_and_johnnys_sitemap(self):
        out = self.sites.lookup("winner basil", site="johnnys")
        self.assertEqual(out["result"]["dtm"], 74)
        self.assertIn("4505", out["result"]["sourceUrl"])

    def test_cache_prevents_repeat_requests(self):
        self.sites.lookup("genovese basil", site="eden")
        FakeSites.hits.clear()
        self.sites.lookup("genovese basil", site="eden")
        self.assertEqual(FakeSites.hits, [])

    def test_no_match(self):
        out = self.sites.lookup("garden gloves", site="burpee")
        self.assertIsNone(out["result"])


if __name__ == "__main__":
    unittest.main()
