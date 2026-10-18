#!/usr/bin/env python3
"""Run the pinned official console through Chromium's native Linux Web Serial.

This explicitly executed hardware test only reads infrastructure CLI commands.
Configure the requested logging mode separately. It never resets the device,
changes firmware settings, adds keepalives, or substitutes a serial transport.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from linux_web_serial import prepared_linux_web_serial

CONSOLE_COMMIT = "45a5257bf792b11a83f4c51c18d4f312addfb677"
CONSOLE_SHA256 = "107d3ad867915aea8b2454c324327397a0f9e2f0469656046054011bc64475f9"
CONSOLE_BYTES = 2455

PAGE = r'''<!doctype html><meta charset="utf-8"><title>Native official console acceptance</title>
<script type="module">
import {SerialConsole} from './console.js';
window.hil = {ready:false, client:null, task:null, writes:0, replies:0,
              setup_writes:0, pending:null, controlPending:null, disconnected:false,
              terminalMode:'direct', loggingMode:null};
hil.open = async function(vid,pid,terminalMode='direct') {
  if(!['direct','companion'].includes(terminalMode)) throw Error('invalid_terminal_mode');
  hil.terminalMode=terminalMode;hil.loggingMode=null;
  const ports=await navigator.serial.getPorts();
  if(ports.length!==1) throw Error('exactly_one_pregranted_native_port_required');
  const info=ports[0].getInfo();
  if(info.usbVendorId!==vid||info.usbProductId!==pid) throw Error('native_USB_ID_mismatch');
  hil.client=new SerialConsole(ports[0]);
  hil.client.onOutput=text=>{
    if(text.includes('*** Terminal disconnected')) hil.disconnected=true;
    // A terminal prompt and the subsequent input echo share a CRLF line.
    // Accept that exact prefix as well as the bare echo after a fresh open.
    const line=text.replace(/\r?\n$/, '');
    if(hil.controlPending && line==='OK - Binary mode' && text.endsWith('\n')) {
      const waiter=hil.controlPending;hil.controlPending=null;
      clearTimeout(waiter.timer);waiter.resolve();
    }
    if(hil.pending && (line===hil.pending.command || line==='> '+hil.pending.command ||
                       line==='>'+hil.pending.command)) hil.pending.echo_seen=true;
    // The unmodified official CRLF transformer delivers these complete lines.
    // Raw console output stays in RAM and is never logged or saved.
    if(hil.pending?.echo_seen && text.startsWith('  -> ') && text.endsWith('\n')) {
      const waiter=hil.pending; hil.pending=null; ++hil.replies;
      clearTimeout(waiter.timer); waiter.resolve(text.slice(5).trim());
    }
  };
  hil.task=hil.client.connect();
  const end=performance.now()+10000;
  while(!hil.client.connected) {
    if(hil.disconnected||performance.now()>end) throw Error('native_console_open_failed');
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  if(terminalMode==='companion') {
    ++hil.writes;++hil.setup_writes;
    // The shipping binary parser consumes this exact CRLF control sequence
    // and leaves following bytes buffered for its ASCII terminal consumer.
    await hil.client.sendCommand('+++MESHCORE-TERM-START');
  }
  return {native_ports:ports.length, connected:hil.client.connected};
};
hil.query = async function(command) {
  if(hil.pending||!hil.client.connected||hil.disconnected) throw Error('console_not_ready');
  const start=performance.now();
  const reply=new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{hil.pending=null;reject(Error('framed_reply_deadline'));},10000);
    hil.pending={resolve,reject,timer,command,echo_seen:false};
  });
  ++hil.writes;
  try { await hil.client.sendCommand(command); }
  catch(error) { if(hil.pending) {clearTimeout(hil.pending.timer);hil.pending=null;} throw error; }
  const body=await reply;
  return {body,elapsed_ms:performance.now()-start,complete_CRLF_reply:true};
};
hil.idle = async function(seconds) {
  const before=hil.writes,start=performance.now();
  await new Promise(resolve=>setTimeout(resolve,seconds*1000));
  if(!hil.client.connected||hil.disconnected) throw Error('console_disconnected_while_idle');
  if(hil.writes!==before) throw Error('unexpected_command_during_idle');
  return {seconds:(performance.now()-start)/1000,command_writes:hil.writes-before,
          connected:hil.client.connected};
};
hil.close = async function() {
  let binary_mode_restored=false;
  try {
    // Only a successfully queried logging-OFF Companion can return to binary.
    // A primary logging-ON stream must remain an ASCII logging terminal.
    if(hil.client?.connected && hil.terminalMode==='companion' && hil.loggingMode==='off') {
      const confirmed=new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>{hil.controlPending=null;reject(Error('terminal_mode_exit_deadline'));},5000);
        hil.controlPending={resolve,reject,timer};
      });
      ++hil.writes;++hil.setup_writes;
      try {await hil.client.sendCommand('+++MESHCORE-TERM-STOP');}
      catch(error) {
        if(hil.controlPending){clearTimeout(hil.controlPending.timer);hil.controlPending=null;}
        throw error;
      }
      await confirmed;binary_mode_restored=true;
    }
  } finally {
    try {if(hil.client) {await hil.client.disconnect();await hil.task;}}
    finally {hil.client=null;hil.task=null;hil.disconnected=false;}
  }
  return {native_streams_closed:true,binary_mode_restored};
};
hil.ready=true;
</script>'''

PAGE_ERROR_CODES = frozenset(re.findall(r"\bError\('([A-Za-z0-9_]+)'\)", PAGE))
JS_ERROR_CLASSES = frozenset({"Error", "TypeError", "RangeError", "ReferenceError",
                             "SyntaxError", "EvalError", "URIError", "AggregateError", "DOMException"})
DOM_EXCEPTION_NAMES = frozenset({
    "AbortError", "DataError", "EncodingError", "HierarchyRequestError", "IndexSizeError",
    "InvalidAccessError", "InvalidCharacterError", "InvalidModificationError", "InvalidStateError",
    "NetworkError", "NotAllowedError", "NotFoundError", "NotReadableError", "NotSupportedError",
    "OperationError", "QuotaExceededError", "ReadOnlyError", "SecurityError", "SyntaxError",
    "TimeoutError", "TransactionInactiveError", "UnknownError", "VersionError", "WrongDocumentError",
})


def safe_javascript_exception(details):
    """Reduce a CDP exception to fixed names/codes and hashes, never its text.

    Native messages and JS stacks can contain host paths, node output or
    credentials. Even a description beginning with a known code is not safe
    unless the entire first line is that exact PAGE literal error.
    """
    if not isinstance(details, dict):
        details = {}
    exception = details.get("exception", {})
    if not isinstance(exception, dict):
        exception = {}
    description = exception.get("description")
    if not isinstance(description, str):
        description = exception.get("value")
    if not isinstance(description, str):
        description = details.get("text", "")
    if not isinstance(description, str):
        description = ""
    first_line = description.splitlines()[0] if description else ""
    class_name = exception.get("className")
    safe = {"class": class_name if isinstance(class_name, str)
            and class_name in JS_ERROR_CLASSES | DOM_EXCEPTION_NAMES else "unrecognized",
            "code": "unclassified_exception"}
    message = first_line.removeprefix("Error: ")
    if message in PAGE_ERROR_CODES:
        safe["code"] = message
    else:
        safe["message_sha256"] = hashlib.sha256(first_line.encode("utf-8", errors="replace")).hexdigest()
    name = first_line.split(":", 1)[0]
    if name in DOM_EXCEPTION_NAMES:
        safe["dom_exception_name"] = name
    return safe


class NativeConsoleJavaScriptError(RuntimeError):
    def __init__(self, details):
        self.safe_details = safe_javascript_exception(details)
        super().__init__("native_console_javascript_failed:" + self.safe_details["code"])


class NativeConsoleTerminated(KeyboardInterrupt):
    """Unwind the owned resources and report a normal SIGTERM interruption."""


NATIVE_ERROR_CODES = frozenset({
    "CDP_browser_does_not_match_owned_profile", "CDP_request_failed", "CDP_request_deadline",
    "USB_endpoint_changed_before_browser", "USB_identity_or_ownership_changed_after_cleanup",
    "USB_vendor_product_ID_required", "complete_CRLF_reply_required",
    "exact_USB_serial_requires_one_primary_interface", "existing_private_work_directory_required",
    "firmware_command_rejected", "logging_mode_changed", "logging_mode_must_be_preconfigured",
    "native_stream_cleanup_failed", "official_console_source_pin_mismatch",
    "official_module_did_not_load", "owned_Chromium_exited_before_ready",
    "owned_Chromium_startup_deadline", "owned_browser_requires_one_initial_blank_page",
    "reopen_reset_or_uptime_regression", "runtime_board_mismatch",
    "runtime_identity_changed_on_reopen", "runtime_version_mismatch",
    "selected_serial_port_must_be_unowned", "uninterrupted_no_transmit_idle_required",
    "uptime_delta_indicates_reset_or_stall", "usb_logging_reply_format_unrecognized",
    "work_directory_must_be_private_and_owned",
})
NATIVE_ERROR_CLASSES = frozenset({
    "RuntimeError", "OSError", "FileNotFoundError", "PermissionError", "TimeoutError",
    "ConnectionError", "ConnectionResetError", "BrokenPipeError", "ValueError",
    "KeyboardInterrupt", "NativeConsoleTerminated", "NativeConsoleJavaScriptError",
})


def safe_native_failure(error, phase):
    """Keep controlled codes; hash arbitrary transport, TTY and exception text."""
    name = type(error).__name__
    safe = {"class": name if name in NATIVE_ERROR_CLASSES else "unrecognized",
            "phase": phase, "code": "hardware_acceptance_interrupted_or_failed"}
    if isinstance(error, NativeConsoleJavaScriptError):
        safe["code"] = "native_console_javascript_failed:" + error.safe_details["code"]
        safe["javascript_exception"] = error.safe_details
    elif type(error) is RuntimeError and str(error) in NATIVE_ERROR_CODES:
        safe["code"] = str(error)
    else:
        safe["message_sha256"] = hashlib.sha256(
            str(error).encode("utf-8", errors="replace")).hexdigest()
    return safe


@contextmanager
def preserving_cleanup_failure(report, label, cleanup):
    """A cleanup failure must not replace the failed native trial's evidence."""
    primary = None
    try:
        yield
    except BaseException as error:
        primary = error
        report.setdefault("primary_failure", safe_native_failure(error, report.get("phase")))
        raise
    finally:
        try:
            cleanup()
        except BaseException as error:
            report[label] = safe_native_failure(error, report.get("phase"))
            if primary is None:
                raise


