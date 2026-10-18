"""Offline owned-CDP startup regressions; never open a browser or serial port."""
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
