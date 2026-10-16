"""Production crypto vs RFC vector + independent Python decoder, no AES mocks."""
from pathlib import Path
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("management", ROOT / "tools/management/report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates = [Path(os.environ.get("MESHCORE_CRYPTO_DIR", "/nonexistent"))]
        candidates += sorted((ROOT / ".pio/libdeps").glob("*/Crypto"))
        cls.crypto = next((p for p in candidates if (p / "AES128.cpp").is_file()), None)
        if cls.crypto is None:
            raise RuntimeError("Install rweather/Crypto 0.4.0 or set MESHCORE_CRYPTO_DIR (no mock fallback)")
        cls.work = tempfile.TemporaryDirectory()
        cls.exe = Path(cls.work.name) / "management-test"
        sources = ["AES128.cpp", "AESCommon.cpp", "BlockCipher.cpp", "Crypto.cpp", "SHA256.cpp", "Hash.cpp"]
        host_define = [] if os.name == "nt" else ["-DHOST_BUILD"]
        sanitizers = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"]
        cmd = [shutil.which("g++") or "g++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra"] + host_define + [
               *sanitizers, "-I", str(cls.crypto), "-I", str(ROOT / "src"),
               str(ROOT / "src/helpers/ManagementReport.cpp"),
               str(ROOT / "test/fixtures/management/protocol_test.cpp")]
        cmd += [str(cls.crypto / p) for p in sources] + ["-o", str(cls.exe)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        result = subprocess.run([str(cls.exe)], check=True, capture_output=True, text=True)
        emitted = [bytes.fromhex(line) for line in result.stdout.splitlines()]
        cls.pages = [page for page in emitted if page[:4] == b"MGR2" and int.from_bytes(page[20:24], "little") == 42]
        cls.legacy_pages = [page for page in emitted if page[:4] == b"MGR1"]
        cls.empty_event_pages = [page for page in emitted if page[:4] == b"MGR2" and int.from_bytes(page[20:24], "little") == 43]
        cls.prototypes = [cls._old_prototype(count) for count in (0, 4, 5)]
        # An old four-entry page pads from 166 to 179 bytes, the same length as
        # a current four-entry page. Its encrypted bytes can look like a valid
        # public event. Public shape checks cannot distinguish that overlap;
        # the unchanged AES-SIV key domain with the new 111-byte AAD must.
        for sequence in range(44, 300):
            prototype = cls._old_prototype(4, sequence)
            padded = prototype + bytes(179 - len(prototype))
            try:
                report._page_bounds(padded)
            except ValueError:
                continue
            cls.colliding_prototype = padded
            break
        else:
            raise AssertionError("no public-shape overlap found for old MGR2 prototype")
        # Compile the unchanged production runtime and transaction writer with
        # a memory filesystem/radio in place of the hardware-facing headers.
        fixture = ROOT / "test/fixtures/management"
        for name in ("ManagementReporter.cpp", "ManagementReporter.h", "FileRead.h", "ContactFileTransaction.h", "PersistentStoreFormat.h"):
            shutil.copyfile(ROOT / "src/helpers" / name, Path(cls.work.name) / name)
        for name in ("CommonCLI.h", "Mesh.h"):
            shutil.copyfile(fixture / name, Path(cls.work.name) / name)
        shutil.copyfile(ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h", Path(cls.work.name) / "IdentityStore.h")
        runtime = Path(cls.work.name) / "runtime-test"
        runtime_cmd = [shutil.which("g++") or "g++", "-std=c++17", "-O1", "-g"] + host_define + ["-DRP2040_PLATFORM",
                       *sanitizers, "-I", cls.work.name,
                       "-I", str(cls.crypto), "-I", str(ROOT / "src/helpers"), "-I", str(ROOT / "src"),
                       str(Path(cls.work.name) / "ManagementReporter.cpp"), str(ROOT / "src/helpers/ManagementReport.cpp"),
                       str(fixture / "runtime_test.cpp")]
        runtime_cmd += [str(cls.crypto / p) for p in sources] + ["-o", str(runtime)]
        subprocess.run(runtime_cmd, check=True, capture_output=True, text=True)
        cls.runtime = runtime

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    @classmethod
    def _old_prototype(cls, count, sequence=44):
        """Generate authentic unreleased MGR2 with its original 98-byte AAD."""
        from Crypto.Cipher import AES
        header = bytearray(cls.pages[0][:98])
        header[20:24] = sequence.to_bytes(4, "little")
        header[78:83] = bytes((0, 1, count, 0, count))
        key = report.derive(report.password_key("management test password"),
                            "MeshCore-MGR1-SIV", header[4:20])
        cipher = AES.new(key, AES.MODE_SIV)
        cipher.update(header)
        private = (bytes(12) + b"\x01") * count
        ciphertext, tag = cipher.encrypt_and_digest(private)
        return bytes(header) + ciphertext + tag

    def _run_cli(self, path, *options):
        output, errors = io.StringIO(), io.StringIO()
        status = 0
        with mock.patch.object(sys, "argv", ["report.py", str(path), *options]), \
                mock.patch.object(report.getpass, "getpass", return_value="management test password"), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                report.main()
            except SystemExit as error:
                status = error.code
        return status, output.getvalue(), errors.getvalue()

    @classmethod
    def _acl_pages(cls, entries, current=True):
        from Crypto.Cipher import AES
        base = cls.empty_event_pages[0][:111] if current else cls.legacy_pages[0][:83]
        per_page = 4 if current else 6
        count = max(1, (len(entries) + per_page - 1) // per_page)
        pages = []
        for index in range(count):
            header = bytearray(base)
            block = entries[index * per_page:(index + 1) * per_page]
            header[78:83] = bytes((index, count, len(entries), index * per_page, len(block)))
            key = report.derive(report.password_key("management test password"),
                                "MeshCore-MGR1-SIV", header[4:20])
            cipher = AES.new(key, AES.MODE_SIV); cipher.update(header)
            ciphertext, tag = cipher.encrypt_and_digest(b"".join(token + bytes((flags,)) for token, flags in block))
            pages.append(bytes(header) + ciphertext + tag)
        return pages

    def test_authenticated_duplicate_acl_fingerprints_are_rejected(self):
        password = "management test password"
        for current in (False, True):
            duplicate = self._acl_pages([(bytes(12), 1), (bytes(12), 2)], current)
            with self.assertRaisesRegex(ValueError, "[Dd]uplicate"):
                report.decode_page(duplicate[0], password)
            with self.assertRaisesRegex(ValueError, "[Dd]uplicate"):
                report.decode_report(duplicate, password)
            per_page = 4 if current else 6
            unique = [(index.to_bytes(12, "little"), 3) for index in range(per_page)]
            duplicate = self._acl_pages([*unique, (unique[0][0], 2)], current)
            for page in duplicate:
                report.decode_page(page, password)  # No duplicates within either page.
            with self.assertRaisesRegex(ValueError, "[Dd]uplicate"):
                report.decode_report(duplicate, password)
            messages = []
            for page in duplicate:
                padded = page + bytes(3 + ((len(page) - 3 + 15) // 16) * 16 - len(page))
                messages.append(dict(type="PACKET", direction="rx", raw=(b"\x1a\x00" + padded).hex()))
            with self.assertRaisesRegex(ValueError, "[Dd]uplicate"):
                report.mqtt_reports(messages, password)
            valid = self._acl_pages([*unique, (per_page.to_bytes(12, "little"), 3)], current)
            decoded = report.decode_report(valid, password)
            self.assertEqual(len(decoded["acl"]), per_page + 1)
            self.assertTrue(all(entry["admin"] and entry["ota_signer"] for entry in decoded["acl"]))

    def test_cli_matches_administrator_for_each_mqtt_report(self):
        from Crypto.Cipher import AES
        administrator = bytes(range(32))
        messages = []
        for radio in (bytes(range(16)), bytes(range(16, 32))):
            header = bytearray(self.empty_event_pages[0][:111])
            header[4:20] = radio
            header[78:83] = bytes((0, 1, 1, 0, 1))
            key = report.derive(report.password_key("management test password"),
                                "MeshCore-MGR1-SIV", radio)
            private = report.fingerprint("management test password", radio, administrator) + b"\x03"
            cipher = AES.new(key, AES.MODE_SIV); cipher.update(header)
            ciphertext, tag = cipher.encrypt_and_digest(private)
            page = bytes(header) + ciphertext + tag
            padded = page + bytes(3 + ((len(page) - 3 + 15) // 16) * 16 - len(page))
            messages.append(dict(type="PACKET", direction="rx", raw=(b"\x1a\x00" + padded).hex()))
        path = Path(self.work.name) / "cli-mqtt.json"
        for jsonl in (False, True):
            path.write_text("\n".join(json.dumps(message) for message in messages)
                            if jsonl else json.dumps(messages), encoding="ascii")
            status, output, errors = self._run_cli(path, "--mqtt", "--match-admin", administrator.hex())
            self.assertEqual((status, errors), (0, ""))
            decoded = json.loads(output)
            self.assertEqual(len(decoded), 2)
            for item in decoded:
                self.assertEqual(item.get("matching_acl"), item["acl"])
                self.assertEqual(len(item["matching_acl"]), 1)
                self.assertTrue(item["matching_acl"][0]["admin"] and item["matching_acl"][0]["ota_signer"])
            status, output, errors = self._run_cli(path, "--mqtt", "--match-admin", "ff" * 32)
            self.assertEqual((status, errors), (0, ""))
            self.assertEqual([item.get("matching_acl") for item in json.loads(output)], [[], []])

    def test_cli_rejects_invalid_match_key_even_with_no_mqtt_reports(self):
        path = Path(self.work.name) / "cli-empty.json"
        path.write_text("[]", encoding="ascii")
        for candidate in ("not-hex", "00", ""):
            status, output, errors = self._run_cli(path, "--mqtt", "--match-admin", candidate)
            self.assertEqual(status, 2)
            self.assertEqual(output, "")
            self.assertIn("error:", errors)

    def test_cli_file_read_errors_are_reported_without_tracebacks(self):
        for path in (Path(self.work.name) / "missing.json", Path(self.work.name)):
            status, output, errors = self._run_cli(path)
            self.assertEqual(status, 2)
            self.assertEqual(output, "")
            self.assertIn("error:", errors)
            self.assertNotIn("Traceback", errors)

    def test_cli_closed_stdin_reports_input_errors_without_tracebacks(self):
        path = Path(self.work.name) / "cli-no-password.json"
        path.write_text(json.dumps([page.hex() for page in self.pages]), encoding="ascii")
        for arguments, expected in (([str(path)], "password input unavailable"),
                                    ([str(path.parent / "absent.json")], "No such file"),
                                    ([str(path), "--match-admin", "00"], "administrator key")):
            result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/management/report.py"),
                                     *arguments], input="", capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn(expected, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            if expected != "password input unavailable":
                self.assertNotIn("Management password:", result.stderr)

    def test_cli_canonical_match_and_input_errors(self):
        path = Path(self.work.name) / "cli-pages.json"
        path.write_text(json.dumps([page.hex() for page in self.pages]), encoding="ascii")
        status, output, errors = self._run_cli(path, "--match-admin", "00" * 32)
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(json.loads(output)["matching_acl"], [])
        for malformed in ("{", '["not hex"]'):
            path.write_text(malformed, encoding="ascii")
            status, output, errors = self._run_cli(path)
            self.assertEqual(status, 2)
            self.assertEqual(output, "")
            self.assertIn("error:", errors)

    def test_cli_rejects_excessive_json_nesting_before_password_prompt(self):
        path = Path(self.work.name) / "cli-deep.json"
        path.write_text("[" * 10000 + "0" + "]" * 10000, encoding="ascii")
        for options in ((), ("--mqtt",)):
            with mock.patch.object(report.getpass, "getpass") as password_prompt:
                with mock.patch.object(sys, "argv", ["report.py", str(path), *options]), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    with self.assertRaises(SystemExit) as failure:
                        report.main()
                self.assertEqual(failure.exception.code, 2)
                self.assertIn("nested too deeply", errors.getvalue())
                self.assertNotIn("Traceback", errors.getvalue())
                password_prompt.assert_not_called()

    def test_cli_rejects_deep_mqtt_jsonl_before_password_prompt(self):
        path = Path(self.work.name) / "cli-deep-jsonl.json"
        deep = '{"metadata":' * 10000 + "0" + "}" * 10000
        for text in ("{}\n" + deep, deep + "\n{}"):
            path.write_text(text, encoding="ascii")
            output, errors = io.StringIO(), io.StringIO()
            with mock.patch.object(report.getpass, "getpass") as password_prompt, \
                    mock.patch.object(sys, "argv", ["report.py", str(path), "--mqtt"]), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                with self.assertRaises(SystemExit) as failure:
                    report.main()
            self.assertEqual(failure.exception.code, 2)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("nested too deeply", errors.getvalue())
            self.assertNotIn("Traceback", errors.getvalue())
            password_prompt.assert_not_called()

    def test_input_json_depth_limit_is_explicit_and_checks_before_decoding(self):
        limit = report.MAX_JSON_DEPTH
        for opening, closing in (("[", "]"), ('{"metadata":', "}")):
            with self.subTest(opening=opening):
                value = report._load_input_json(opening * limit + "0" + closing * limit)
                for _ in range(limit):
                    value = value[0] if isinstance(value, list) else value["metadata"]
                self.assertEqual(value, 0)
                text = opening * (limit + 1) + "0" + closing * (limit + 1)
                with mock.patch.object(report.json, "loads") as decoder:
                    with self.assertRaisesRegex(ValueError, "nested too deeply"):
                        report._load_input_json(text)
                    decoder.assert_not_called()

    def test_input_json_depth_ignores_string_brackets_and_escaped_quotes(self):
        text = 'brackets [{]} and quote " and backslash \\ ' * 1000
        document = {"metadata": text, "raw": "\\"}
        self.assertEqual(report._load_input_json(json.dumps(document)), document)
        # An escaped backslash before a closing quote must not hide the next
        # actual container, including when the following record exceeds depth.
        deep = "[" * report.MAX_JSON_DEPTH + "0" + "]" * report.MAX_JSON_DEPTH
        with self.assertRaisesRegex(ValueError, "nested too deeply"):
            report._load_input_json("[" + json.dumps("\\") + "," + deep + "]")

    def test_interoperable_full_report(self):
        decoded = report.decode_report(list(reversed(self.pages)), "management test password")
        self.assertEqual(decoded["sequence"], 42)
        self.assertEqual(len(decoded["acl"]), 36)
        self.assertEqual(decoded["firmware_version"], "1.17.1.5")
        self.assertEqual(decoded["protocol"], "MGR2")
        self.assertEqual(len(self.pages), 9)
        usb = decoded["usb_logging"]
        self.assertTrue(usb["supported"] and usb["recovery_deferred"])
        self.assertTrue(usb["host_connected"] and usb["reader_connected"])
        self.assertTrue(usb["logger_active"])
        self.assertEqual(usb["stage"], 2)
        self.assertEqual(usb["backoff_step"], 8)
        self.assertEqual(usb["retry_seconds"], 604800)
        self.assertEqual(usb["inactive_seconds"], 0xffffffff)
        self.assertTrue(usb["watchdog_auto"])
        self.assertEqual(usb["auto_connected_seconds"], 1209600)
        self.assertEqual(decoded["usb_watchdog_last"], dict(
            reasons=15, reason_names=["host-absent", "reader-absent", "tx-stalled", "client-inactive"],
            action_code=3, action="reboot-requested", persisted=True,
            epoch=1700000001, uptime_seconds=777, sequence=42))
        self.assertNotIn("manifest_id", decoded)

    def test_legacy_report_still_decodes_without_inventing_usb_status(self):
        decoded = report.decode_report(self.legacy_pages, "management test password")
        self.assertEqual(decoded["protocol"], "MGR1")
        self.assertEqual(len(decoded["acl"]), 36)
        self.assertIsNone(decoded["usb_logging"])
        self.assertIsNone(decoded["usb_watchdog_last"])
        with self.assertRaises(ValueError):
            report.decode_report([self.legacy_pages[0]] + self.pages[1:], "management test password")

    def test_unreleased_98_byte_prototypes_are_not_accepted_as_current_pages(self):
        for prototype in self.prototypes:
            with self.subTest(count=prototype[82]):
                with self.assertRaises(ValueError):
                    report.decode_page(prototype, "management test password")
                with self.assertRaises(ValueError):
                    report.decode_report([prototype], "management test password")
                for prefix in (b"\x3d\x00", b"\x19\x00"):
                    wire = prefix + prototype
                    if prefix[0] == 0x19:
                        wire += bytes(3 + ((len(prototype) - 3 + 15) // 16) * 16 - len(prototype))
                    message = dict(type="PACKET", direction="rx", raw=wire.hex())
                    extracted = report.mqtt_payload(message)
                    if extracted is not None:
                        # A padded four-entry prototype can overlap current
                        # public structure, but never authenticates as MGR2.
                        with self.assertRaises(ValueError):
                            report.decode_page(extracted, "management test password")

    def test_old_prototype_padding_overlap_fails_current_authentication(self):
        from Crypto.Cipher import AES
        padded = self.colliding_prototype
        old = padded[:166]
        key = report.derive(report.password_key("management test password"),
                            "MeshCore-MGR1-SIV", old[4:20])
        cipher = AES.new(key, AES.MODE_SIV); cipher.update(old[:98])
        self.assertEqual(cipher.decrypt_and_verify(old[98:-16], old[-16:]),
                         (bytes(12) + b"\x01") * 4)
        self.assertEqual(report._page_bounds(padded), (111, 179))
        with self.assertRaises(ValueError):
            report.decode_page(padded, "management test password")
        for prefix in (b"\x3d\x00", b"\x19\x00"):
            message = dict(type="PACKET", direction="rx", raw=(prefix + padded).hex())
            self.assertEqual(report.mqtt_payload(message), padded)
            with self.assertRaises(ValueError):
                report.mqtt_reports([message], "management test password")

    def test_current_none_event_with_empty_acl_is_authenticated(self):
        decoded = report.decode_report(self.empty_event_pages, "management test password")
        self.assertEqual(decoded["protocol"], "MGR2")
        self.assertEqual(decoded["acl"], [])
        self.assertIsNone(decoded["usb_watchdog_last"])

    def test_current_reports_decode_in_browser_with_real_production_ciphertext(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for cross-language management decoding")
        script = """
const assert = require('assert');
globalThis.crypto = require('crypto').webcrypto;
const decoder = require(process.argv[1]);
let data = '';
process.stdin.on('data', chunk => { data += chunk; });
process.stdin.on('end', async () => {
  try {
    const captures = JSON.parse(data);
    const pages = captures.MGR2;
    const legacy = await decoder.decodeManagement(captures.MGR1.join('\\n'), 'management test password');
    assert(legacy.authenticated && legacy.complete);
    assert.strictEqual(legacy.acl.length, 36);
    assert.strictEqual(legacy.public.protocol, 'MGR1');
    assert.strictEqual(legacy.pageCount, 6);
    assert.strictEqual(legacy.public.usbWatchdogLast, null);
    assert.strictEqual(legacy.public.usbLogging, null);
    for (const prototype of captures.prototypes) {
      await assert.rejects(decoder.decodeManagement(prototype, 'management test password'));
    }
    await assert.rejects(decoder.decodeManagement(captures.oldPadded, 'management test password'), /Password is wrong/);
    const mgr3 = Buffer.from(pages[0], 'hex'); mgr3[3] = '3'.charCodeAt(0);
    await assert.rejects(decoder.decodeManagement(mgr3.toString('hex'), 'management test password'));
    const empty = await decoder.decodeManagement(captures.empty.join('\\n'), 'management test password');
    assert(empty.authenticated && empty.complete && !empty.acl.length);
    assert.strictEqual(empty.public.protocol, 'MGR2');
    assert.strictEqual(empty.public.usbWatchdogLast, null);
    const result = await decoder.decodeManagement(pages.join('\\n'), 'management test password');
    assert(result.authenticated && result.complete);
    assert.strictEqual(result.acl.length, 36);
    assert.strictEqual(result.pageCount, 9);
    assert.strictEqual(result.public.protocol, 'MGR2');
    assert.strictEqual(result.public.usbLogging.retrySeconds, 604800);
    assert.strictEqual(result.public.usbLogging.inactiveSeconds, 0xffffffff);
    assert.strictEqual(result.public.usbLogging.hostConnected, true);
    assert.strictEqual(result.public.usbLogging.readerConnected, true);
    assert.strictEqual(result.public.usbLogging.loggerActive, true);
    assert.strictEqual(result.public.usbLogging.watchdogAuto, true);
    assert.strictEqual(result.public.usbLogging.autoConnectedSeconds, 1209600);
    assert.strictEqual(result.public.usbWatchdogLast.epoch, 1700000001);
    assert.strictEqual(result.public.usbWatchdogLast.uptimeSeconds, 777);
    assert.strictEqual(result.public.usbWatchdogLast.sequence, 42);
    assert.strictEqual(result.public.usbWatchdogLast.reasons, 15);
    assert.strictEqual(result.public.usbWatchdogLast.action, 'reboot-requested');
    assert.strictEqual(result.public.usbWatchdogLast.persisted, true);
    const raw = pages.map(page => '1900' + page + '00'.repeat((3 + Math.ceil((page.length / 2 - 3) / 16) * 16) - page.length / 2));
    const routed = await decoder.decodeManagement(raw.join('\\n'), 'management test password');
    assert(routed.authenticated && routed.complete);
    assert.strictEqual(routed.public.envelope.route, 'flood');
    const mutated = Buffer.from(pages[0], 'hex'); mutated[87] ^= 1;
    await assert.rejects(decoder.decodeManagement(mutated.toString('hex'), 'management test password'), /Password is wrong/);
    const changedLogger = Buffer.from(pages[0], 'hex'); changedLogger[84] ^= 4;
    await assert.rejects(decoder.decodeManagement(changedLogger.toString('hex'), 'management test password'), /Password is wrong/);
    for (let offset = 98; offset < 111; ++offset) {
      const changedEvent = Buffer.from(pages[0], 'hex'); changedEvent[offset] ^= 1;
      await assert.rejects(decoder.decodeManagement(changedEvent.toString('hex'), 'management test password'), /Password is wrong|Invalid/);
    }
    const changedSnapshot = Buffer.from(pages[1], 'hex'); changedSnapshot[99] ^= 1;
    await assert.rejects(decoder.decodeManagement(pages[0] + '\\n' + changedSnapshot.toString('hex'), ''), /same snapshot/);
  } catch (error) { console.error(error); process.exitCode = 1; }
});
"""
        import json
        subprocess.run([node, "-e", script, str(ROOT / "docs/_javascript/management_decoder.js")],
                       input=json.dumps({"MGR2": [page.hex() for page in self.pages],
                                         "MGR1": [page.hex() for page in self.legacy_pages],
                                         "prototypes": [page.hex() for page in self.prototypes],
                                         "oldPadded": self.colliding_prototype.hex(),
                                         "empty": [page.hex() for page in self.empty_event_pages]}),
                       text=True, check=True, timeout=30)

    def test_runtime_storage_scheduling_rollover_and_failures(self):
        subprocess.run([str(self.runtime)], check=True)

    def test_actual_legacy_route_adoption_persistence_and_precedence(self):
        source = (ROOT / "src/helpers/CommonCLI_Management.cpp").read_text(encoding="utf-8")
        state = source[source.index("static constexpr char DATA_ROUTE_FILE[]"):
                       source.index("static uint32_t readRoute32(")]
        generated = ("namespace mesh {\n" + state +
                     extract_braced(source, "static uint8_t nibble(") + "\n" +
                     extract_braced(source, "static char* trimDataRoute(") + "\n" +
                     extract_braced(source, "static bool parseDataPath(") + "\n" +
                     extract_braced(source, "static void writeRoute32(") + "\n" +
                     extract_braced(source, "static bool dataRouteSave(") + "\n}\n" +
                     extract_braced(source, "bool CommonCLI::adoptLegacyDataTxPath(") + "\n")
        (Path(self.work.name) / "production_route.inc").write_text(generated, encoding="ascii")
        executable = Path(self.work.name) / "route-test"
        sanitizers = [] if os.name == "nt" else ["-DHOST_BUILD", "-fsanitize=address,undefined",
                                               "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"]
        compiled = subprocess.run([shutil.which("g++") or "g++", "-std=c++17", "-O1", "-g",
                                   "-Wall", "-Wextra", "-DRP2040_PLATFORM", *sanitizers,
                                   "-I", self.work.name,
                                   str(ROOT / "test/fixtures/management/route_test.cpp"),
                                   "-o", str(executable)], capture_output=True, text=True)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        subprocess.run([str(executable)], check=True)

    def test_tampering_and_wrong_password(self):
        for position in (4, 20, 28, 74, 76, 83, 85, 86, 90, 94, *range(98, 111), 111, -1):
            changed = bytearray(self.pages[0]); changed[position] ^= 1
            with self.subTest(position=position), self.assertRaises(ValueError):
                report.decode_report([bytes(changed)] + self.pages[1:], "management test password")
        with self.assertRaises(ValueError):
            report.decode_report(self.pages, "incorrect management password")

    def test_logger_activity_is_authenticated_separately_from_connection(self):
        changed = bytearray(self.pages[0]); changed[84] ^= 4
        public = report._usb_status(changed)
        self.assertTrue(public["host_connected"] and public["reader_connected"])
        self.assertFalse(public["logger_active"])
        with self.assertRaises(ValueError):
            report.decode_page(bytes(changed), "management test password")

    def test_page_omission_duplicate_mixed(self):
        for pages in ([], self.pages[:-1], self.pages[1:] + [self.pages[1]], self.pages + [self.pages[0]]):
            with self.assertRaises(ValueError):
                report.decode_report(pages, "management test password")

    def test_current_size_page_bounds_and_unknown_version_are_rejected(self):
        for offset, value in ((3, ord("3")), (3, ord("4")), (79, 8), (81, 6), (82, 5), (80, 37), (84, 8), (85, 0xc0)):
            changed = bytearray(self.pages[0]); changed[offset] = value
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                report.decode_page(bytes(changed), "management test password")
        for payload in (self.pages[0][:-1], self.pages[0] + b"\x00", *(self.pages[0][:n] for n in range(127))):
            with self.assertRaises(ValueError):
                report.decode_page(payload, "management test password")
        mgr3 = bytearray(self.pages[0]); mgr3[3] = ord("3")
        for prefix in (b"\x3d\x00", b"\x19\x00"):
            self.assertIsNone(report.mqtt_payload(dict(type="PACKET", direction="rx", raw=(prefix + mgr3).hex())))

    def test_event_validation_and_advisory_zero_time(self):
        for code in (0x80, 0x8f, 0xdf, 0xef, 0xff):
            changed = bytearray(self.pages[0]); changed[98] = code
            with self.subTest(code=code), self.assertRaises(ValueError):
                report.decode_page(bytes(changed), "management test password")
        changed = bytearray(self.pages[0]); changed[107:111] = bytes(4)
        with self.assertRaises(ValueError):
            report.decode_page(bytes(changed), "management test password")
        changed = bytearray(self.pages[0]); changed[99:103] = bytes(4)
        self.assertEqual(report._watchdog_event(changed)["epoch"], 0)
        for action in range(1, 5):
            changed[98] = 15 | (action << 4)
            event = report._watchdog_event(changed)
            self.assertEqual(event["action_code"], action)
            self.assertFalse(event["persisted"])

    def test_authenticated_event_mismatch_is_not_one_snapshot(self):
        from Crypto.Cipher import AES
        page = self.pages[1]
        key = report.derive(report.password_key("management test password"), "MeshCore-MGR1-SIV", page[4:20])
        cipher = AES.new(key, AES.MODE_SIV); cipher.update(page[:111])
        private = cipher.decrypt_and_verify(page[111:-16], page[-16:])
        header = bytearray(page[:111]); header[99] ^= 1
        cipher = AES.new(key, AES.MODE_SIV); cipher.update(header)
        ciphertext, tag = cipher.encrypt_and_digest(private)
        changed = bytes(header) + ciphertext + tag
        self.assertEqual(report.decode_page(changed, "management test password"),
                         report.decode_page(page, "management test password"))
        with self.assertRaisesRegex(ValueError, "mixed report snapshots"):
            report.decode_report([self.pages[0], changed] + self.pages[2:], "management test password")

    def test_fingerprints_and_password_length(self):
        a = report.fingerprint("management test password", bytes(16), bytes(32))
        b = report.fingerprint("management test password", bytes([1]) + bytes(15), bytes(32))
        self.assertEqual(len(a), 12); self.assertNotEqual(a, b)
        for password in ("short", "x" * 97):
            with self.assertRaises(ValueError): report.password_key(password)

    def test_companions_excluded(self):
        for role in ("simple_repeater/MyMesh", "simple_room_server/MyMesh", "simple_sensor/SensorMesh"):
            self.assertIn("_cli.beginManagement(*this, _fs)", (ROOT / "examples" / (role + ".cpp")).read_text())
        for path in (ROOT / "examples/companion_radio").glob("*.*"):
            if path.suffix in (".cpp", ".h"): self.assertNotIn("beginManagement", path.read_text())

    def test_mqtt_paths_scopes_and_duplicate_uplinks(self):
        messages = []
        for route in range(4):
            for page in self.pages:
                wire = bytes([0x3c | route]) + (bytes(4) if route in (0, 3) else b"")
                wire += bytes([0x42]) + bytes.fromhex("1234abcd") + page
                message = dict(type="PACKET", direction="rx", raw=wire.hex())
                self.assertEqual(report.mqtt_payload(message), page)
                messages.extend([message, message])
        decoded = report.mqtt_reports(messages, "management test password")
        self.assertEqual(len(decoded), 1)
        self.assertEqual(len(decoded[0]["acl"]), 36)
        self.assertIsNone(report.mqtt_payload(dict(type="PACKET", direction="tx", raw=wire.hex())))
        self.assertIsNone(report.mqtt_payload(dict(type="PACKET", direction="rx", raw="3dc0")))
        self.assertEqual(report.mqtt_reports(messages[:2], "management test password"), [])
        for page in self.pages:
            padded = page + bytes(3 + ((len(page) - 3 + 15) // 16) * 16 - len(page))
            message = dict(type="PACKET", direction="rx", raw=(b"\x19\x00" + padded).hex())
            self.assertEqual(report.mqtt_payload(message), page)
            malformed = padded[:-1] + b"\x01" if len(padded) > len(page) else padded + b"\x00"
            message["raw"] = (b"\x19\x00" + malformed).hex()
            self.assertIsNone(report.mqtt_payload(message))


if __name__ == "__main__": unittest.main()