@contextmanager
def recorded_linux_web_serial(port, report):
    manager = prepared_linux_web_serial(port)
    tty = manager.__enter__()
    report["original_TTY_restored"] = False

    def restore():
        try:
            # This helper's exit only restores termios; the primary trial
            # exception is handled by the surrounding cleanup boundary.
            manager.__exit__(None, None, None)
        except FileNotFoundError:
            report["TTY_cleanup_endpoint"] = "missing"
            raise
        except RuntimeError as error:
            if str(error) == "TTY endpoint changed; original attributes were not applied":
                report["TTY_cleanup_endpoint"] = "identity_changed"
            raise
        report["original_TTY_restored"] = True

    with preserving_cleanup_failure(report, "TTY_cleanup_failure", restore):
        yield tty


@contextmanager
def cleanup_on_sigterm():
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(signum, _frame):
        # A repeated TERM must not interrupt the bounded cleanup already in
        # progress. The caller's original handler is restored on every exit.
        signal.signal(signum, signal.SIG_IGN)
        raise NativeConsoleTerminated()

    signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def usb_logging_mode(reply):
    """Normalize the exact infrastructure and Companion getter replies.

    A pending interface restart is deliberately rejected: the requested live
    logging mode must already be configured before this read-only idle test.
    """
    if reply in ("> off", "> on", "> stream"):
        return reply[2:]
    ports = (
        "primary USB serial port (tty/COM name is host-assigned)",
        "dedicated USB CDC 1, interface 02 (Linux: *-if02; tty/COM name is host-assigned)",
    )
    if isinstance(reply, str):
        for mode in ("off", "on", "stream"):
            if any(reply == f"usb.logging {mode}; port: {port}" for port in ports):
                return mode
    raise RuntimeError("usb_logging_reply_format_unrecognized")


