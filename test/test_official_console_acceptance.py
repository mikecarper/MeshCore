"""Offline owned-CDP startup regressions; never open a browser or serial port."""
from contextlib import contextmanager
import importlib.util
import io
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HIL = Path(__file__).resolve().parents[1] / "tools/hil"
sys.path.insert(0, str(HIL))
spec = importlib.util.spec_from_file_location("official_acceptance", HIL / "official_console_acceptance.py")
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


class UsbLoggingReplyTests(unittest.TestCase):
    PRIMARY = "primary USB serial port (tty/COM name is host-assigned)"
    DEDICATED = "dedicated USB CDC 1, interface 02 (Linux: *-if02; tty/COM name is host-assigned)"

    def test_infrastructure_and_exact_companion_getters_preserve_each_mode(self):
        for mode in ("off", "on", "stream"):
            replies = ("> " + mode,
                       f"usb.logging {mode}; port: {self.PRIMARY}",
                       f"usb.logging {mode}; port: {self.DEDICATED}")
            for reply in replies:
                with self.subTest(mode=mode, reply=reply):
                    self.assertEqual(acceptance.usb_logging_mode(reply), mode)
                    acceptance.require_usb_logging_mode(reply, mode, "mode_mismatch")

    def test_wrong_live_mode_remains_a_failure_for_initial_and_reopen_checks(self):
        for error_code in ("logging_mode_must_be_preconfigured", "logging_mode_changed"):
            for actual in ("on", "stream"):
                for reply in ("> " + actual, f"usb.logging {actual}; port: {self.PRIMARY}"):
                    with self.subTest(error_code=error_code, actual=actual), \
                            self.assertRaisesRegex(RuntimeError, "^" + error_code + "$"):
                        acceptance.require_usb_logging_mode(reply, "off", error_code)

    def test_malformed_pending_or_unrelated_replies_are_rejected_without_echoing_input(self):
        invalid = (None, "", "off", "> OFF", "> off trailing", "OK - USB logging off (saved)",
                   "usb.logging off", "usb.logging off; port: unknown private endpoint",
                   f"usb.logging off; port: {self.PRIMARY} (reboot required to change USB interfaces)",
                   f"usb.logging off; port: {self.PRIMARY}\n> on",
                   f"usb.logging off; port: {self.PRIMARY} ",
                   f"> usb.logging off; port: {self.PRIMARY}")
        for reply in invalid:
            with self.subTest(reply=reply), \
                    self.assertRaisesRegex(RuntimeError, "^usb_logging_reply_format_unrecognized$"):
                acceptance.usb_logging_mode(reply)

    def test_companion_port_descriptions_match_the_shipping_source(self):
        root = HIL.parents[1]
        source = (root / "src/helpers/UsbLogging.cpp").read_text()
        companion = (root / "examples/companion_radio/MyMesh.cpp").read_text()
        self.assertIn('return "' + self.PRIMARY + '";', source)
        self.assertIn('return "' + self.DEDICATED + '";', source)
        self.assertIn('"usb.logging %s; port: %s%s"', companion)


