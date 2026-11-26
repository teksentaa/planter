"""Tests for packetreader.py.  Run:  python3 -m unittest discover -s tests"""
import base64
import io
import json
import os
import shutil
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import packetreader  # noqa: E402

BACK_TEXT = """BOTANICAL INTERESTS
Tomato 'Cherokee Purple' Organic
Heirloom. Dusky rose fruit with rich, sweet flavor.
Days to Emerge: 6-14 days
Seed Depth: 1/4"
Plant Spacing: 24-36"
Days to Maturity: 80
Sun: Full Sun
SOWING: Start seeds indoors 6-8 weeks before your
average last frost date. Transplant outdoors when all danger of frost has passed.
Packed for 2026 Net Wt. 0.1 g"""

LOOSE_TEXT = """Ferry-Morse
SUGAR SNAP
Pea
Sow seeds 1" deep, 2" apart as soon as the soil can be worked in early spring.
Seedlings appear in 7-14 days. Harvest in 62 days. Sell by 12/2026"""


class ParseTests(unittest.TestCase):
    def test_labeled_back(self):
        r = packetreader.parse_packet_text([BACK_TEXT])
        s = r["seed"]
        self.assertEqual((s["name"], s["variety"], s["brand"], s["year"]), ("Tomato", "Cherokee Purple", "Botanical Interests", 2026))
        self.assertEqual((s["dtm"], s["germDays"], s["depthIn"], s["spacingIn"], s["sun"]), (80, 6, 0.25, 24, "Full sun"))
        self.assertEqual(s["indoorWeeks"], 7)
        self.assertIn("indoorWeeks", r["stated"])
        self.assertNotIn("transplantWeeks", r["stated"])  # typical value, not printed as a number

    def test_sentences_only(self):
        r = packetreader.parse_packet_text([LOOSE_TEXT])
        s = r["seed"]
        self.assertEqual((s["name"], s["variety"], s["brand"], s["year"]), ("Pea", "Sugar Snap", "Ferry-Morse", 2026))
        self.assertEqual((s["depthIn"], s["spacingIn"], s["germDays"], s["dtm"]), (1, 2, 7, 62))
        self.assertEqual(s["directWeeks"], -6)  # typical pea timing; "as soon as soil can be worked" agrees

    def test_nothing_useful(self):
        r = packetreader.parse_packet_text(["Thank you for gardening with us"])
        self.assertNotIn("name", r["seed"])
        self.assertEqual(r["stated"], [])


class FakeOllama(BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.seen = body
        answer = {"name": "Zinnia", "variety": "State Fair Mix", "brand": "Burpee", "category": "flower", "sun": "Full sun",
                  "spacingIn": "12", "depthIn": 0.25, "germDays": None, "dtm": 75, "indoorWeeks": None,
                  "transplantWeeks": None, "directWeeks": 1, "year": 2026, "notes": "Great cut flowers."}
        data = json.dumps({"message": {"role": "assistant", "content": json.dumps(answer)}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class VisionTests(unittest.TestCase):
    def test_vision_model_used_when_configured(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        saved = packetreader.OLLAMA_URL, packetreader.VISION_MODEL
        packetreader.OLLAMA_URL, packetreader.VISION_MODEL = f"http://127.0.0.1:{srv.server_address[1]}", "qwen2.5vl:7b"
        try:
            r = packetreader.read([base64.b64encode(b"\xff\xd8fakejpeg").decode()])
        finally:
            packetreader.OLLAMA_URL, packetreader.VISION_MODEL = saved
            srv.shutdown()
        self.assertEqual(r["engine"], "vision model (qwen2.5vl:7b)")
        self.assertEqual((r["seed"]["variety"], r["seed"]["spacingIn"], r["seed"]["directWeeks"]), ("State Fair Mix", 12, 1))
        self.assertNotIn("germDays", r["stated"])
        self.assertEqual(FakeOllama.seen["model"], "qwen2.5vl:7b")
        self.assertEqual(len(FakeOllama.seen["messages"][0]["images"]), 1)

    def test_falls_back_to_tesseract_when_model_is_down(self):
        if not shutil.which("tesseract"):
            self.skipTest("tesseract not installed")
        saved = packetreader.OLLAMA_URL, packetreader.VISION_MODEL
        packetreader.OLLAMA_URL, packetreader.VISION_MODEL = "http://127.0.0.1:9", "qwen2.5vl:7b"
        try:
            r = packetreader.read([_packet_jpeg()])
        finally:
            packetreader.OLLAMA_URL, packetreader.VISION_MODEL = saved
        self.assertEqual(r["engine"], "Tesseract text recognition")
        self.assertIn("vision model", r.get("note", ""))
        self.assertEqual(r["seed"].get("dtm"), 80)


def _packet_jpeg():
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1100, 900), (250, 247, 238))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(BACK_TEXT.splitlines()):
        d.text((40, 30 + i * 60), line, font=font, fill=(20, 20, 20))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


@unittest.skipUnless(shutil.which("tesseract"), "tesseract not installed")
class OcrTests(unittest.TestCase):
    def test_reads_printed_packet(self):
        r = packetreader.read_ocr([_packet_jpeg()])
        s = r["seed"]
        self.assertEqual((s.get("name"), s.get("variety"), s.get("dtm"), s.get("depthIn")), ("Tomato", "Cherokee Purple", 80, 0.25))


if __name__ == "__main__":
    unittest.main()