def require_usb_logging_mode(reply, expected, error_code):
    require(usb_logging_mode(reply) == expected, error_code)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass


class CDP:
    def __init__(self, websocket, debug_port, profile):
        self.websocket = websocket
        self.socket = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        phase = "version_endpoint"
        try:
            with self.opener.open(f"http://127.0.0.1:{debug_port}/json/version", timeout=5) as response:
                version = json.load(response)
            phase = "browser_ownership"
            browser = websocket.create_connection(version["webSocketDebuggerUrl"], timeout=5,
                                                   suppress_origin=True, http_no_proxy=["127.0.0.1", "localhost"])
            try:
                browser.send(json.dumps({"id": 1, "method": "Browser.getBrowserCommandLine"}))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    result = json.loads(browser.recv())
                    if result.get("id") == 1:
                        break
                else:
                    raise TimeoutError("owned_browser_command_line_deadline")
                arguments = result.get("result", {}).get("arguments", [])
                require("--user-data-dir=" + str(profile) in arguments
                        and "--remote-debugging-port=" + str(debug_port) in arguments,
                        "CDP_browser_does_not_match_owned_profile")
            finally:
                browser.close()
            phase = "initial_page_endpoint"
            # The fresh owned browser already has one about:blank page. Reuse
            # it: PUT /json/new on every retry creates extra pages/renderer work
            # and can compound a startup failure on the small Pi.
            with self.opener.open(f"http://127.0.0.1:{debug_port}/json/list", timeout=5) as response:
                targets = json.load(response)
            pages = [target for target in targets if target.get("type") == "page"]
            if not pages:
                raise OSError("owned_initial_page_not_ready")
            require(len(pages) == 1 and pages[0].get("url") == "about:blank",
                    "owned_browser_requires_one_initial_blank_page")
            phase = "initial_page_connection"
            self.socket = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=5,
                                                      suppress_origin=True, http_no_proxy=["127.0.0.1", "localhost"])
            self.socket.settimeout(0.5)
            self.sequence = 0
            self.browser_version = version.get("Browser")
            phase = "initial_page_enable"
            self.call("Page.enable")
            self.call("Runtime.enable")
        except BaseException as error:
            # A failed constructor is never returned to execute()'s finally.
            # Close this partial connection here before any startup retry.
            if self.socket is not None:
                self.socket.close()
            error.startup_phase = phase
            raise

    def call(self, method, parameters=None, timeout=15):
        self.sequence += 1
        sequence = self.sequence
        self.socket.send(json.dumps({"id": sequence, "method": method, "params": parameters or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response = json.loads(self.socket.recv())
            except self.websocket.WebSocketTimeoutException:
                continue
            if response.get("id") == sequence:
                require("error" not in response, "CDP_request_failed")
                return response.get("result", {})
        raise RuntimeError("CDP_request_deadline")

    def evaluate(self, expression, timeout=15):
        response = self.call("Runtime.evaluate", {"expression": expression,
                            "returnByValue": True, "awaitPromise": True}, timeout)
        if "exceptionDetails" in response:
            raise NativeConsoleJavaScriptError(response["exceptionDetails"])
        return response.get("result", {}).get("value")


def wait_for_owned_browser(websocket, debug_port, profile, browser, report, timeout=60):
    startup = report["browser_startup"] = {"attempts": 0, "initial_page_reused": True}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require(browser.poll() is None, "owned_Chromium_exited_before_ready")
        startup["attempts"] += 1
        try:
            page = CDP(websocket, debug_port, profile)
            startup["ready"] = True
            return page
        except (OSError, urllib.error.URLError, websocket.WebSocketTimeoutException,
                websocket.WebSocketConnectionClosedException) as error:
            startup.update(last_error_type=type(error).__name__,
                           last_phase=getattr(error, "startup_phase", "unknown"))
            time.sleep(0.1)
    raise RuntimeError("owned_Chromium_startup_deadline")


def select_device(serial_number):
    from serial.tools import list_ports
    matches = []
    for info in list_ports.comports():
        if info.serial_number != serial_number:
            continue
        properties = subprocess.run(["udevadm", "info", "--query=property", "--name=" + info.device],
                                    check=True, capture_output=True, text=True).stdout
        values = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
        if values.get("ID_USB_INTERFACE_NUM") == "00":
            matches.append(info)
    require(len(matches) == 1, "exact_USB_serial_requires_one_primary_interface")
    device = matches[0]
    require(device.vid is not None and device.pid is not None, "USB_vendor_product_ID_required")
    ownership = subprocess.run(["sudo", "-n", "fuser", device.device], capture_output=True)
    require(ownership.returncode == 1 and not ownership.stdout and not ownership.stderr,
            "selected_serial_port_must_be_unowned")
    return device


def close_owned_browser(page, browser, report):
    try:
        if page is not None:
            try:
                page.evaluate("hil.client ? hil.close() : null")
            except Exception as error:
                report["browser_stream_cleanup_error"] = True
                if isinstance(error, NativeConsoleJavaScriptError):
                    report["browser_stream_cleanup_exception"] = error.safe_details
            finally:
                try:
                    page.socket.close()
                except Exception:
                    report["browser_socket_cleanup_error"] = True
    finally:
        # Stream/CDP failures must never skip termination of our process group.
        if browser is not None and browser.poll() is None:
            os.killpg(browser.pid, signal.SIGTERM)
            try:
                browser.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(browser.pid, signal.SIGKILL)
                browser.wait(timeout=5)
        report["owned_browser_closed"] = browser is None or browser.poll() is not None


def execute(args, report):
    if args.vendor:
        sys.path.insert(0, str(args.vendor.resolve()))
    import websocket

    source = args.console_source.read_bytes()
    require(len(source) == CONSOLE_BYTES and hashlib.sha256(source).hexdigest() == CONSOLE_SHA256,
            "official_console_source_pin_mismatch")
    report.update(console_commit=CONSOLE_COMMIT, console_sha256=CONSOLE_SHA256,
                  console_bytes=CONSOLE_BYTES, artifact_manifest_sha256=sha256(args.artifact_manifest),
                  expected_board_sha256=hashlib.sha256(args.expected_board.encode()).hexdigest(),
                  expected_version=args.expected_version, logging_expected=args.logging,
                  serial_identity_sha256=hashlib.sha256(args.serial.encode()).hexdigest())
    device = select_device(args.serial)
    work = Path(tempfile.mkdtemp(prefix="official-native-console-", dir=args.work_dir))
    work.chmod(0o700)
    report["private_state_directory"] = str(work)
    site = work / "site"
    site.mkdir()
    (site / "index.html").write_text(PAGE, encoding="utf-8")
    (site / "console.js").write_bytes(source)
    handler = lambda *values, **kw: QuietHandler(*values, directory=str(site), **kw)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = work / "profile"
    (profile / "Default").mkdir(parents=True)
    grant = {"name": device.product or args.expected_board, "vendor_id": device.vid,
             "product_id": device.pid, "serial_number": args.serial}
    preferences = {"profile": {"content_settings": {"exceptions": {"serial_chooser_data": {
        origin + ",*": {"setting": {"chosen-objects": [grant]}}}}}}}
    (profile / "Default/Preferences").write_text(json.dumps(preferences))
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        debug_port = reservation.getsockname()[1]
    browser = page = None
    browser_log = (work / "chromium-private.log").open("xb")
    try:
        with recorded_linux_web_serial(device.device, report) as tty:
            report["TTY_preflight"] = tty
            with preserving_cleanup_failure(report, "browser_cleanup_failure",
                    lambda: close_owned_browser(page, browser, report)):
                # Re-enumerate the exact UID and ownership after TTY preparation.
                require(select_device(args.serial).device == device.device, "USB_endpoint_changed_before_browser")
                browser = subprocess.Popen([str(args.chromium), "--headless=new", "--no-sandbox",
                    "--disable-dev-shm-usage", "--disable-gpu", "--disable-background-networking",
                    "--disable-extensions", "--enable-automation", "--renderer-process-limit=1",
                    "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=" + str(debug_port),
                    "--user-data-dir=" + str(profile), "about:blank"],
                    stdout=browser_log, stderr=browser_log, start_new_session=True)
                report["phase"] = "browser_startup"
                page = wait_for_owned_browser(websocket, debug_port, profile, browser, report)
                report["browser_version"] = page.browser_version
                report["phase"] = "official_module_startup"
                page.call("Page.navigate", {"url": origin + "/index.html"}, timeout=60)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if page.evaluate("!!window.hil?.ready"):
                        break
                    time.sleep(0.1)
                require(page.evaluate("!!window.hil?.ready"), "official_module_did_not_load")
                report["phase"] = "native_serial_open"
                page.evaluate(f"hil.open({device.vid},{device.pid},{json.dumps(args.terminal_mode)})")

                def query(command):
                    report["phase"] = "native_query:" + command
                    result = page.evaluate("hil.query(" + json.dumps(command) + ")")
                    require(result["complete_CRLF_reply"], "complete_CRLF_reply_required")
                    body = result["body"]
                    require(not body.startswith(("Error", "Unknown", "Unsupported")), "firmware_command_rejected")
                    return body, result["elapsed_ms"]

                board, _ = query("board")
                version, _ = query("ver")
                logging, _ = query("get usb.logging")
                page.evaluate("hil.loggingMode=" + json.dumps(usb_logging_mode(logging)))
                require(board == args.expected_board, "runtime_board_mismatch")
                require(version == args.expected_version, "runtime_version_mismatch")
                require_usb_logging_mode(logging, args.logging, "logging_mode_must_be_preconfigured")
                before, _ = query("stats-core")
                before = json.loads(before)
                report["before"] = before
                report["phase"] = "uninterrupted_idle"
                print(json.dumps({"phase": "native-console-idle-start", "seconds": args.idle_seconds}), flush=True)
                idle = page.evaluate(f"hil.idle({args.idle_seconds})", timeout=args.idle_seconds + 30)
                report["idle"] = idle
                require(idle["seconds"] >= args.idle_seconds and idle["command_writes"] == 0,
                        "uninterrupted_no_transmit_idle_required")
                after, elapsed = query("stats-core")
                after = json.loads(after)
                report.update(after=after, first_post_idle_reply_ms=elapsed)
                elapsed_uptime = after["uptime_secs"] - before["uptime_secs"]
                require(args.idle_seconds - 2 <= elapsed_uptime <= idle["seconds"] + 15,
                        "uptime_delta_indicates_reset_or_stall")
                report["idle_uptime_delta_seconds"] = elapsed_uptime
                report["phase"] = "native_serial_first_close"
                report["first_close"] = page.evaluate("hil.close()")
                report["phase"] = "native_serial_reopen"
                page.evaluate(f"hil.open({device.vid},{device.pid},{json.dumps(args.terminal_mode)})")
                reopened, elapsed = query("stats-core")
                reopened = json.loads(reopened)
                require(reopened["uptime_secs"] >= after["uptime_secs"], "reopen_reset_or_uptime_regression")
                require(query("board")[0] == args.expected_board and query("ver")[0] == args.expected_version,
                        "runtime_identity_changed_on_reopen")
                reopened_logging = query("get usb.logging")[0]
                page.evaluate("hil.loggingMode=" + json.dumps(usb_logging_mode(reopened_logging)))
                require_usb_logging_mode(reopened_logging, args.logging, "logging_mode_changed")
                report.update(reopened=reopened, reopened_reply_ms=elapsed,
                              command_writes=page.evaluate("hil.writes"), complete_CRLF_replies=page.evaluate("hil.replies"))
                report["phase"] = "native_serial_final_close"
                report["final_close"] = page.evaluate("hil.close()")
                report.update(command_writes=page.evaluate("hil.writes"),
                              setup_writes=page.evaluate("hil.setup_writes"))
                report["native_streams_closed"] = True
                report["phase"] = "complete"
        require(select_device(args.serial).device == device.device, "USB_identity_or_ownership_changed_after_cleanup")
        require(not (report.get("browser_stream_cleanup_error") or report.get("browser_socket_cleanup_error")),
                "native_stream_cleanup_failed")
    finally:
        browser_log.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--expected-board", required=True)
    parser.add_argument("--expected-version", required=True, help="Exact full ver reply, including Build suffix")
    parser.add_argument("--logging", choices=("on", "off"), required=True)
    parser.add_argument("--terminal-mode", choices=("direct", "companion"), default="direct",
                        help="Companion sends the shipping TERM-START on every open and TERM-STOP on logging-OFF close")
    parser.add_argument("--idle-seconds", type=int, default=600)
    parser.add_argument("--console-source", type=Path, required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--chromium", type=Path, default=Path("/usr/bin/chromium"))
    parser.add_argument("--vendor", type=Path, help="Optional existing pySerial/websocket-client package directory")
    parser.add_argument("--execute", action="store_true", help="Explicitly open the selected native serial port")
    args = parser.parse_args(argv)
    if not 1 <= args.idle_seconds <= 3600:
        parser.error("idle seconds must be between 1 and 3600")
    if not args.serial or any(value in args.serial for value in "\r\n\0"):
        parser.error("serial must be an exact single-line USB identity")
    if not args.execute:
        print(json.dumps({"execute": False, "idle_seconds": args.idle_seconds,
                          "terminal_mode": args.terminal_mode,
                          "Companion_handoff_writes_outside_idle": args.terminal_mode == "companion",
                          "console_sha256": CONSOLE_SHA256, "native_WebSerial": True,
                          "commands": ["board", "ver", "get usb.logging", "stats-core"],
                          "settings_mutation": False, "reset_hooks": False}))
        return 0
    os.umask(0o077)
    require(args.work_dir.is_dir(), "existing_private_work_directory_required")
    require(args.work_dir.stat().st_uid == os.getuid()
            and args.work_dir.stat().st_mode & 0o077 == 0, "work_directory_must_be_private_and_owned")
    descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    report = {"schema_version": 1, "ok": False,
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "pinned-official-console-native-WebSerial-open-idle-reopen",
              "settings_mutation": False, "reset_hooks": False,
              "terminal_mode": args.terminal_mode,
              "firmware_artifact_binding": "external manifest plus exact runtime board/version; no flash read"}
    status = 1
    try:
        with cleanup_on_sigterm():
            execute(args, report)
        report["ok"] = True
        status = 0
    except BaseException as error:
        # Preserve only controlled errors, never arbitrary native console output.
        failure = safe_native_failure(error, report.get("phase"))
        report["failure"] = failure
        report["error_type"] = failure["class"]
        report["error"] = failure["code"]
        if isinstance(error, NativeConsoleJavaScriptError):
            report["javascript_exception"] = error.safe_details
        if isinstance(error, NativeConsoleTerminated):
            report["termination_signal"] = "SIGTERM"
            status = 143
        elif isinstance(error, KeyboardInterrupt):
            status = 130
    finally:
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    print(json.dumps({"ok": report["ok"], "output": str(args.output), "error": report.get("error")}))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