class OwnedStartupTests(unittest.TestCase):
    def fixture(self, *, fail_first_page=False, wrong_profile=False, pages=1):
        requests, sockets, connections = [], [], []
        fail = [fail_first_page]
        profile = Path("/private/owned-profile")
        arguments = ["--user-data-dir=" + str(profile), "--remote-debugging-port=12345"]
        if wrong_profile:
            arguments[0] = "--user-data-dir=/private/another-browser"

        class Socket:
            def __init__(self, browser):
                self.browser, self.closed, self.queue = browser, False, []
                sockets.append(self)

            def send(self, text):
                request = json.loads(text)
                if self.browser:
                    # Browser events must not be mistaken for the command reply.
                    self.queue.extend([{"method": "Target.targetCreated"},
                                       {"id": request["id"], "result": {"arguments": arguments}}])
                else:
                    if fail[0]:
                        fail[0] = False
                        raise OSError("renderer connection interrupted")
                    self.queue.append({"id": request["id"], "result": {}})

            def recv(self):
                return json.dumps(self.queue.pop(0))

            def settimeout(self, timeout):
                self.timeout = timeout

            def close(self):
                self.closed = True

        def connect(url, **kwargs):
            connections.append((url, kwargs))
            return Socket("/browser/" in url)

        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                if request.endswith("/json/version"):
                    value = {"webSocketDebuggerUrl": "ws://127.0.0.1:12345/browser/owned", "Browser": "test Chromium"}
                elif request.endswith("/json/list"):
                    value = [{"type": "page", "url": "about:blank",
                              "webSocketDebuggerUrl": f"ws://127.0.0.1:12345/page/{i}"} for i in range(pages)]
                else:
                    raise AssertionError("Startup must reuse its initial page, never PUT /json/new")
                return io.StringIO(json.dumps(value))

        websocket = SimpleNamespace(create_connection=connect,
                                    WebSocketTimeoutException=type("SocketTimeout", (Exception,), {}),
                                    WebSocketConnectionClosedException=type("SocketClosed", (Exception,), {}))
        return profile, websocket, Opener(), requests, sockets, connections

    def test_reuses_initial_page_and_matches_owned_reply_after_event(self):
        profile, websocket, opener, requests, sockets, connections = self.fixture()
        with patch.object(acceptance.urllib.request, "build_opener", return_value=opener):
            page = acceptance.CDP(websocket, 12345, profile)
        self.assertEqual(requests, ["http://127.0.0.1:12345/json/version", "http://127.0.0.1:12345/json/list"])
        self.assertEqual(page.browser_version, "test Chromium")
        self.assertTrue(sockets[0].closed)
        self.assertFalse(sockets[1].closed)
        for _, kwargs in connections:
            self.assertEqual(kwargs["http_no_proxy"], ["127.0.0.1", "localhost"])
        page.socket.close()

    def test_partial_page_socket_is_closed_and_retry_creates_no_new_target(self):
        profile, websocket, opener, requests, sockets, _ = self.fixture(fail_first_page=True)
        browser = SimpleNamespace(poll=lambda: None)
        report = {}
        with patch.object(acceptance.urllib.request, "build_opener", return_value=opener), \
                patch.object(acceptance.time, "sleep"):
            page = acceptance.wait_for_owned_browser(websocket, 12345, profile, browser, report)
        self.assertEqual(report["browser_startup"]["attempts"], 2)
        self.assertEqual(report["browser_startup"]["last_phase"], "initial_page_enable")
        self.assertTrue(sockets[0].closed and sockets[1].closed and sockets[2].closed)
        self.assertFalse(sockets[3].closed)
        self.assertEqual(sum(request.endswith("/json/list") for request in requests), 2)
        self.assertTrue(all(isinstance(request, str) for request in requests))
        page.socket.close()

    def test_another_owned_profile_is_rejected_before_page_attach(self):
        profile, websocket, opener, requests, sockets, _ = self.fixture(wrong_profile=True)
        with patch.object(acceptance.urllib.request, "build_opener", return_value=opener), \
                self.assertRaisesRegex(RuntimeError, "does_not_match_owned_profile"):
            acceptance.CDP(websocket, 12345, profile)
        self.assertEqual(len(requests), 1)
        self.assertTrue(sockets[0].closed)

    def test_extra_pages_are_rejected_without_adding_another_page(self):
        profile, websocket, opener, _, sockets, _ = self.fixture(pages=2)
        with patch.object(acceptance.urllib.request, "build_opener", return_value=opener), \
                self.assertRaisesRegex(RuntimeError, "one_initial_blank_page"):
            acceptance.CDP(websocket, 12345, profile)
        self.assertEqual(len(sockets), 1)
        self.assertTrue(sockets[0].closed)

    def test_startup_deadline_preserves_phase_without_arbitrary_exception_text(self):
        _, websocket, _, _, _, _ = self.fixture()
        error = OSError("private host details")
        error.startup_phase = "version_endpoint"
        report = {}
        with patch.object(acceptance, "CDP", side_effect=error), \
                patch.object(acceptance.time, "monotonic", side_effect=[0, 0, .001, .02]), \
                patch.object(acceptance.time, "sleep"), \
                self.assertRaisesRegex(RuntimeError, "owned_Chromium_startup_deadline"):
            acceptance.wait_for_owned_browser(websocket, 12345, Path("/owned"),
                                              SimpleNamespace(poll=lambda: None), report, timeout=.01)
        self.assertEqual(report["browser_startup"]["attempts"], 2)
        self.assertEqual(report["browser_startup"]["last_phase"], "version_endpoint")
        self.assertNotIn("private host details", json.dumps(report))


