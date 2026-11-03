#!/usr/bin/env python3
"""Qualify every generated HTML gzip stream and its compression settings."""
import ast
import gzip
import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import zopfli_compress
from webconfig_minify import strip_source


def arrays(header):
    return {name: bytes(int(value, 16) for value in re.findall(r"0x([0-9a-f]{2})", body))
            for name, body in re.findall(r"const uint8_t (\w+)\[\] PROGMEM = \{(.*?)\};",
                                         header, re.S)}


class WebConfigGzipTest(unittest.TestCase):
    def test_embedded_pages_round_trip_and_match_cache_keys(self):
        header = (ROOT / "src/helpers/esp32/WebConfigHtml.h").read_text()
        self.assertIn("gzip, 1000 iterations", header)
        self.assertNotIn("WEBCONFIG_HTML_BR", header)
        blobs = arrays(header)
        self.assertEqual(set(blobs), {"WEBCONFIG_HTML_GZ", "WEBCONFIG_HTML_LOADER",
                                      "WEBCONFIG_HTML_SUCCESS"})
        page = gzip.decompress(blobs["WEBCONFIG_HTML_GZ"])
        self.assertEqual(page, strip_source((ROOT / "webui/index.html").read_text()).encode())
        version = hashlib.sha256(blobs["WEBCONFIG_HTML_GZ"]).hexdigest()[:16]
        self.assertIn('WEBCONFIG_HTML_VERSION[] = "%s"' % version, header)
        self.assertIn(("/ui?v=" + version).encode(), gzip.decompress(blobs["WEBCONFIG_HTML_LOADER"]))
        self.assertEqual(gzip.decompress(blobs["WEBCONFIG_HTML_SUCCESS"]),
                         b"<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>")
        for name, blob in blobs.items():
            declared = re.search(name + r"_LEN = (\d+);", header)
            self.assertEqual(len(blob), int(declared[1]))
            self.assertEqual(blob[:3], b"\x1f\x8b\x08")
            self.assertEqual(blob[4:8], b"\0" * 4)
            self.assertEqual(blob[9], 255)

    def test_compact_uploader_and_wifi_form_are_precompressed(self):
        header = (ROOT / "src/helpers/esp32/StaticHtml.h").read_text()
        self.assertIn("gzip, 1000 iterations", header)
        blobs = arrays(header)
        for page in (ROOT / "webui/pages").glob("*.html"):
            expected = page.read_bytes()
            if page.name == "elegant_ota_compact.html":
                marker = b"/* MESH_SPARK_MD5 */"
                self.assertEqual(expected.count(marker), 1)
                expected = expected.replace(marker, (ROOT / "webui/vendor/spark-md5-3.0.2.min.js").read_bytes())
            self.assertEqual(gzip.decompress(blobs["MESH_HTML_" + page.stem.upper()]), expected)

    def test_loader_matches_legacy_android_browser_syntax(self):
        blobs = arrays((ROOT / "src/helpers/esp32/WebConfigHtml.h").read_text())
        loader = gzip.decompress(blobs["WEBCONFIG_HTML_LOADER"]).decode()
        script = loader.split("<script>", 1)[1].split("</script>", 1)[0]
        self.assertNotIn("=>", script)
        self.assertNotRegex(script, r"\b(?:async|await|let|const)\b")

    def test_gzip_binding_uses_1000_iterations_without_changing_raw_ota_default(self):
        with mock.patch.object(zopfli_compress._gzip, "compress",
                               return_value=gzip.compress(b"page", mtime=0)) as compress:
            encoded = zopfli_compress.gzip_compress(b"page")
        self.assertEqual(compress.call_args.kwargs["numiterations"], 1000)
        self.assertEqual(gzip.decompress(encoded), b"page")
        self.assertEqual(zopfli_compress.ITERATIONS, 15)

    def test_cache_validates_contents_and_recovers_from_corruption(self):
        # Load the production cache helper without invoking its build entry point.
        source = ROOT / "scripts/generate_webconfig_html.py"
        tree = ast.parse(source.read_text(), filename=str(source))
        self.assertIsInstance(tree.body[-1], ast.Expr)
        self.assertEqual(tree.body[-1].value.func.id, "main")
        tree.body.pop()
        namespace = {}
        exec(compile(tree, str(source), "exec"), namespace)
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                compress = namespace["gzip_compress"]
                with mock.patch.dict(namespace, gzip_compress=mock.Mock(wraps=compress)):
                    first = namespace["compress_page"](b"<html>cache test</html>")
                    self.assertEqual(first, namespace["compress_page"](b"<html>cache test</html>"))
                    self.assertEqual(namespace["gzip_compress"].call_count, 1)
                    cache = next(Path(".pio/gzip-html").glob("*.gz"))
                    cache.write_bytes(first[:-3])
                    self.assertEqual(first, namespace["compress_page"](b"<html>cache test</html>"))
                    self.assertEqual(namespace["gzip_compress"].call_count, 2)
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()
