#!/usr/bin/env python3
"""Exercise the real Room page against an explicitly selected live board.

Run with other room authentication tests paused. This causes a temporary global
30-second password lock, uses three ephemeral browser identities, and reads
chat/index data. It never submits a chat draft, saves settings, or changes room
data. Chromium uses an isolated private profile; window.fetch is not replaced.
CDP passively observes requests and responses without pausing or altering them.
Any unexpected write fails the run immediately. Request evidence excludes bodies,
passwords, tokens and headers.

Example, with an existing SSH TCP forward to the actual board::

  python3 tools/hil/room_browser_acceptance.py \
    --endpoint http://127.0.0.1:19080 --manifest identity.json \
    --secrets-file private.json --run-id v4-room-ui-01 \
    --allow-auth-attempts --work-dir /private/browser-runs \
    --chromium /usr/bin/chromium --report new-result.json

The manifest uses room_webconfig_acceptance.py's identity/artifact schema.
Its optional room_html_sha256 pins the exact served HTML; otherwise --room-source
extracts that hash from the repository's RoomWebHtml.h. The firmware SHA is
external evidence, not a live flash measurement. The owned 0600/0400 private JSON
needs admin_password and room_public_read_only; optional request_timeout,
room_timeout and timing_guard have the same meaning as the HTTP runner. Its
source_ips are unused: the Room budget is global across tokens. Passwords,
tokens, chat contents, names, origins and raw browser errors are never printed.

Requires Chromium and websocket-client (optionally supplied by --vendor), plus
the adjacent official_console_acceptance.py, linux_web_serial.py and
room_webconfig_acceptance.py. No serial port is opened. Allow about 90 seconds.
Failure may leave a temporary room lock; private browser state is retained in
the protected work directory for operator cleanup, and the owned browser exits.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
from urllib.parse import urlsplit

from official_console_acceptance import CDP
from room_webconfig_acceptance import (
    CheckFailure, HttpClient, MAX_RESPONSE, ROOM_ERROR, ROOM_WRONG, RoomClient,
    load_json, parse_endpoint, require, resolve_endpoint, subset_matches,
    validate_manifest,
)


def private_settings(value):
    password = value.get("admin_password")
    require(isinstance(password, str) and 0 < len(password.encode("utf-8")) <= 64
            and "\0" not in password, "private_admin_password_invalid")
    require(type(value.get("room_public_read_only")) is bool,
            "room_read_only_expectation_required")
    value = dict(value)
    for key, default, minimum, maximum in (
            ("request_timeout", 2.0, 0.1, 5.0),
            ("room_timeout", 12.0, 2.0, 20.0),
            ("timing_guard", 0.35, 0.2, 1.0)):
        number = value.get(key, default)
        require(type(number) in (int, float) and minimum <= number <= maximum,
                "private_timing_value_invalid")
        value[key] = float(number)
    return value


def html_hash(args, manifest):
    supplied = manifest.get("room_html_sha256")
    if supplied is not None:
        require(isinstance(supplied, str) and re.fullmatch(r"[0-9a-fA-F]{64}", supplied),
                "manifest_room_html_sha256_invalid")
        return supplied.lower()
    try:
        source = args.room_source.read_bytes()
        require(source.count(b'R"roomhtml(') == 1
                and source.count(b')roomhtml";') == 1, "room_source_delimiters_invalid")
        html = source.split(b'R"roomhtml(', 1)[1].split(b')roomhtml";', 1)[0]
    except OSError:
        raise CheckFailure("room_source_unreadable_or_manifest_HTML_hash_required") from None
    require(html.startswith(b"<!doctype html>"), "room_source_HTML_invalid")
    return hashlib.sha256(html).hexdigest()


def served_html_hash(endpoint, timeout):
    connection = http.client.HTTPConnection(endpoint["address"], endpoint["port"],
                                           timeout=timeout, source_address=("127.0.0.1", 0))
    try:
        connection.request("GET", "/room", headers={"Host": endpoint["host_header"],
                           "Accept": "text/html", "Connection": "close"})
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE + 1)
        require(response.status == 200 and len(body) <= MAX_RESPONSE
                and response.getheader("Content-Type", "").startswith("text/html"),
                "live_room_page_contract_invalid")
        return hashlib.sha256(body).hexdigest()
    except (OSError, http.client.HTTPException):
        raise CheckFailure("live_room_page_request_failed") from None
    finally:
        connection.close()


class PassiveCDP(CDP):
    """Observe the existing transport without Fetch interception or patches."""

    def __init__(self, websocket, debug_port, profile, origin):
        self.origin = origin
        self.operations = Counter()
        self.unexpected_requests = 0
        self.write_requests = 0
        self.network_started = time.monotonic()
        self.requests = []
        self.request_ids = {}
        self.room_operations = {}
        self.runtime_errors = Counter()
        self.browser_errors = Counter()
        super().__init__(websocket, debug_port, profile)
        try:
            self.call("Log.enable")
            self.call("Network.enable", {"maxPostDataSize": 4096})
        except BaseException:
            # A constructor failure never reaches the caller's page cleanup.
            # Release this partial connection before retrying the owned page.
            self.socket.close()
            raise

    def network(self, method, event):
        identifier = event.get("requestId")
        elapsed = round(time.monotonic() - self.network_started, 3)
        if method == "Network.requestWillBeSent":
            request = event["request"]
            url = urlsplit(request["url"])
            verb = request["method"]
            same_origin = url.scheme + "://" + url.netloc == self.origin
            known_paths = ("/room", "/api/status", "/favicon.ico", "/api/room", "/api/room/result")
            path = url.path if same_origin and url.path in known_paths else "other"
            headers = {key.lower(): value for key, value in request.get("headers", {}).items()}
            sequence = headers.get("x-room-seq", "")
            sequence = int(sequence) if re.fullmatch(r"[0-9]{1,10}", str(sequence)) else None
            operation = None
            read_only = same_origin and not url.query and not url.fragment and (
                verb == "GET" and path in ("/room", "/api/status", "/favicon.ico")
                or verb == "POST" and path == "/api/room/result")
            if verb == "POST" and path == "/api/room":
                try:
                    payload = json.loads(request.get("postData", ""))
                    operation = payload.get("op") if isinstance(payload, dict) else None
                except (ValueError, TypeError):
                    operation = None
                if operation in ("status", "posts", "board.index"):
                    self.operations[operation] += 1
                    if sequence is not None:
                        self.room_operations[sequence] = operation
                    read_only = not url.query and not url.fragment
                else:
                    operation = "unexpected"
            elif path == "/api/room/result":
                operation = self.room_operations.get(sequence, "unmatched_result")
            entry = {"ordinal": len(self.requests) + 1, "path": path,
                     "method": verb if verb in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS") else "other",
                     "operation": operation, "room_sequence": sequence,
                     "started_seconds": elapsed, "status": None,
                     "finished": False, "failed": False}
            self.requests.append(entry)
            self.request_ids[identifier] = entry
            if not read_only:
                self.unexpected_requests += 1
                if verb not in ("GET", "HEAD", "OPTIONS"):
                    self.write_requests += 1
                raise CheckFailure("unexpected_browser_request_observed_passively")
            return
        entry = self.request_ids.get(identifier)
        if entry is None:
            return
        if method == "Network.responseReceived":
            status = event.get("response", {}).get("status")
            entry["status"] = int(status) if type(status) in (int, float) else None
            entry["response_seconds"] = elapsed
        elif method == "Network.loadingFinished":
            entry.update(finished=True, finished_seconds=elapsed,
                         encoded_bytes=event.get("encodedDataLength"))
        elif method == "Network.loadingFailed":
            error = event.get("errorText", "")
            entry.update(failed=True, failed_seconds=elapsed,
                         canceled=bool(event.get("canceled")),
                         failure_class=(error if re.fullmatch(r"net::ERR_[A-Z0-9_]+", error)
                                        else "other_network_failure"))

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
            if response.get("method", "").startswith("Network."):
                self.network(response["method"], response.get("params", {}))
                continue
            if response.get("method") == "Runtime.exceptionThrown":
                details = response.get("params", {}).get("exceptionDetails", {})
                name = details.get("exception", {}).get("className", "OtherError")
                if name not in ("SyntaxError", "ReferenceError", "TypeError", "RangeError",
                                "SecurityError", "DOMException", "Error"):
                    name = "OtherError"
                self.runtime_errors[name] += 1
                continue
            if response.get("method") == "Log.entryAdded":
                entry = response.get("params", {}).get("entry", {})
                if entry.get("level") == "error":
                    source = entry.get("source")
                    if source not in ("javascript", "network", "security", "storage", "rendering"):
                        source = "other"
                    self.browser_errors[source] += 1
                continue
            identifier = response.get("id")
            if identifier == sequence:
                require("error" not in response, "CDP_request_failed")
                return response.get("result", {})
        raise CheckFailure("CDP_request_deadline")


def wait_for_room_browser(websocket, debug_port, profile, origin, browser, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require(browser.poll() is None, "owned_Chromium_exited_before_ready")
        try:
            return PassiveCDP(websocket, debug_port, profile, origin)
        except (OSError, urllib.error.URLError, websocket.WebSocketTimeoutException,
                websocket.WebSocketConnectionClosedException):
            time.sleep(0.1)
    raise CheckFailure("owned_Chromium_startup_deadline")


def snapshot(page, draft):
    return page.evaluate("""(() => {
      const el=id=>document.getElementById(id);
      // DOM nodes precede the large inline script in the streamed response.
      // Also tolerate lexical bindings that are still uninitialized mid-parse.
      const safely=(read,fallback)=>{try{return read()}catch(_){return fallback}};
      const complete=document.readyState==='complete';
      const scriptReady=complete&&safely(()=>typeof joined==='boolean'
        &&typeof polling==='boolean'&&typeof role==='number'
        &&typeof makeIdentity==='function'&&typeof request==='function'
        &&typeof refreshChat==='function'&&typeof chain==='object',false);
      return {ready:scriptReady&&!!el('join')&&!!el('message')&&!!el('refresh'),
        document_ready_state:document.readyState, script_ready:scriptReady,
        form_present:!!el('join'), draft_input_present:!!el('message'),
        joined:safely(()=>typeof joined==='boolean'&&joined,false),
        role:safely(()=>typeof role==='number'?role:null,null),
        boot:safely(()=>typeof boot==='number'?boot:null,null),
        polling:safely(()=>typeof polling==='boolean'&&polling,false),
        room_visible:!!el('room')&&!el('room').hidden,
        state:el('state')?.textContent, error:el('error')?.textContent,
        draft_matches:el('message')?.value===__DRAFT__,
        password_cleared:el('password')?.value==='',
        notices_ready:!!el('notices')?.childNodes.length,
        native_fetch:Function.prototype.toString.call(window.fetch).includes('[native code]'),
        web_key:el('web-key')?.textContent, room_name:el('room-name')?.textContent};
    })()""".replace("__DRAFT__", json.dumps(draft)))


def safe_snapshot(value):
    """Only fixed classifications/booleans leave the private CDP state."""
    keep = ("ready", "document_ready_state", "script_ready", "form_present",
            "draft_input_present", "joined", "role", "polling", "room_visible",
            "draft_matches", "password_cleared", "notices_ready", "native_fetch")
    result = {key: value.get(key) for key in keep}
    states = {"Admin access": "admin", "Read and write access": "read_write",
              "Read-only access": "read_only", "Disconnected": "disconnected", "": "empty"}
    result["state_kind"] = states.get(value.get("state"), "other")
    error = value.get("error") or ""
    result["error_kind"] = ({"": "none", ROOM_ERROR: "password_rate_window",
                            ROOM_WRONG: "incorrect_password",
                            "No confirmation received. Refresh and check the room before trying the operation again.":
                                "request_confirmation_timeout"}.get(error, "other"))
    result["error_length"] = len(error)
    result["boot_present"] = type(value.get("boot")) is int and value["boot"] > 0
    return result


def require_join_handler(page):
    node = page.call("Runtime.evaluate", {
        "expression": "document.getElementById('join')", "returnByValue": False})
    object_id = node.get("result", {}).get("objectId")
    require(object_id is not None, "production_join_form_object_missing")
    try:
        listeners = page.call("DOMDebugger.getEventListeners", {"objectId": object_id})
        require(any(listener.get("type") == "submit" for listener in listeners.get("listeners", [])),
                "production_join_submit_handler_not_attached")
    finally:
        page.call("Runtime.releaseObject", {"objectId": object_id})


def wait_for(page, draft, predicate, seconds, failure):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        require(page.unexpected_requests == 0, "unexpected_browser_request_observed_passively")
        value = snapshot(page, draft)
        if predicate(value):
            return value
        time.sleep(0.1)
    raise CheckFailure(failure)


def join(page, password):
    # Interact with the production form; never invoke an API or compose handler.
    require_join_handler(page)
    page.evaluate("""(() => {
      if(document.readyState!=='complete'||typeof joined!=='boolean')
        throw Error('Room production handlers are not ready');
      document.getElementById('password').value=__PASSWORD__;
      document.getElementById('join').requestSubmit(); return true;
    })()""".replace("__PASSWORD__", json.dumps(password)))


def execute(args, manifest, private, report, record):
    if args.vendor:
        sys.path.insert(0, str(args.vendor.resolve()))
    import websocket

    endpoint = resolve_endpoint(args.endpoint)
    require(ipaddress.ip_address(endpoint["address"]).is_loopback,
            "endpoint_must_be_loopback_forward_to_board")
    origin = "http://" + endpoint["host_header"]
    expected_html = html_hash(args, manifest)
    client = HttpClient(endpoint, "127.0.0.1", private["request_timeout"])

    def identity():
        reply = client.request("GET", "/api/status", use_cookie=False)
        require(reply.status == 200 and subset_matches(reply.body, manifest["status"]),
                "live_status_identity_mismatch")
        require(reply.body.get("password_supported") is True
                and reply.body.get("needs_setup") is False
                and reply.body.get("needs_password") is False,
                "configured_admin_auth_precondition_failed")
        require(type(reply.body.get("uptime_s")) is int, "live_uptime_missing")
        return reply.body["uptime_s"], time.monotonic()

    uptime, identity_at = identity()
    require(served_html_hash(endpoint, private["request_timeout"]) == expected_html,
            "live_room_HTML_does_not_match_expected_source")
    report["room_html_sha256"] = expected_html
    record("live_node_version_mode_and_Room_HTML_match_manifest_and_source")
    report["phase"] = "initial_quiet_window"
    record("initial_quiet_window", "WAIT", seconds=30 + private["timing_guard"])
    time.sleep(30 + private["timing_guard"])

    work = Path(tempfile.mkdtemp(prefix="room-browser-", dir=args.work_dir))
    work.chmod(0o700)
    report["private_state_directory"] = str(work)
    profile = work / "profile"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        debug_port = reservation.getsockname()[1]
    browser = page = None
    browser_log = (work / "chromium-private.log").open("xb")
    draft = "HIL unsent draft " + secrets.token_hex(12)
    report["draft_sha256"] = hashlib.sha256(draft.encode()).hexdigest()
    try:
        browser = subprocess.Popen([str(args.chromium), "--headless=new", "--no-sandbox",
            "--disable-dev-shm-usage", "--disable-gpu", "--disable-background-networking",
            "--disable-extensions", "--disable-sync", "--no-first-run", "--no-default-browser-check",
            "--enable-automation", "--renderer-process-limit=1",
            "--proxy-server=direct://", "--proxy-bypass-list=*",
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=" + str(debug_port),
            "--user-data-dir=" + str(profile), "about:blank"],
            stdout=browser_log, stderr=browser_log, start_new_session=True)
        page = wait_for_room_browser(websocket, debug_port, profile, origin, browser)
        report["browser_version"] = page.browser_version
        report["phase"] = "initial_admin_UI_login"
        page.call("Page.navigate", {"url": origin + "/room"})
        wait_for(page, draft, lambda state: state["ready"], 15, "real_room_UI_did_not_load")
        report["authentication_attempts_started"] = True
        join(page, private["admin_password"])
        ready = lambda state: (state["joined"] and state["role"] == 3
            and state["room_visible"] and not state["polling"] and state["notices_ready"]
            and state["state"] == "Admin access" and state["error"] == "")
        initial = wait_for(page, draft, ready, 30, "real_admin_UI_login_failed")
        require(initial["password_cleared"] and initial["native_fetch"],
                "UI_password_not_cleared_or_fetch_not_native")
        expected_name = manifest["config_expected"].get("radio", {}).get("name")
        if expected_name is not None:
            require(initial["room_name"] == expected_name, "room_UI_node_name_mismatch")
        require(type(initial["boot"]) is int and initial["boot"] > 0
                and re.fullmatch(r"[0-9A-Fa-f]{64}", initial["web_key"] or "") is not None,
                "room_UI_boot_or_identity_missing")
        page.evaluate("""(() => {const el=document.getElementById('message');
          el.value=__DRAFT__;el.dispatchEvent(new Event('input',{bubbles:true}));return true;})()
        """.replace("__DRAFT__", json.dumps(draft)))
        require(snapshot(page, draft)["draft_matches"], "draft_input_failed")
        record("real_admin_UI_login_and_unsent_draft")

        report["phase"] = "force_global_failure_window"
        actors = [RoomClient(HttpClient(endpoint, "127.0.0.1", private["request_timeout"]),
                             private["room_timeout"]) for _ in range(2)]
        forbidden = {private["admin_password"], private.get("room_guest_password", "")}
        first = None
        before_status_requests = page.operations["status"]
        for index in range(5):
            wrong = "hil-wrong-" + secrets.token_hex(16)
            while wrong in forbidden:
                wrong = "hil-wrong-" + secrets.token_hex(16)
            forbidden.add(wrong)
            reply = actors[index % 2].status(wrong)
            if first is None:
                first = reply
            if private["room_public_read_only"]:
                require("error" not in reply.body and reply.body.get("role") == 0,
                        "wrong_password_not_admitted_as_public_guest")
            else:
                require(reply.body.get("error") == ROOM_WRONG,
                        "wrong_password_did_not_match_private_policy")
        require(time.monotonic() - first.started < 10,
                "five_failure_setup_too_slow_for_UI_window")
        locked = actors[1].status(private["admin_password"])
        require(locked.status == 200 and locked.body.get("error") == ROOM_ERROR,
                "real_global_lock_not_confirmed_as_JSON_error")
        empty = actors[1].status("")
        if private["room_public_read_only"]:
            require("error" not in empty.body and empty.body.get("role") == 0,
                    "empty_password_public_guest_access_lost_during_lock")
        else:
            require(empty.body.get("error") == ROOM_WRONG,
                    "empty_password_bypassed_private_room_policy")
        record("five_unique_wrong_passwords_across_two_tokens_lock_valid_admin_JSON_error")

        report["phase"] = "real_automatic_UI_poll_under_lock"
        # Pump CDP events while the unmodified page's own 15-second timer polls.
        remaining = min(20.0, first.started + 27.0 - time.monotonic())
        require(remaining > 0, "insufficient_time_for_automatic_UI_poll")
        failed = wait_for(page, draft, lambda state: state["error"] == ROOM_ERROR,
                          remaining, "automatic_UI_poll_did_not_show_real_lock_message")
        require(failed["draft_matches"] and failed["joined"]
                and failed["room_visible"] and failed["native_fetch"]
                and failed["boot"] == initial["boot"]
                and page.operations["status"] > before_status_requests,
                "UI_draft_session_or_real_poll_not_preserved_during_lock")
        record("automatic_real_UI_poll_shows_rate_window_and_preserves_draft")

        report["phase"] = "window_expiry_and_UI_reconnect"
        deadline = first.finished + 30 + private["timing_guard"]
        while time.monotonic() < deadline:
            require(snapshot(page, draft)["draft_matches"], "draft_changed_while_waiting_for_retry")
            require(page.unexpected_requests == 0, "unexpected_browser_request_observed_passively")
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        # Connect is the production retry that also clears an old error banner.
        # Refresh alone leaves that banner's text unchanged in the current UI.
        join(page, private["admin_password"])
        recovered = wait_for(page, draft, ready, 30, "UI_admin_retry_did_not_recover_after_window")
        require(recovered["draft_matches"] and recovered["password_cleared"]
                and recovered["native_fetch"] and recovered["boot"] == initial["boot"]
                and recovered["web_key"] == initial["web_key"],
                "UI_retry_changed_draft_identity_boot_or_fetch")
        record("Connect_retry_recovers_admin_and_clears_error_without_clearing_draft")
        final_uptime, final_at = identity()
        require(abs(final_uptime - uptime - (final_at - identity_at)) < 5,
                "board_restarted_or_uptime_changed_during_UI_scenario")
        require(page.unexpected_requests == 0, "unexpected_browser_request_observed_passively")
        report.update(browser_read_operations=dict(page.operations),
                      unexpected_browser_requests_observed=page.unexpected_requests,
                      native_fetch_unchanged=True, room_boot_unchanged=True,
                      browser_identity_preserved=True, unsent_draft_preserved=True,
                      retry_method="production Connect form", temporary_room_lock_may_remain=False)
    finally:
        if page is not None:
            try:
                report["final_UI_state"] = safe_snapshot(snapshot(page, draft))
            except Exception:
                report["final_UI_state_unavailable"] = True
            report["browser_read_operations"] = dict(page.operations)
            report["unexpected_browser_requests_observed"] = page.unexpected_requests
            report["browser_write_requests_observed"] = page.write_requests
            report["browser_request_evidence"] = page.requests
            report["page_runtime_exception_classes"] = dict(page.runtime_errors)
            report["browser_error_sources"] = dict(page.browser_errors)
            page.socket.close()
        if browser is not None and browser.poll() is None:
            os.killpg(browser.pid, signal.SIGTERM)
            try:
                browser.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(browser.pid, signal.SIGKILL)
                browser.wait(timeout=5)
        report["owned_browser_closed"] = browser is None or browser.poll() is not None
        browser_log.close()


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint", required=True, help="Loopback HTTP origin forwarded to the actual board")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--secrets-file", type=Path,
                        default=os.environ.get("MESHCORE_AUTH_SECRETS_FILE"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--allow-auth-attempts", action="store_true")
    parser.add_argument("--report", type=Path, required=True, help="New sanitized JSON report; never overwritten")
    parser.add_argument("--work-dir", type=Path, required=True, help="Existing private parent for browser state")
    parser.add_argument("--chromium", type=Path, default=Path("/usr/bin/chromium"))
    parser.add_argument("--vendor", type=Path, help="Directory containing websocket-client")
    parser.add_argument("--room-source", type=Path,
                        default=Path(__file__).resolve().parents[2] / "src/helpers/esp32/RoomWebHtml.h")
    return parser


def main(argv=None):
    args = argument_parser().parse_args(argv)
    report = {"schema": 1, "result": "FAIL", "phase": "input_validation", "steps": [],
              "run_id": None, "live_flash_hash_verified": False,
              "firmware_sha256_evidence": "externally supplied manifest",
              "board_settings_changed_by_runner": False, "compose_submit_actions": 0,
              "browser_network_interception": False, "authentication_attempts_started": False,
              "temporary_room_lock_may_remain": False}
    output = None
    started = time.monotonic()

    def record(step, result="PASS", **values):
        event = {"step": step, "result": result, **values}
        report["steps"].append(event)
        print(json.dumps(event, sort_keys=True), flush=True)

    try:
        require(args.allow_auth_attempts, "explicit_allow_auth_attempts_required")
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.run_id) is not None,
                "run_id_invalid")
        report["run_id"] = args.run_id
        require(args.secrets_file is not None, "private_secrets_file_required")
        manifest = validate_manifest(load_json(args.manifest))
        private = private_settings(load_json(args.secrets_file, private=True))
        endpoint = parse_endpoint(args.endpoint)
        require(endpoint["hostname"] in ("localhost", "127.0.0.1"),
                "endpoint_must_be_localhost_or_127_0_0_1_forward")
        require(args.work_dir.is_dir() and args.work_dir.stat().st_uid == os.geteuid()
                and args.work_dir.stat().st_mode & 0o077 == 0,
                "work_directory_must_be_owned_and_private")
        try:
            output = os.fdopen(os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                       0o600), "w", encoding="utf-8")
        except OSError:
            raise CheckFailure("report_path_unavailable_or_already_exists") from None
        report.update(source_commit=manifest["source_commit"].lower(),
                      firmware_sha256=manifest["firmware_sha256"].lower(),
                      mode=manifest["status"]["mode"],
                      node_id_sha256=hashlib.sha256(manifest["status"]["node_id"].encode()).hexdigest())
        execute(args, manifest, private, report, record)
        report["result"] = "PASS"
        report["phase"] = "complete"
        return_code = 0
    except (CheckFailure, KeyboardInterrupt) as error:
        report["failure"] = "operator_interrupted" if isinstance(error, KeyboardInterrupt) else str(error)
        return_code = 130 if isinstance(error, KeyboardInterrupt) else 1
    except Exception as error:
        # Do not expose URLs, request bodies, raw browser errors or credentials.
        report["failure"] = "unexpected_" + type(error).__name__
        return_code = 1
    finally:
        if report["result"] != "PASS":
            report["temporary_room_lock_may_remain"] = report["authentication_attempts_started"]
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if output:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
            output.close()
        print(json.dumps({key: value for key, value in report.items() if key != "steps"},
                         sort_keys=True), flush=True)
    return return_code


if __name__ == "__main__":
    sys.exit(main())