class OwnedCleanupTests(unittest.TestCase):
    def test_termination_unwinds_and_restores_original_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        unwound = []
        with self.assertRaises(acceptance.NativeConsoleTerminated):
            with acceptance.cleanup_on_sigterm():
                try:
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                finally:
                    unwound.append(True)
                    self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)
        self.assertEqual(unwound, [True])
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_normal_exit_restores_original_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        with acceptance.cleanup_on_sigterm():
            self.assertTrue(callable(signal.getsignal(signal.SIGTERM)))
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_socket_and_stream_failure_still_closes_only_owned_browser(self):
        from unittest.mock import Mock
        page = SimpleNamespace(evaluate=Mock(side_effect=RuntimeError("private stream details")),
                               socket=SimpleNamespace(close=Mock(side_effect=OSError("private socket details"))))
        browser = SimpleNamespace(pid=123456, poll=Mock(side_effect=[None, 0]), wait=Mock())
        report = {}
        with patch.object(acceptance.os, "killpg") as kill:
            acceptance.close_owned_browser(page, browser, report)
        kill.assert_called_once_with(123456, signal.SIGTERM)
        browser.wait.assert_called_once_with(timeout=10)
        self.assertEqual(report, {"browser_stream_cleanup_error": True,
                                  "browser_socket_cleanup_error": True, "owned_browser_closed": True})
        self.assertNotIn("private", json.dumps(report))

    def test_interrupted_stream_cleanup_still_closes_owned_browser(self):
        from unittest.mock import Mock
        page = SimpleNamespace(evaluate=Mock(side_effect=acceptance.NativeConsoleTerminated()),
                               socket=SimpleNamespace(close=Mock()))
        browser = SimpleNamespace(pid=123456, poll=Mock(side_effect=[None, 0]), wait=Mock())
        with patch.object(acceptance.os, "killpg") as kill, \
                self.assertRaises(acceptance.NativeConsoleTerminated):
            acceptance.close_owned_browser(page, browser, {})
        page.socket.close.assert_called_once_with()
        kill.assert_called_once_with(123456, signal.SIGTERM)

    def test_unresponsive_owned_browser_uses_bounded_kill_fallback(self):
        from unittest.mock import Mock, call
        browser = SimpleNamespace(pid=123456, poll=Mock(side_effect=[None, -9]),
                                  wait=Mock(side_effect=[subprocess.TimeoutExpired("owned", 10), None]))
        report = {}
        with patch.object(acceptance.os, "killpg") as kill:
            acceptance.close_owned_browser(None, browser, report)
        self.assertEqual(kill.call_args_list, [call(123456, signal.SIGTERM), call(123456, signal.SIGKILL)])
        self.assertEqual(browser.wait.call_args_list, [call(timeout=10), call(timeout=5)])
        self.assertTrue(report["owned_browser_closed"])

    def test_termination_produces_private_failure_report_and_143(self):
        def interrupted(_args, report):
            try:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            finally:
                report["mock_owned_resources_unwound"] = True

        previous = signal.getsignal(signal.SIGTERM)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            work.chmod(0o700)
            output = work / "result.json"
            with patch.object(acceptance, "execute", side_effect=interrupted), \
                    patch("sys.stdout", new=io.StringIO()):
                status = acceptance.main(["--serial", "exact", "--expected-board", "board",
                    "--expected-version", "version", "--logging", "off", "--console-source", "/unused",
                    "--artifact-manifest", "/unused", "--output", str(output),
                    "--work-dir", str(work), "--execute"])
            report = json.loads(output.read_text())
            self.assertEqual(status, 143)
            self.assertFalse(report["ok"])
            self.assertEqual(report["termination_signal"], "SIGTERM")
            self.assertTrue(report["mock_owned_resources_unwound"])
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)


