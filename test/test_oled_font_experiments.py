"""nRF52 font storage choices preserve renderer behavior and stay in scope."""
import hashlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures" / "oled_font_experiments"


class OledFontExperimentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.decoder = cls.directory / "tinf.o"
        subprocess.run([
            "cc", "-Os", "-DMESHCORE_TINF_IMPLEMENTATION=1", "-c",
            str(ROOT / "src/helpers/ota/tinf/tinflate.c"), "-o", str(cls.decoder),
        ], check=True)
        cls.results = {}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_renderer(self, mode, flags=(), signed_char=False, sanitized=False, explicit=True):
        key = (mode, tuple(flags), signed_char, sanitized, explicit)
        if key in self.results:
            return self.results[key]
        executable = self.directory / f"font_{len(self.results)}"
        subprocess.run([
            "c++", "-std=c++11", "-O2", "-Wall", "-Wextra",
            "-Wno-implicit-fallthrough", "-DARDUINO=1",
            "-fsigned-char" if signed_char else "-funsigned-char",
            *( ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if sanitized else [] ),
            "-DOLEDDISPLAY_REDUCE_MEMORY=1",
            *([f"-DMESH_ARIAL_EXPERIMENT={mode}"] if explicit else []),
            f"-DEXPECT_MODE={mode if not flags else 0}",
            *(flags or ("-DNRF52_PLATFORM=1", "-DMESH_NRF52_FLASH_TRIM=1", "-DST7789=1")),
            f"-I{FIXTURES}", f"-I{ROOT / 'src'}",
            str(FIXTURES / "test.cpp"),
            str(ROOT / "src/helpers/ui/OLEDDisplay.cpp"),
            str(ROOT / "src/helpers/ui/OLEDDisplayFonts.cpp"),
            str(self.decoder), "-o", str(executable),
        ], check=True)
        symbols = subprocess.run(["nm", "-C", "--defined-only", str(executable)],
                                 text=True, capture_output=True, check=True).stdout
        if not flags and mode != 0:
            self.assertNotIn("ArialMT_Plain_10", symbols, "Unused initial Arial10 is still linked")
        result = subprocess.run([str(executable)], text=True, capture_output=True, check=True)
        self.results[key] = result.stdout.splitlines()
        return self.results[key]

    def test_all_glyphs_metrics_and_framebuffers_match(self):
        baseline = self.run_renderer(0)
        self.assertEqual(baseline[0], "initial 13")
        self.assertEqual([line.split()[0] for line in baseline[1:]], [
            "glyphs", "clipping", "utf8", "wrapping", "controls", "bitmaps", "logs", "custom"])
        self.assertTrue(baseline[1].startswith("glyphs 448 "))
        self.assertTrue(baseline[2].startswith("clipping 960 "))
        for mode in (1, 2, 3):
            with self.subTest(mode=mode):
                actual = self.run_renderer(mode)
                self.assertEqual(actual[0], "initial 19")
                self.assertEqual(actual[1:], baseline[1:])

    def test_experiment_guards_signed_char_latin1_indices(self):
        baseline = self.run_renderer(0)
        for mode in (1, 2, 3):
            with self.subTest(mode=mode):
                self.assertEqual(self.run_renderer(mode, signed_char=True)[1:], baseline[1:])

    def test_experimental_renderer_passes_memory_and_undefined_behavior_checks(self):
        baseline = self.run_renderer(0)
        for mode in (1, 2, 3):
            with self.subTest(mode=mode):
                actual = self.run_renderer(mode, signed_char=True, sanitized=True)
                self.assertEqual(actual[1:], baseline[1:])

    def test_tinf_is_enabled_only_for_effective_zopfli_font_or_existing_features(self):
        qualified = ("-DNRF52_PLATFORM=1", "-DMESH_NRF52_FLASH_TRIM=1", "-DST7789=1")
        cases = [(*qualified, f"-DMESH_ARIAL_EXPERIMENT={mode}") for mode in range(10)]
        expected = [mode in (3, 9) for mode in range(10)]
        cases.append(qualified)  # No override: production compressed Noto font.
        expected.append(True)
        cases.extend([
            (*qualified, "-DMESH_NRF52_FONT_MODE=0"),
            (*qualified, "-DMESH_NRF52_FONT_MODE=9"),
            (*qualified, "-DMESH_NRF52_FONT_MODE=9", "-DMESH_ARIAL_EXPERIMENT=0"),
        ])
        expected.extend((False, True, False))
        excluded = (
            ("-DNRF52_PLATFORM=1", "-DST7789=1"),
            ("-DNRF52_PLATFORM=1", "-DMESH_NRF52_FLASH_TRIM=0", "-DST7789=1"),
            ("-DMESH_NRF52_FLASH_TRIM=1", "-DST7789=1"),
            ("-DNRF52_PLATFORM=1", "-DMESH_NRF52_FLASH_TRIM=1"),
            (*qualified, "-DCOMPANION_RADIO_FULL=1"),
        )
        cases.extend((*flags, "-DMESH_ARIAL_EXPERIMENT=3") for flags in excluded)
        expected.extend(False for _ in excluded)
        cases.extend((*flags, "-DMESH_ARIAL_EXPERIMENT=9") for flags in excluded)
        expected.extend(False for _ in excluded)
        cases.extend(excluded)  # Production default remains off outside scope.
        expected.extend(False for _ in excluded)
        cases.extend((*flags, "-DMESH_NRF52_FONT_MODE=9") for flags in excluded)
        expected.extend(False for _ in excluded)
        cases.extend([
            ("-DENABLE_OTA=1",), ("-DOTA_TRANSPORT_DEFLATE_TEST=1",),
        ])
        expected.extend((True, True))
        # Reader requires an explicit feature flag on Full USB profiles.
        for platform in ("NRF52_PLATFORM", "ESP32_PLATFORM"):
            reader = (f"-D{platform}=1", "-DCOMPANION_RADIO_FULL=1", "-DENABLE_USB_INTERFACE=1")
            cases.extend([
                reader, (*reader, "-DCOMPANION_FEATURE_READER=0"),
                (*reader, "-DCOMPANION_FEATURE_READER=1"),
            ])
            expected.extend((False, False, True))
        for flags, decoder_enabled in zip(cases, expected):
            with self.subTest(flags=flags):
                output = self.directory / "guard.o"
                subprocess.run([
                    "cc", "-Os", *flags, "-c", str(ROOT / "src/helpers/ota/OtaTinf.c"),
                    "-o", str(output),
                ], check=True)
                symbols = subprocess.run(
                    ["nm", "-g", "--defined-only", str(output)],
                    text=True, capture_output=True, check=True).stdout
                self.assertEqual("tinf_uncompress_exact" in symbols, decoder_enabled)

    def test_similar_size_family_trials_match_raw_pixels_and_keep_full_coverage(self):
        families = []
        for mode in (4, 5, 6, 7, 8):
            with self.subTest(mode=mode):
                unsigned = self.run_renderer(mode)
                self.assertEqual(unsigned[0], "initial 19")
                self.assertTrue(unsigned[1].startswith("glyphs 448 "))
                self.assertTrue(unsigned[2].startswith("clipping 960 "))
                # The fixture compares every glyph against a separate
                # pixel-at-a-time reference and checks 189 drawable slots,
                # exact 19/28 line boxes and 12/17-pixel H cap heights.
                self.assertEqual(self.run_renderer(mode, signed_char=True), unsigned)
                self.assertEqual(self.run_renderer(mode, signed_char=True, sanitized=True), unsigned)
                families.append(unsigned[1])
        self.assertEqual(len(set(families)), 5, "family trials unexpectedly contain the same pixels")

    def test_production_compressed_noto_default_matches_raw_family_exactly(self):
        raw_noto = self.run_renderer(7)
        compressed = self.run_renderer(9)
        self.assertEqual(compressed, raw_noto)
        self.assertEqual(self.run_renderer(9, explicit=False), raw_noto)
        self.assertEqual(self.run_renderer(9, signed_char=True), raw_noto)
        self.assertEqual(self.run_renderer(9, signed_char=True, sanitized=True), raw_noto)
        self.assertEqual(self.run_renderer(9, signed_char=True, sanitized=True, explicit=False), raw_noto)

    def test_production_font_optout_restores_original_renderer(self):
        qualified = ("-DNRF52_PLATFORM=1", "-DMESH_NRF52_FLASH_TRIM=1", "-DST7789=1")
        baseline = self.run_renderer(0)
        self.assertEqual(self.run_renderer(
            9, (*qualified, "-DMESH_NRF52_FONT_MODE=0"), explicit=False), baseline)
        self.assertEqual(self.run_renderer(
            0, (*qualified, "-DMESH_NRF52_FONT_MODE=9")), baseline)

    def test_experiment_is_disabled_outside_nrf52_infrastructure_st7789(self):
        baseline = self.run_renderer(0)
        excluded = (
            ("-DNRF52_PLATFORM=1", "-DST7789=1"),  # Companion: no trim macro
            ("-DNRF52_PLATFORM=1", "-DMESH_NRF52_FLASH_TRIM=0", "-DST7789=1"),
            ("-DNRF52_PLATFORM=1", "-DMESH_NRF52_FLASH_TRIM=1", "-DST7789=1",
             "-DCOMPANION_RADIO_FULL=1"),
            ("-DMESH_NRF52_FLASH_TRIM=1", "-DST7789=1"),  # not nRF52
            ("-DNRF52_PLATFORM=1", "-DMESH_NRF52_FLASH_TRIM=1"),  # another display
        )
        for mode in (3, 8, 9):
            for flags in excluded:
                with self.subTest(mode=mode, flags=flags):
                    self.assertEqual(self.run_renderer(mode, flags), baseline)
        for flags in excluded:
            with self.subTest(default=True, flags=flags):
                self.assertEqual(self.run_renderer(9, flags, explicit=False), baseline)
            with self.subTest(override=True, flags=flags):
                self.assertEqual(self.run_renderer(
                    9, (*flags, "-DMESH_NRF52_FONT_MODE=9"), explicit=False), baseline)


