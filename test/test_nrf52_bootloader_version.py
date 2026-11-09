"""Compile the real reader; optionally verify pinned stock/OTAFIX artifacts."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test/fixtures/nrf52_bootloader_version"
SPEC = importlib.util.spec_from_file_location("nrf52_stock_artifacts", FIXTURES / "stock_artifacts.py")
STOCK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STOCK)


def workflow_step_script(workflow, name):
    """Extract the named step's literal shell block, ending at YAML indentation.

    This test deliberately executes the production workflow script. Avoid a
    YAML dependency in the lightweight bootloader job, and do not infer where
    the next step or job starts from the job's name.
    """
    lines = workflow.splitlines(keepends=True)
    header = re.compile(r"^(?P<indent> *)- name: " + re.escape(name) + r"\s*$")
    matches = [(i, m) for i, line in enumerate(lines)
               if (m := header.match(line.rstrip("\r\n")))]
    if len(matches) != 1:
        raise ValueError("Expected exactly one named workflow step")
    start, match = matches[0]
    step_indent = len(match.group("indent"))
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= step_indent:
            break
        end += 1

    run_indent = step_indent + 2
    run_header = re.compile(r"^ {" + str(run_indent) + r"}run: \|[+-]?\s*$")
    run_lines = [i for i in range(start + 1, end)
                 if run_header.match(lines[i].rstrip("\r\n"))]
    if len(run_lines) != 1:
        raise ValueError("Expected one literal run block in workflow step")
    body = []
    for line in lines[run_lines[0] + 1:end]:
        if line.strip() and len(line) - len(line.lstrip(" ")) <= run_indent:
            break
        body.append(line)
    if not any(line.strip() for line in body):
        raise ValueError("Workflow run block is empty")
    return textwrap.dedent("".join(body))


class BootloaderVersionTest(unittest.TestCase):
    def test_otafix_release_download_falls_back_without_skipping_verification(self):
        workflow = (ROOT / '.github/workflows/run-unit-tests.yml').read_text(encoding='utf-8')
        body = workflow_step_script(workflow, 'Verify all signed OTAFIX 2.4.6 release images')
        self.assertIn('releases/assets/546337954', body)
        self.assertIn('Accept: application/octet-stream', body)
        prelude = r'''
curl() {
  case "$*" in
    *api.github.com*) echo api >> "$CAPTURE"; [ "$MODE" != both_fail ] ;;
    *) echo direct >> "$CAPTURE"; [ "$MODE" = direct_ok ] ;;
  esac
}
python3() { echo verify:"$*" >> "$CAPTURE"; }
'''
        for mode, expected, success in (
                ('direct_ok', ['direct', 'verify:-B test/test_nrf52_bootloader_version.py -v'], True),
                ('fallback_ok', ['direct', 'api', 'verify:-B test/test_nrf52_bootloader_version.py -v'], True),
                ('both_fail', ['direct', 'api'], False)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                log = Path(temp) / 'calls'
                env = dict(os.environ, CAPTURE=str(log), MODE=mode,
                           MESHCORE_BOOTLOADER_MOTA_ZIP=str(Path(temp) / 'asset.zip'))
                result = subprocess.run(['bash', '-e', '-c', prelude + body],
                                        cwd=ROOT, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
                self.assertEqual(log.read_text(encoding='utf-8').splitlines(), expected)

    def test_workflow_step_script_stops_at_the_next_step_or_job(self):
        prefix = ('  bootloader-version:\n    steps:\n'
                  '      - name: Pin release\n        env:\n          X: y\n'
                  '        run: |\n          echo "test-core:"\n'
                  '          if true; then\n            echo verified\n          fi\n')
        expected = 'echo "test-core:"\nif true; then\n  echo verified\nfi\n'
        for suffix in ('', '\n  test-core:\n    steps: []\n',
                       '\n      - name: Another step\n        run: false\n',
                       '\n      - uses: actions/checkout@v6\n'):
            with self.subTest(suffix=suffix):
                self.assertEqual(workflow_step_script(prefix + suffix, 'Pin release').strip(),
                                 expected.strip())
        with self.assertRaises(ValueError):
            workflow_step_script(prefix, 'Absent')
        with self.assertRaises(ValueError):
            workflow_step_script(prefix + prefix, 'Pin release')
        with self.assertRaises(ValueError):
            workflow_step_script('      - name: Pin release\n        run: false\n', 'Pin release')

    def test_stock_artifact_manifest(self):
        records = json.loads((FIXTURES / "stock_images.json").read_text())["artifacts"]
        self.assertEqual(len(records), 26)
        self.assertEqual(len({record["image_sha256"] for record in records}), len(records))
        for record in records:
            with self.subTest(source=record["url"]):
                self.assertRegex(record["sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(record["image_sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(record["version"], r"^\d+\.\d+(?:\.\d+)?(?:[-_+].+)?$")
                self.assertIn(record["format"], ("hex", "uf2", "dfu"))
                self.assertEqual(record["start"], "0xf4000")
                self.assertEqual(record["size"], 40960)
                self.assertEqual(Path(record["file"]).name, record["file"])

    def test_stock_artifact_decoders(self):
        record = {"start": "0xf4000", "size": 40960, "format": "hex"}
        def ihex(address, kind, payload):
            data = bytes((len(payload), address >> 8, address & 255, kind)) + payload
            return ":" + (data + bytes((-sum(data) & 255,))).hex() + "\n"
        raw = (ihex(0, 4, b"\x00\x0f") + ihex(0x4000, 0, b"abcd") +
               ihex(0, 1, b"")).encode()
        self.assertEqual(STOCK.bootloader_image(raw, record)[:8], b"abcd\xff\xff\xff\xff")
        with self.assertRaises(ValueError):
            STOCK.bootloader_image(raw.replace(b"61626364", b"61626365"), record)
        with self.assertRaises(ValueError):
            STOCK.bootloader_image(raw.split(b":00000001")[0], record)
        conflict = (ihex(0, 4, b"\x00\x0f") + ihex(0x4000, 0, b"a") +
                    ihex(0x4000, 0, b"b") + ihex(0, 1, b"")).encode()
        with self.assertRaises(ValueError):
            STOCK.bootloader_image(conflict, record)
        record["format"] = "uf2"
        block = bytearray(512)
        struct.pack_into("<IIIIIIII", block, 0, 0x0a324655, 0x9e5d5157,
                         0x2000, 0xf4000, 4, 0, 1, 0xada52840)
        block[32:36] = b"abcd"
        struct.pack_into("<I", block, 508, 0x0ab16f30)
        self.assertEqual(STOCK.bootloader_image(block, record)[:4], b"abcd")
        struct.pack_into("<I", block, 8, 0x2001)
        self.assertEqual(STOCK.bootloader_image(block, record), b"\xff" * 40960)
        struct.pack_into("<I", block, 8, 0x2000)
        struct.pack_into("<I", block, 12, 0x26000)  # application-only UF2
        self.assertEqual(STOCK.bootloader_image(block, record), b"\xff" * 40960)
        block[-1] ^= 1
        with self.assertRaises(ValueError):
            STOCK.bootloader_image(block, record)
        record["format"] = "dfu"
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"manifest": {"softdevice_bootloader": {
                "sd_size": 2, "bl_size": 4, "bin_file": "sd_bl.bin"}}}))
            archive.writestr("sd_bl.bin", b"SDabcd")
        self.assertEqual(STOCK.bootloader_image(archive_bytes.getvalue(), record)[:4], b"abcd")

    def test_firmware_uses_shared_reader(self):
        board = (ROOT / "src/helpers/NRF52Board.cpp").read_text()
        getter = board.split("bool NRF52Board::getBootloaderVersion(", 1)[1].split("\n}", 1)[0]
        self.assertIn("mesh::nrf52BootloaderRegion(", getter)
        self.assertIn("NRF_FICR->CODEPAGESIZE, NRF_FICR->CODESIZE", getter)
        self.assertIn("mbr_start, NRF_UICR->NRFFW[0]", getter)
        self.assertIn("mesh::nrf52BootloaderVersion(", getter)
        self.assertIn("bootloaderVersion, out, max_len", getter)
        self.assertNotIn("0x000FB000", getter)
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        cli = common.split('configKeyEquals(config, "bootloader.ver")', 1)[1].split("} else {", 1)[0]
        self.assertIn("char ver[128]", cli)
        self.assertIn("_board->getBootloaderVersion(ver, sizeof(ver))", cli)

    def test_companion_getter_and_real_cmd42_dispatch(self):
        from cpp_source import body
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        local = body(source, "bool MyMesh::handleLocalControlCommand(")
        branch = body(local, 'if (strcmp(command, "get bootloader.ver") == 0)')
        # Execute the actual pointer/size validation, space normalization and
        # query. Other settings and hardware services cannot hide a getter bug.
        entry = local[:local.index("#if defined(ESP32_PLATFORM)")]
        getter = entry + branch + "\n  return false;\n}\n"
        shared = body(source, "bool MyMesh::handleCommand(")
        self.assertIn("if (handleLocalControlCommand(command, reply, reply_capacity)) return true;", shared)
        shared_entry = shared[:shared.index("#if defined(ESP32_PLATFORM)")]
        getter += shared_entry + "  (void)sender_timestamp;\n  return handleLocalControlCommand(command, reply, reply_capacity);\n}\n"
        framed = body(source, "if (mesh::companion::isRunCliFrame(")
        terminal = body(source, "void MyMesh::handleTerminalCommand(")
        terminal_adapter = body(terminal, "if (!usb_mota_owner_transition")
        self.assertIn("handleLocalControlCommand(command, local_reply, sizeof(local_reply))", terminal_adapter)
        # The read-only query is not hidden behind Full, OTA, logging, terminal,
        # or private-key export feature switches.
        import re
        nesting = re.findall(r"^\s*#\s*(if|ifdef|ifndef|endif)\b",
                             local[:local.index(branch)], re.MULTILINE)
        self.assertEqual(sum(-1 if token == "endif" else 1 for token in nesting), 0)
        for forbidden in ("savePrefs", "reboot", "startOTAUpdate", "_prefs", "ota_"):
            self.assertNotIn(forbidden, branch)
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-boot-version-") as temp:
            work = Path(temp)
            (work / "production_companion_getter.inc").write_text(getter)
            (work / "production_companion_frame.inc").write_text(framed)
            (work / "production_companion_terminal.inc").write_text(terminal_adapter)
            for platform, full in (("NRF52_PLATFORM", 0), ("NRF52_PLATFORM", 1),
                                   ("ESP32_PLATFORM", 0), ("STM32_PLATFORM", 0),
                                   ("RP2040_PLATFORM", 0)):
                with self.subTest(platform=platform, full=full):
                    exe = work / (platform + str(full))
                    command = [os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                        "-D" + platform + "=1", "-DCOMPANION_RADIO_FULL=" + str(full),
                        "-DCOMPANION_FEATURE_TEXT_TERMINAL=" + str(full), "-DCOMPANION_FEATURE_OTA_CLI=" + str(full),
                        "-I" + str(ROOT / "src"), "-I" + str(work),
                        str(FIXTURES / "test_companion.cpp"), "-o", str(exe)]
                    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("getter/dispatch cases passed", result.stdout)

    def test_native_version_reader(self):
        compiler = shutil.which(os.environ.get("CXX", "g++"))
        self.assertIsNotNone(compiler, "g++ (or CXX) is required for this native test")
        with tempfile.TemporaryDirectory(prefix="meshcore-boot-version-") as temp:
            exe = Path(temp) / ("test.exe" if os.name == "nt" else "test")
            command = [compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                       "-I" + str(ROOT / "src"), "-isystem", str(ROOT / "test/mocks"),
                       str(ROOT / "test/fixtures/nrf52_bootloader_version/test_version.cpp"),
                       "-o", str(exe)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("native cases passed", result.stdout)
            # Optional manufacturer downloads: exact hashes, normalized layout,
            # and exact reported versions, not merely a dotted-number regex.
            stock_cache = os.environ.get("MESHCORE_STOCK_BOOTLOADER_CACHE")
            if stock_cache:
                records = json.loads((FIXTURES / "stock_images.json").read_text())["artifacts"]
                for record in records:
                    with self.subTest(stock_image=record["url"]):
                        raw = (Path(stock_cache) / record["file"]).read_bytes()
                        self.assertEqual(hashlib.sha256(raw).hexdigest(), record["sha256"])
                        payload = STOCK.bootloader_image(raw, record)
                        self.assertEqual(hashlib.sha256(payload).hexdigest(), record["image_sha256"])
                        result = subprocess.run([str(exe), "--hex", record["start"]],
                                                input=payload.hex(), capture_output=True,
                                                text=True, timeout=20)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual(result.stdout.strip(), record["version"])
                print(f"Verified version reader against {len(records)} distinct manufacturer bootloader images")
            # Optional local/release artifacts; never downloaded or flashed by
            # this test. Each must be a raw bootloader image starting at F4000.
            for path in os.environ.get("MESHCORE_BOOTLOADER_IMAGES", "").split(os.pathsep):
                if path:
                    with self.subTest(image=path):
                        result = subprocess.run([str(exe), path], check=True, capture_output=True,
                                                text=True, timeout=20)
                        self.assertRegex(result.stdout, r"\d+\.\d+(?:\.\d+)?")
            release = os.environ.get("MESHCORE_BOOTLOADER_MOTA_ZIP")
            if release:
                self.assertEqual(hashlib.sha256(Path(release).read_bytes()).hexdigest(),
                                 "04fe6d1b11b0c3b926289d6384dae5cb878462da"
                                 "a7fbbe9afb70f42d869f8003", "unexpected 2.4.6 release archive")
                sys.path.insert(0, str(ROOT / "tools/mota"))
                import motalib
                with zipfile.ZipFile(release) as archive:
                    names = [name for name in archive.namelist() if name.endswith(".mota")]
                    self.assertEqual(len(names), 17)
                    sd_name = "mota/update-heltec_mesh_tower_v2_sdcard_bootloader-0.11.0-OTAFIX2.4.6.mota"
                    self.assertIn(sd_name, names)
                    for name in names:
                        with self.subTest(release_image=name):
                            parsed = motalib.parse_container(archive.read(name))
                            self.assertEqual(motalib.verify(parsed), [])
                            if name == sd_name:
                                self.assertNotIn(b"UF2 Bootloader ", parsed.payload)
                                identity = motalib.parse_bootloader_identity(parsed.payload)
                                self.assertEqual(identity.crc32, 0x5DACDB3D)
                                self.assertEqual(identity.boot_version, 0x020406FF)
                            result = subprocess.run([str(exe), "--hex"], input=parsed.payload.hex(),
                                                    capture_output=True, text=True, timeout=20)
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                            self.assertIn("OTAFIX2.4.6", result.stdout)
                            if name == sd_name:
                                self.assertEqual(result.stdout.strip(), "OTAFIX2.4.6")
                    print(f"Verified version reader against {len(names)} signed OTAFIX 2.4.6 release images")

            release_249 = os.environ.get("MESHCORE_BOOTLOADER_MOTA_249_ZIP")
            if release_249:
                self.assertEqual(hashlib.sha256(Path(release_249).read_bytes()).hexdigest(),
                                 "6fd7d799873ba6016d37679d9ef3610e62105c2477b49a71933eec12756e625c",
                                 "unexpected 2.4.9 release archive")
                with zipfile.ZipFile(release_249) as archive:
                    names = [name for name in archive.namelist() if name.endswith(".mota")]
                    self.assertEqual(len(names), 27)
                    for name in names:
                        with self.subTest(release_image=name):
                            # The pinned bundle holds one exact 40 KiB full image per
                            # board. Isolate the native reader without making this
                            # test depend on package capability-policy changes.
                            container = archive.read(name)
                            self.assertEqual(len(container), 41330)
                            payload = container[-5 - 40960:-5]
                            result = subprocess.run([str(exe), "--hex"], input=payload.hex(),
                                                    capture_output=True, text=True, timeout=20)
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                            self.assertIn("OTAFIX2.4.9", result.stdout)
                            if "sensecap_solar_p1" in name:
                                self.assertEqual(result.stdout.strip(), "v0.11.0-OTAFIX2.4.9")
                            if "wiscore_rak4631_board_bootloader" in name:
                                self.assertEqual(result.stdout.strip(), "OTAFIX2.4.9")
                    print(f"Verified version reader against {len(names)} OTAFIX 2.4.9 board images")


if __name__ == "__main__":
    unittest.main()
