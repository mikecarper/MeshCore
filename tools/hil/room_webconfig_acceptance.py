#!/usr/bin/env python3
"""Live WebConfig/room password acceptance checks; never changes board settings.

Run on a gateway with two ALREADY CONFIGURED IPv4 source addresses, while other
authentication attempts and room polling are paused. This deliberately causes
temporary login lockouts and creates WebConfig sessions (evicting the old single
session). It sends only login/logout, config/status reads and room status reads;
it does not post messages, change preferences, reboot, or configure networking.
Room access must already be enabled, with spare browser slots and a poll quota
that permits roughly 25 status reads during one minute (zero disables quotas).

Example (credentials are never command-line arguments)::

  python3 tools/hil/room_webconfig_acceptance.py \
    --endpoint http://192.0.2.10 --manifest identity.json \
    --secrets-file private.json --run-id candidate-lan-01 \
    --scenario all --allow-auth-attempts --report sanitized-result.json

Manifest example (replace every placeholder with independently checked values)::

  {"source_commit":"<8-to-40-hex>", "firmware_sha256":"<64-hex>",
   "status":{"node_id":"<16-hex>", "board":"<exact board>",
             "role":"<exact role>", "fw":"<exact /api/status fw>",
             "mode":"lan"},
   "config_expected":{"radio":{"name":"<expected node name>"}}}

Private JSON must be owned by this user and mode 0600 (or 0400)::

  {"admin_password":"<existing password>",
   "source_ips":["192.0.2.20","192.0.2.21"],
   "room_guest_password":"<optional existing guest password>",
   "room_public_read_only":false,
   "request_timeout":2.0, "room_timeout":12.0, "timing_guard":0.35}

MESHCORE_AUTH_SECRETS_FILE may supply the private file path instead. The runner
does not print endpoints, source addresses, node names, passwords, cookies,
room tokens or raw responses. The manifest's firmware hash is EXTERNAL evidence,
not a live flash-hash measurement: /api/status has no such field. Network checks
use timing guard bands; exact millisecond boundaries belong to the host tests.
Allow about three minutes for all scenarios. On failure, a partial budget or
30-second lock can remain; the report records that possibility explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
from http.cookies import SimpleCookie
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


CLIENT_ERROR = "locked, retry in 30s"
SHARED_ERROR = "too many failed logins, retry shortly"
ROOM_ERROR = "too many wrong room passwords; wait 30 seconds"
ROOM_WRONG = "incorrect room password"
MAX_RESPONSE = 131072


class CheckFailure(RuntimeError):
    """Only fixed, credential-free reason codes may leave the runner."""


def require(condition, code):
    if not condition:
        raise CheckFailure(code)


def load_json(path, *, private=False):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            metadata = os.fstat(stream.fileno())
            require(stat.S_ISREG(metadata.st_mode), "input_not_regular_file")
            require(metadata.st_size <= 65536, "input_file_too_large")
            if private:
                require(stat.S_IMODE(metadata.st_mode) in (0o600, 0o400),
                        "private_file_requires_mode_0600_or_0400")
                require(metadata.st_uid == os.geteuid(), "private_file_wrong_owner")
            value = json.load(stream)
    except CheckFailure:
        raise
    except (OSError, ValueError, UnicodeError):
        raise CheckFailure("input_file_unreadable_or_invalid_json") from None
    require(isinstance(value, dict), "input_json_must_be_object")
    return value


def subset_matches(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and subset_matches(actual[key], value)
            for key, value in expected.items())
    return type(actual) is type(expected) and actual == expected


def has_expected_leaf(value):
    return (any(has_expected_leaf(item) for item in value.values())
            if isinstance(value, dict) else True)


def validate_manifest(value):
    require(re.fullmatch(r"[0-9a-fA-F]{8,40}", str(value.get("source_commit", "")))
            is not None, "manifest_source_commit_required")
    require(re.fullmatch(r"[0-9a-fA-F]{64}", str(value.get("firmware_sha256", "")))
            is not None, "manifest_firmware_sha256_required")
    expected = value.get("status")
    require(isinstance(expected, dict), "manifest_status_required")
    for key in ("node_id", "board", "role", "fw", "mode"):
        require(isinstance(expected.get(key), str) and bool(expected[key]),
                "manifest_identity_fields_required")
    require(re.fullmatch(r"[0-9a-f]{16}", expected["node_id"]) is not None,
            "manifest_node_id_invalid")
    require(expected["mode"] in ("lan", "setup"), "manifest_mode_invalid")
    require(isinstance(value.get("config_expected"), dict)
            and has_expected_leaf(value["config_expected"]),
            "manifest_config_expectation_required")
    return value


def validate_private(value, scenario):
    password = value.get("admin_password")
    require(isinstance(password, str) and 0 < len(password.encode("utf-8")) <= 64
            and "\0" not in password, "private_admin_password_invalid")
    addresses = value.get("source_ips")
    require(isinstance(addresses, list) and len(addresses) == 2
            and all(isinstance(address, str) for address in addresses),
            "two_source_addresses_required")
    try:
        parsed = [str(ipaddress.IPv4Address(item)) for item in addresses]
    except (ValueError, TypeError, ipaddress.AddressValueError):
        raise CheckFailure("source_addresses_must_be_ipv4") from None
    require(parsed[0] != parsed[1], "source_addresses_must_differ")
    for address in parsed:
        ip = ipaddress.IPv4Address(address)
        require(not ip.is_unspecified and not ip.is_multicast,
                "source_address_not_unicast")
    value = dict(value, source_ips=parsed)
    if scenario in ("room", "all"):
        require(type(value.get("room_public_read_only")) is bool,
                "room_read_only_expectation_required")
    guest = value.get("room_guest_password")
    if guest is not None:
        require(isinstance(guest, str) and 0 < len(guest.encode("utf-8")) <= 64
                and "\0" not in guest and guest != password,
                "private_guest_password_invalid_or_same_as_admin")
    for key, default, minimum, maximum in (
            ("request_timeout", 2.0, 0.1, 5.0),
            ("room_timeout", 12.0, 2.0, 20.0),
            ("timing_guard", 0.35, 0.2, 1.0)):
        number = value.get(key, default)
        require(type(number) in (int, float) and minimum <= number <= maximum,
                "private_timing_value_invalid")
        value[key] = float(number)
    return value


@dataclass
class Reply:
    status: int
    body: dict
    headers: list
    started: float
    finished: float


class HttpClient:
    def __init__(self, endpoint, source_ip, timeout, *, connection_factory=None,
                 clock=time.monotonic):
        self.endpoint, self.source_ip, self.timeout = endpoint, source_ip, timeout
        self.connection_factory = connection_factory or http.client.HTTPConnection
        self.clock = clock
        self.cookie = None

    def request(self, method, path, payload=None, *, headers=None, use_cookie=True):
        require((method, path) in {
            ("GET", "/api/status"), ("GET", "/api/config"),
            ("POST", "/api/login"), ("POST", "/api/logout"),
            ("POST", "/api/room"), ("POST", "/api/room/result")},
            "request_outside_read_and_auth_allowlist")
        if path == "/api/room":
            require(isinstance(payload, dict) and set(payload) == {"op", "password"}
                    and payload["op"] == "status", "room_operation_must_be_status_read")
        elif path == "/api/login":
            require(isinstance(payload, dict) and set(payload) == {"password"},
                    "login_payload_invalid")
        else:
            require(payload is None or (path == "/api/logout" and payload == {}),
                    "unexpected_body_on_read_or_logout")
        body = None if payload is None else json.dumps(
            payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        request_headers = {"Host": self.endpoint["host_header"],
                           "Accept": "application/json", "Connection": "close"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if use_cookie and self.cookie:
            request_headers["Cookie"] = self.cookie
        request_headers.update(headers or {})
        connection = self.connection_factory(
            self.endpoint["address"], self.endpoint["port"], timeout=self.timeout,
            source_address=(self.source_ip, 0))
        started = self.clock()
        try:
            connection.connect()
            require(connection.sock.getsockname()[0] == self.source_ip,
                    "http_source_binding_not_observed")
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            require(response.getheader("Content-Type", "").split(";", 1)[0].strip()
                    == "application/json", "response_not_json_content_type")
            raw = response.read(MAX_RESPONSE + 1)
            require(len(raw) <= MAX_RESPONSE, "response_too_large")
            parsed = json.loads(raw)
            require(isinstance(parsed, dict), "response_json_not_object")
            return Reply(response.status, parsed, response.getheaders(),
                         started, self.clock())
        except CheckFailure:
            raise
        except (OSError, ValueError, UnicodeError, http.client.HTTPException):
            raise CheckFailure("http_transport_or_json_failure") from None
        finally:
            connection.close()

    def login(self, password):
        reply = self.request("POST", "/api/login", {"password": password})
        if reply.status == 200:
            require(reply.body.get("ok") is True and "error" not in reply.body,
                    "login_success_contract_invalid")
            cookies = SimpleCookie()
            try:
                for key, value in reply.headers:
                    if key.lower() == "set-cookie":
                        cookies.load(value)
            except Exception:
                raise CheckFailure("session_cookie_invalid") from None
            require("wcs" in cookies, "admin_login_did_not_create_session")
            cookie = cookies["wcs"]
            require(re.fullmatch(r"[0-9a-f]{32}", cookie.value) is not None
                    and bool(cookie["httponly"])
                    and cookie["samesite"].lower() == "lax",
                    "session_cookie_security_contract_invalid")
            self.cookie = "wcs=" + cookie.value
        return reply


class RoomClient:
    def __init__(self, client, timeout, *, clock=time.monotonic, sleep=time.sleep):
        self.client, self.timeout, self.clock, self.sleep = client, timeout, clock, sleep
        self.token, self.sequence, self.boot = secrets.token_hex(32), 0, None

    def status(self, password):
        self.sequence += 1
        headers = {"X-Room-Token": self.token, "X-Room-Seq": str(self.sequence)}
        deadline = self.clock() + self.timeout
        started = self.clock()
        while True:
            require(self.clock() < deadline, "room_admission_timeout")
            reply = self.client.request("POST", "/api/room",
                                        {"op": "status", "password": password},
                                        headers=headers, use_cookie=False)
            if reply.status == 202:
                require(reply.body.get("accepted") is True, "room_admission_contract_invalid")
                break
            require(reply.status == 429 and reply.body.get("error") == "room service busy",
                    "room_submission_rejected")
            self.sleep(0.1)
        while True:
            require(self.clock() < deadline, "room_result_timeout")
            reply = self.client.request("POST", "/api/room/result", headers=headers,
                                        use_cookie=False)
            if reply.status == 202:
                require(reply.body.get("pending") is True, "room_pending_contract_invalid")
                self.sleep(0.05)
                continue
            require(reply.status == 200, "room_result_transport_status_invalid")
            if reply.body.get("error") in (
                    "room browser disabled; use set room.web on",
                    "room browser busy; try later",
                    "room rate limit reached; try after the next minute"):
                raise CheckFailure("room_enabled_capacity_or_quota_precondition_failed")
            if "error" not in reply.body:
                require(type(reply.body.get("boot")) is int
                        and type(reply.body.get("role")) is int,
                        "room_status_identity_fields_missing")
                if self.boot is None:
                    self.boot = reply.body["boot"]
                require(reply.body["boot"] == self.boot, "room_reboot_during_test")
            return Reply(reply.status, reply.body, reply.headers, started, reply.finished)


def parse_endpoint(value):
    try:
        url = urlsplit(value)
        require(url.scheme == "http" and bool(url.hostname)
                and not url.username and not url.password and url.path in ("", "/")
                and not url.query and not url.fragment, "endpoint_must_be_plain_http_origin")
        port = url.port or 80
        require(1 <= port <= 65535, "endpoint_port_invalid")
        return {"hostname": url.hostname, "port": port,
                "host_header": url.netloc}
    except (ValueError, TypeError):
        raise CheckFailure("endpoint_invalid") from None


def resolve_endpoint(value):
    result = parse_endpoint(value)
    try:
        addresses = socket.getaddrinfo(result["hostname"], result["port"],
                                       socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        raise CheckFailure("endpoint_ipv4_resolution_failed") from None
    require(bool(addresses), "endpoint_ipv4_resolution_failed")
    # Resolve once, then pin every request to that address. No proxies/redirects.
    result["address"] = addresses[0][4][0]
    return result


class Acceptance:
    def __init__(self, endpoint, manifest, private, record, *, clock=time.monotonic,
                 sleep=time.sleep):
        self.manifest, self.private, self.record = manifest, private, record
        self.clock, self.sleep = clock, sleep
        self.clients = [HttpClient(endpoint, address, private["request_timeout"], clock=clock)
                        for address in private["source_ips"]]
        self.guard = private["timing_guard"]
        self.uptime = None
        self.uptime_at = None
        self.auth_attempts_started = False
        self.phase = "preflight"
        self.generated_wrong_passwords = set()

    def passed(self, step):
        self.record({"step": step, "result": "PASS"})

    def until(self, deadline):
        while self.clock() < deadline:
            self.sleep(min(0.5, deadline - self.clock()))

    def quiet_window(self, label):
        self.record({"step": label, "result": "WAIT", "seconds": 30 + self.guard})
        self.until(self.clock() + 30 + self.guard)

    def identity(self):
        for client in self.clients:
            reply = client.request("GET", "/api/status", use_cookie=False)
            require(reply.status == 200 and subset_matches(reply.body, self.manifest["status"]),
                    "live_status_identity_mismatch")
            require(reply.body.get("password_supported") is True
                    and reply.body.get("needs_setup") is False
                    and reply.body.get("needs_password") is False
                    and reply.body.get("auth") is False,
                    "admin_auth_not_required_or_initial_setup")
            uptime, now = reply.body.get("uptime_s"), self.clock()
            require(type(uptime) is int and uptime >= 0, "live_uptime_missing")
            if self.uptime is not None:
                elapsed = now - self.uptime_at
                difference = uptime - self.uptime
                if difference < 0:
                    difference += 4294967.296
                require(abs(difference - elapsed) < 5.0, "board_uptime_changed_unexpectedly")
            self.uptime, self.uptime_at = uptime, now

    def authorized(self, client):
        self.auth_attempts_started = True
        reply = client.login(self.private["admin_password"])
        require(reply.status == 200, "valid_admin_login_rejected")
        return reply

    def config(self, client):
        reply = client.request("GET", "/api/config")
        require(reply.status == 200 and "error" not in reply.body
                and subset_matches(reply.body, self.manifest["config_expected"]),
                "protected_config_read_not_authorized_or_changed")

    def wrong_password(self):
        while True:
            candidate = "hil-" + secrets.token_hex(5)
            if candidate not in (self.private["admin_password"],
                                 self.private.get("room_guest_password")) \
                    and candidate not in self.generated_wrong_passwords:
                self.generated_wrong_passwords.add(candidate)
                return candidate

    @staticmethod
    def error(reply, status, error, code):
        require(reply.status == status and reply.body.get("error") == error, code)

    def wrong_login(self, client):
        self.auth_attempts_started = True
        reply = client.login(self.wrong_password())
        self.error(reply, 401, "wrong password", "wrong_login_not_counted_as_failure")
        return reply

    def webconfig(self):
        self.phase = "webconfig"
        a, b = self.clients
        self.quiet_window("webconfig_initial_quiet_window")
        self.authorized(a)
        self.authorized(b)
        self.config(b)
        burst_started = self.clock()
        for _ in range(5):
            fifth = self.wrong_login(a)
        require(self.clock() - burst_started < 3.0, "client_failure_burst_too_slow")
        self.error(a.login(self.private["admin_password"]), 429, CLIENT_ERROR,
                   "fifth_failure_did_not_lock_its_address")
        self.config(b)
        self.authorized(b)
        self.error(a.login(self.private["admin_password"]), 429, CLIENT_ERROR,
                   "other_address_success_cleared_client_lock")
        self.passed("webconfig_five_failures_isolate_address_and_preserve_owner_session")
        self.until(fifth.started + 29.0)
        self.error(a.login(self.private["admin_password"]), 429, CLIENT_ERROR,
                   "client_lock_expired_before_guarded_boundary")
        self.until(fifth.finished + 30 + self.guard)
        self.authorized(a)
        self.passed("webconfig_client_lock_recovers_after_guarded_30s_boundary")
        self.authorized(b)
        self.authorized(a)  # A owns the current single session.
        burst_started = self.clock()
        first = None
        for index in range(6):
            reply = self.wrong_login(self.clients[index % 2])
            if first is None:
                first = reply
        require(self.clock() - burst_started < 3.0, "shared_failure_burst_too_slow")
        self.error(b.login(self.private["admin_password"]), 429, SHARED_ERROR,
                   "six_failures_did_not_exhaust_shared_budget")
        self.config(a)
        self.passed("webconfig_shared_budget_blocks_new_logins_preserves_existing_cookie")
        self.until(first.started + 5.0)
        self.error(b.login(self.private["admin_password"]), 429, SHARED_ERROR,
                   "shared_budget_refilled_before_guarded_boundary")
        self.until(first.finished + 6 + self.guard)
        self.authorized(b)
        self.wrong_login(a)
        self.error(b.login(self.private["admin_password"]), 429, SHARED_ERROR,
                   "successful_login_refunded_shared_failure_budget")
        self.config(b)
        self.passed("webconfig_shared_budget_refills_after_guarded_6s_without_success_refund")
        self.quiet_window("webconfig_release_lockouts")
        self.authorized(a)
        self.authorized(b)
        self.config(b)
        self.identity()

    def room_success(self, actor, password, role):
        reply = actor.status(password)
        require("error" not in reply.body and reply.body.get("role") == role,
                "room_valid_credential_or_read_only_policy_mismatch")
        return reply

    def room_wrong(self, actor, password):
        self.auth_attempts_started = True
        reply = actor.status(password)
        if self.private["room_public_read_only"]:
            require("error" not in reply.body and reply.body.get("role") == 0,
                    "wrong_room_password_not_admitted_as_read_only_guest")
        else:
            self.error(reply, 200, ROOM_WRONG, "wrong_room_password_not_rejected")
        return reply

    def room_empty(self, actor):
        reply = actor.status("")
        if self.private["room_public_read_only"]:
            require("error" not in reply.body and reply.body.get("role") == 0,
                    "empty_room_password_lost_public_read_only_access")
        else:
            self.error(reply, 200, ROOM_WRONG, "empty_room_password_bypassed_read_only_policy")

    def room(self):
        self.phase = "room"
        self.quiet_window("room_initial_quiet_window")
        owner, other = [RoomClient(client, self.private["room_timeout"],
                                   clock=self.clock, sleep=self.sleep)
                        for client in self.clients]
        admin = self.private["admin_password"]
        guest = self.private.get("room_guest_password")
        self.room_success(owner, admin, 3)
        if guest:
            self.room_success(other, guest, 2)
        self.room_empty(other)
        repeated = self.wrong_password()
        first = self.room_wrong(other, repeated)
        for index in range(5):
            self.room_wrong((owner, other)[index % 2], repeated)
        self.room_success(owner, admin, 3)
        self.passed("room_repeated_wrong_password_across_tokens_counts_once")
        for actor in (owner, other, owner):
            self.room_wrong(actor, self.wrong_password())
        self.room_success(owner, admin, 3)
        if guest:
            self.room_success(other, guest, 2)
        # Separate first and fifth failures enough to detect a window that
        # incorrectly starts at its fifth failure, without millisecond claims.
        self.until(first.finished + 2.0)
        self.room_wrong(other, self.wrong_password())
        require(self.clock() - first.started < 10.0, "room_failure_window_setup_too_slow")
        for actor, password in ((owner, admin), (other, repeated)):
            self.error(actor.status(password), 200, ROOM_ERROR,
                       "room_shared_window_did_not_block_nonempty_credential")
        if guest:
            self.error(other.status(guest), 200, ROOM_ERROR,
                       "room_shared_window_did_not_block_correct_guest_password")
        self.room_empty(other)
        self.passed("room_five_unique_failures_block_all_nonempty_credentials_without_success_refund")
        self.passed("room_empty_password_preserves_configured_read_only_policy_during_lock")
        self.until(first.started + 29.0)
        self.error(owner.status(admin), 200, ROOM_ERROR,
                   "room_window_expired_before_guarded_boundary")
        self.until(first.finished + 30 + self.guard)
        self.room_success(owner, admin, 3)
        self.passed("room_window_recovers_30s_after_first_failure_without_lock_extension")
        self.identity()

    def logout(self):
        # Last successful WebConfig login owns the one slot. Identify it with
        # a config read; logout is the only additional state change permitted.
        found = False
        for client in reversed(self.clients):
            if not client.cookie:
                continue
            reply = client.request("GET", "/api/config")
            if reply.status == 200:
                reply = client.request("POST", "/api/logout", {})
                require(reply.status == 200 and reply.body.get("ok") is True,
                        "owned_session_logout_failed")
                found = True
                self.passed("owned_webconfig_session_logged_out")
                break
            require(reply.status == 401, "owned_session_cleanup_unexpected_status")
        require(found, "owned_session_not_usable_for_cleanup")


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--secrets-file", type=Path,
                        default=os.environ.get("MESHCORE_AUTH_SECRETS_FILE"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scenario", choices=("webconfig", "room", "all"), default="all")
    parser.add_argument("--allow-auth-attempts", action="store_true")
    parser.add_argument("--report", type=Path, help="New sanitized JSON report; never overwritten")
    return parser


def main(argv=None):
    args = argument_parser().parse_args(argv)
    report = {"schema": 1, "run_id": None, "result": "FAIL", "steps": [],
              "live_flash_hash_verified": False,
              "firmware_sha256_evidence": "externally supplied manifest",
              "timing_evidence": "network guard bands; not exact millisecond boundaries",
              "board_settings_changed_by_runner": False}
    output = None
    acceptance = None
    started = time.monotonic()

    def record(step):
        report["steps"].append(step)
        print(json.dumps(step, sort_keys=True), flush=True)

    try:
        require(args.allow_auth_attempts, "explicit_allow_auth_attempts_required")
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.run_id) is not None,
                "run_id_invalid")
        report["run_id"] = args.run_id
        require(args.secrets_file is not None, "private_secrets_file_required")
        manifest = validate_manifest(load_json(args.manifest))
        private = validate_private(load_json(args.secrets_file, private=True), args.scenario)
        parse_endpoint(args.endpoint)  # Validate before creating output/resolving.
        if args.report:
            try:
                output = os.fdopen(os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                           0o600), "w", encoding="utf-8")
            except OSError:
                raise CheckFailure("report_path_unavailable_or_already_exists") from None
        report.update(source_commit=manifest["source_commit"].lower(),
                      firmware_sha256=manifest["firmware_sha256"].lower(),
                      scenario=args.scenario, mode=manifest["status"]["mode"],
                      node_id_sha256=hashlib.sha256(
                          manifest["status"]["node_id"].encode("ascii")).hexdigest())
        endpoint = resolve_endpoint(args.endpoint)
        acceptance = Acceptance(endpoint, manifest, private, record)
        acceptance.identity()
        acceptance.passed("both_source_addresses_match_manifest_identity_and_require_admin_auth")
        # Prove the protected config expectation before ANY wrong password.
        acceptance.authorized(acceptance.clients[0])
        acceptance.config(acceptance.clients[0])
        acceptance.passed("protected_config_matches_manifest")
        if args.scenario in ("webconfig", "all"):
            acceptance.webconfig()
        if args.scenario in ("room", "all"):
            acceptance.room()
        acceptance.logout()
        report["result"] = "PASS"
        report["temporary_auth_lockout_may_remain"] = False
        return_code = 0
    except (CheckFailure, KeyboardInterrupt) as error:
        report["failure"] = ("operator_interrupted" if isinstance(error, KeyboardInterrupt)
                             else str(error))
        return_code = 130 if isinstance(error, KeyboardInterrupt) else 1
    except Exception as error:
        # Never expose a third-party exception's URL, response, headers or body.
        report["failure"] = "unexpected_" + type(error).__name__
        return_code = 1
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if report["result"] != "PASS":
            report["phase"] = acceptance.phase if acceptance else "input_validation"
            report["temporary_auth_lockout_may_remain"] = bool(
                acceptance and acceptance.auth_attempts_started)
        if output:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
            output.close()
        print(json.dumps({key: value for key, value in report.items() if key != "steps"},
                         sort_keys=True), flush=True)
    return return_code


if __name__ == "__main__":
    sys.exit(main())