class FontTrialSourceAuditTest(unittest.TestCase):
    def test_pinned_source_glyphs_keep_all_ink_pixels_when_sources_are_available(self):
        # Normal firmware builds/CI need no TrueType files or rasterizer. The
        # optional regeneration audit runs on the host that owns pinned fonts.
        try:
            from PIL import Image, ImageDraw, ImageFont, features
        except ImportError:
            self.skipTest("Pinned rasterizer is not installed")
        sys.path.insert(0, str(ROOT / "scripts"))
        import generate_font_family_trials as generator
        if Image.__version__ != generator.PILLOW_VERSION or features.version("freetype2") != generator.FREETYPE_VERSION:
            self.skipTest("Pinned Pillow/FreeType versions are not installed")
        font_directory = Path("C:/Windows/Fonts")
        if not font_directory.exists():
            font_directory = Path("/mnt/c/Windows/Fonts")
        if not all((font_directory / family.filename).exists() for family in generator.FAMILIES):
            self.skipTest("Pinned source font files are not available")
        header = (ROOT / "src/helpers/ui/FontFamilyTrials.h").read_text(encoding="utf-8")
        for family in generator.FAMILIES:
            source = font_directory / family.filename
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), family.sha256)
            section_match = re.search(
                rf"#if MESH_ARIAL_EXPERIMENT == {family.mode}\b(.*?)#endif", header, re.S)
            self.assertIsNotNone(section_match, "Generated header lacks a configured font family")
            section = section_match[1]
            for font_id, size in enumerate(family.sizes, 1):
                body = re.search(rf"static const uint8_t TRIAL_{font_id}\[\] = \{{(.*?)\}};", section, re.S)[1]
                data = bytes(int(value, 16) for value in re.findall(r"0x([0-9a-fA-F]+)", body))
                first, count, height = data[2], data[3], data[1]
                self.assertEqual((first, count, height), (32, 224, 19 if font_id == 1 else 28))
                source_font = ImageFont.truetype(str(source), size, layout_engine=ImageFont.Layout.BASIC)
                drawable = 0
                for slot in range(count):
                    entry = data[4 + 4 * slot:8 + 4 * slot]
                    offset = int.from_bytes(entry[:2], "big")
                    length = entry[2]
                    if offset == 0xffff:
                        self.assertEqual(length, 0)
                        continue
                    drawable += 1
                    bitmap = data[4 + 4 * count + offset:4 + 4 * count + offset + length]
                    stride = (height + 7) // 8
                    encoded = {(index // stride, 8 * (index % stride) + bit)
                               for index, value in enumerate(bitmap)
                               for bit in range(8) if value & (1 << bit)}
                    image = Image.new("L", (256, 256), 0)
                    ImageDraw.Draw(image).text((96, 96), chr(first + slot),
                                               font=source_font, fill=255, anchor="ls")
                    image = image.point(lambda value: 255 if value >= 128 else 0)
                    box = image.getbbox()
                    self.assertIsNotNone(box)
                    original = {(x, y) for y in range(box[1], box[3])
                                for x in range(box[0], box[2]) if image.getpixel((x, y))}
                    def normalize(pixels):
                        left = min(x for x, _ in pixels)
                        top = min(y for _, y in pixels)
                        return {(x - left, y - top) for x, y in pixels}
                    with self.subTest(mode=family.mode, size=size, code=first + slot):
                        self.assertEqual(normalize(encoded), normalize(original),
                                         "Source ink was cropped or altered during bitmap placement")
                self.assertEqual(drawable, 189)


if __name__ == "__main__":
    unittest.main()