class PrimaryFailureCleanupTests(unittest.TestCase):
    def main_with(self, execute):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            work.chmod(0o700)
            output = work / "result.json"
            stdout = io.StringIO()
            with patch.object(acceptance, "execute", side_effect=execute), \
                    patch("sys.stdout", new=stdout):
                status = acceptance.main(["--serial", "exact", "--expected-board", "board",
                    "--expected-version", "version", "--logging", "on", "--console-source", "/unused",
                    "--artifact-manifest", "/unused", "--output", str(output),
                    "--work-dir", str(work), "--execute"])
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            return status, json.loads(output.read_text()), stdout.getvalue()

    @staticmethod
    @contextmanager
    def disappeared_tty(_port):
        try:
            yield {"vmin_during_test": 1, "vtime_during_test": 0}
        finally:
            raise FileNotFoundError("private missing /dev/ttyACM-secret")

    def test_idle_disconnect_survives_missing_tty_cleanup_and_main_report(self):
        original = acceptance.NativeConsoleJavaScriptError({"exception": {
            "className": "Error", "description": "Error: console_disconnected_while_idle\nprivate stack"}})

        def disconnected(_args, report):
            report["phase"] = "uninterrupted_idle"
            with acceptance.recorded_linux_web_serial("/dev/inert", report):
                raise original

        with patch.object(acceptance, "prepared_linux_web_serial", self.disappeared_tty):
            status, report, stdout = self.main_with(disconnected)
        self.assertEqual(status, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["error_type"], "NativeConsoleJavaScriptError")
        self.assertEqual(report["error"], "native_console_javascript_failed:console_disconnected_while_idle")
        self.assertEqual(report["primary_failure"]["phase"], "uninterrupted_idle")
        self.assertEqual(report["TTY_cleanup_failure"]["class"], "FileNotFoundError")
        self.assertIn("message_sha256", report["TTY_cleanup_failure"])
        self.assertEqual(report["TTY_cleanup_endpoint"], "missing")
        self.assertFalse(report["original_TTY_restored"])
        self.assertNotIn("private", json.dumps(report) + stdout)

    def test_cleanup_failure_after_successful_body_cannot_pass(self):
        def completed(_args, report):
            report["phase"] = "complete"
            with acceptance.recorded_linux_web_serial("/dev/inert", report):
                report["native_streams_closed"] = True

        with patch.object(acceptance, "prepared_linux_web_serial", self.disappeared_tty):
            status, report, _stdout = self.main_with(completed)
        self.assertEqual(status, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["error_type"], "FileNotFoundError")
        self.assertNotIn("primary_failure", report)
        self.assertFalse(report["original_TTY_restored"])

    def test_browser_and_tty_cleanup_failures_cannot_replace_original_transport_error(self):
        original = TimeoutError("private original transport deadline")
        report = {"phase": "uninterrupted_idle"}

        def browser_cleanup():
            raise OSError("private browser cleanup error")

        with patch.object(acceptance, "prepared_linux_web_serial", self.disappeared_tty), \
                self.assertRaises(TimeoutError) as caught:
            with acceptance.recorded_linux_web_serial("/dev/inert", report):
                with acceptance.preserving_cleanup_failure(report, "browser_cleanup_failure", browser_cleanup):
                    raise original
        self.assertIs(caught.exception, original)
        self.assertEqual(report["primary_failure"]["class"], "TimeoutError")
        self.assertEqual(report["browser_cleanup_failure"]["class"], "OSError")
        self.assertEqual(report["TTY_cleanup_failure"]["class"], "FileNotFoundError")
        self.assertNotIn("private", json.dumps(report))

    def test_replaced_tty_is_not_modified_or_reported_as_restored(self):
        # Real Linux pseudoterminals exercise the unchanged identity guard;
        # no physical serial device or browser is opened.
        import os
        import pty
        import termios
        import linux_web_serial as tty_helper
        master, slave = pty.openpty()
        other_master, other_slave = pty.openpty()
        try:
            path, other_path = os.ttyname(slave), os.ttyname(other_slave)
            attributes = termios.tcgetattr(other_slave)
            attributes[6][termios.VTIME] = b"\x07"
            termios.tcsetattr(other_slave, termios.TCSANOW, attributes)
            original_other = termios.tcgetattr(other_slave)
            original_open = tty_helper._open_tty
            calls = []

            def replaced_open(selected):
                calls.append(selected)
                return original_open(selected if len(calls) == 1 else other_path)

            report = {"phase": "complete"}
            with patch.object(tty_helper, "_open_tty", side_effect=replaced_open), \
                    self.assertRaisesRegex(RuntimeError, "endpoint changed"):
                with acceptance.recorded_linux_web_serial(path, report):
                    pass
            self.assertEqual(termios.tcgetattr(other_slave), original_other)
            self.assertEqual(report["TTY_cleanup_endpoint"], "identity_changed")
            self.assertFalse(report["original_TTY_restored"])
            self.assertEqual(report["TTY_cleanup_failure"]["class"], "RuntimeError")
            self.assertNotIn("primary_failure", report)
        finally:
            for fd in (master, slave, other_master, other_slave):
                os.close(fd)

    def test_unknown_runtime_error_text_is_hashed_in_final_report(self):
        def failed(_args, _report):
            raise RuntimeError("private password from an external dependency")

        status, report, stdout = self.main_with(failed)
        self.assertEqual(status, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["error"], "hardware_acceptance_interrupted_or_failed")
        self.assertIn("message_sha256", report["failure"])
        self.assertNotIn("private", json.dumps(report) + stdout)


class SafeJavaScriptFailureTests(unittest.TestCase):
    def evaluate_error(self, details):
        page = object.__new__(acceptance.CDP)
        page.call = lambda *args, **kwargs: {"exceptionDetails": details}
        with self.assertRaises(acceptance.NativeConsoleJavaScriptError) as caught:
            page.evaluate("hil.query('board')")
        return caught.exception

    def test_page_literal_error_is_reported_without_stack_or_description(self):
        error = self.evaluate_error({"text": "Uncaught (in promise)", "exception": {
            "className": "Error", "description": "Error: framed_reply_deadline\n at private-site/secret"}})
        self.assertEqual(error.safe_details, {"class": "Error", "code": "framed_reply_deadline"})
        self.assertEqual(str(error), "native_console_javascript_failed:framed_reply_deadline")
        self.assertNotIn("secret", json.dumps(error.safe_details))

    def test_dom_exception_name_is_safe_but_native_message_is_hashed(self):
        message = "NetworkError: private serial endpoint secret"
        error = self.evaluate_error({"exception": {"className": "DOMException", "description": message}})
        self.assertEqual(error.safe_details["class"], "DOMException")
        self.assertEqual(error.safe_details["dom_exception_name"], "NetworkError")
        self.assertEqual(error.safe_details["message_sha256"], acceptance.hashlib.sha256(message.encode()).hexdigest())
        self.assertNotIn("secret", str(error) + json.dumps(error.safe_details))

    def test_unknown_class_and_known_code_prefix_do_not_expose_arbitrary_text(self):
        message = "Error: framed_reply_deadline secret-password"
        error = self.evaluate_error({"exception": {"className": "private-class-secret", "description": message}})
        self.assertEqual(error.safe_details["class"], "unrecognized")
        self.assertEqual(error.safe_details["code"], "unclassified_exception")
        self.assertEqual(error.safe_details["message_sha256"], acceptance.hashlib.sha256(message.encode()).hexdigest())
        self.assertNotIn("secret", str(error) + json.dumps(error.safe_details))

    @unittest.skipUnless(shutil.which("node"), "Node required for offline PAGE protocol test")
    def test_companion_handshakes_reopen_and_logging_gate_without_idle_writes(self):
        script = acceptance.PAGE.split('<script type="module">', 1)[1].split('</script>', 1)[0]
        script = script.replace("import {SerialConsole} from './console.js';", "")
        setup = r'''
import assert from 'node:assert/strict';
globalThis.window=globalThis;
Object.defineProperty(globalThis,'navigator',{value:{serial:{getPorts:async()=>[
  {getInfo:()=>({usbVendorId:0x303a,usbProductId:0x1001})}
]}}});
const realTimeout=globalThis.setTimeout;
globalThis.setTimeout=(fn,ms)=>realTimeout(fn,[10000,5000].includes(ms)?5:ms);
let mode='binary',dropStopAck=false,closed=0;
const sent=[];
class SerialConsole {
  constructor(port){this.port=port;this.connected=false;}
  async connect(){this.connected=true;}
  async disconnect(){this.connected=false;++closed;}
  async reset(){throw Error('reset_must_never_be_called');}
  async sendCommand(command){
    sent.push(command);
    if(command==='+++MESHCORE-TERM-START') {mode='ascii';return;}
    if(command==='+++MESHCORE-TERM-STOP') {
      assert.equal(mode,'ascii');mode='binary';
      if(!dropStopAck)this.onOutput('OK - Binary mode\n');
      return;
    }
    if(mode!=='ascii')return;
    this.onOutput('> '+command+'\n');this.onOutput('  -> bench reply\n');
  }
}
'''
        checks = r'''
// Direct remains inert and cannot accidentally make a binary Companion ASCII.
await hil.open(0x303a,0x1001);
assert.deepEqual(sent,[]);
await assert.rejects(hil.query('board'),/framed_reply_deadline/);
await hil.close();
const start=sent.length;
for(let open=0;open<2;++open){
  await hil.open(0x303a,0x1001,'companion');
  assert.equal(sent.at(-1),'+++MESHCORE-TERM-START');
  assert.equal((await hil.query('board')).body,'bench reply');
  hil.loggingMode='off'; // Actual Python path sets this from the strict getter.
  const writes=hil.writes,setup=hil.setup_writes;
  await hil.idle(0);
  assert.equal(hil.writes,writes);assert.equal(hil.setup_writes,setup);
  const result=await hil.close();
  assert.equal(result.binary_mode_restored,true);assert.equal(mode,'binary');
}
assert.deepEqual(sent.slice(start),[
 '+++MESHCORE-TERM-START','board','+++MESHCORE-TERM-STOP',
 '+++MESHCORE-TERM-START','board','+++MESHCORE-TERM-STOP']);
assert.equal(hil.setup_writes,4);
await hil.open(0x303a,0x1001,'companion');
hil.loggingMode='on';
const before=sent.length;
assert.equal((await hil.close()).binary_mode_restored,false);
assert.equal(sent.length,before);assert.equal(mode,'ascii');
// Missing ACK is failure, but cannot prevent owned native streams closing.
await hil.open(0x303a,0x1001,'companion');
hil.loggingMode='off';dropStopAck=true;
const closedBefore=closed;
await assert.rejects(hil.close(),/terminal_mode_exit_deadline/);
assert.equal(closed,closedBefore+1);assert.equal(hil.client,null);
'''
        ran = subprocess.run([shutil.which("node"), "--input-type=module", "-"],
                             input=setup + script + checks, capture_output=True, text=True, timeout=10)
        self.assertEqual(ran.returncode, 0, ran.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node required for offline PAGE protocol test")
    def test_page_accepts_exact_prompt_echo_and_requires_complete_response(self):
        # Execute the actual PAGE module body with an inert SDK-shaped class.
        # navigator.serial returns a plain object; no browser/device is opened.
        script = acceptance.PAGE.split('<script type="module">', 1)[1].split('</script>', 1)[0]
        self.assertIn("import {SerialConsole} from './console.js';", script)
        script = script.replace("import {SerialConsole} from './console.js';", "")
        setup = r'''
import assert from 'node:assert/strict';
globalThis.window=globalThis;
Object.defineProperty(globalThis,'navigator',{value:{serial:{getPorts:async()=>[
  {getInfo:()=>({usbVendorId:0x303a,usbProductId:0x1001})}
]}}});
const realTimeout=globalThis.setTimeout;
// Only test deadlines are shortened; idle remains a no-transmit timer.
globalThis.setTimeout=(fn,ms)=>realTimeout(fn,ms===10000?5:ms);
let echoPrefix='> ', complete=true;
class SerialConsole {
  constructor(port){this.port=port;this.connected=false;}
  async connect(){this.connected=true;}
  async disconnect(){this.connected=false;}
  async reset(){throw Error('reset_must_never_be_called');}
  async sendCommand(command){
    this.onOutput(echoPrefix+command+'\n');
    this.onOutput('  -> bench reply'+(complete?'\n':''));
  }
}
'''
        checks = r'''
await hil.open(0x303a,0x1001);
for(const prefix of ['> ','>','']){
  echoPrefix=prefix;
  const result=await hil.query('board');
  assert.equal(result.body,'bench reply');
  assert.equal(result.complete_CRLF_reply,true);
}
const writes=hil.writes;
await hil.idle(0);
assert.equal(hil.writes,writes);
echoPrefix='> stale ';
await assert.rejects(hil.query('board'),/framed_reply_deadline/);
echoPrefix='> ';complete=false;
await assert.rejects(hil.query('board'),/framed_reply_deadline/);
await hil.close();
'''
        ran = subprocess.run([shutil.which("node"), "--input-type=module", "-"],
                             input=setup + script + checks, capture_output=True, text=True, timeout=10)
        self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
