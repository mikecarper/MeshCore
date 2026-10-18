"""Offline guards and live-wire contract tests; no sockets or hardware."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import room_webconfig_acceptance as hil
import room_browser_acceptance as browser_hil


def manifest():
    return {"source_commit": "12345678", "firmware_sha256": "a" * 64,
            "status": {"node_id": "0123456789abcdef", "board": "test-board",
                       "role": "Room Server", "fw": "test-fw", "mode": "lan"},
            "config_expected": {"radio": {"name": "private-node-name"}}}


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status = status
        self.body = json.dumps(body or {}).encode()
        self.headers = headers or [("Content-Type", "application/json")]

    def getheader(self, name, default=None):
        return next((value for key, value in self.headers if key.lower() == name.lower()), default)

    def getheaders(self):
        return self.headers

    def read(self, maximum):
        return self.body[:maximum]


class FakeConnection:
    def __init__(self, response, source="192.0.2.20", error=None):
        self.response, self.source, self.error = response, source, error
        self.sock, self.closed, self.sent = self, False, None

    def getsockname(self):
        return self.source, 12345

    def connect(self):
        if self.error:
            raise self.error

    def request(self, method, path, **values):
        self.sent = method, path, values

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class ContractTests(unittest.TestCase):
    def client(self, connection):
        factory = mock.Mock(return_value=connection)
        client = hil.HttpClient({"address": "192.0.2.10", "port": 80,
                                 "host_header": "board.example"},
                                "192.0.2.20", 2, connection_factory=factory)
        return client, factory

    def test_explicit_authorization_guard_precedes_files_and_network(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(hil, "load_json") as load, \
                mock.patch.object(hil.socket, "getaddrinfo") as resolve:
            result = hil.main(["--endpoint", "http://192.0.2.10", "--manifest", "/missing",
                               "--run-id", "guard-test"])
        self.assertEqual(result, 1)
        load.assert_not_called()
        resolve.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["failure"],
                         "explicit_allow_auth_attempts_required")

    def test_private_permissions_and_identity_pins_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private.json"
            path.write_text('{"admin_password":"never-output-this"}')
            path.chmod(0o600)
            self.assertEqual(hil.load_json(path, private=True)["admin_password"],
                             "never-output-this")
            path.chmod(0o644)
            with self.assertRaisesRegex(hil.CheckFailure, "private_file_requires_mode"):
                hil.load_json(path, private=True)
        self.assertEqual(hil.validate_manifest(manifest()), manifest())
        incomplete = manifest()
        del incomplete["status"]["node_id"]
        with self.assertRaises(hil.CheckFailure):
            hil.validate_manifest(incomplete)
        incomplete = manifest()
        incomplete["config_expected"] = {}
        with self.assertRaises(hil.CheckFailure):
            hil.validate_manifest(incomplete)
        incomplete["config_expected"] = {"radio": {}}
        with self.assertRaises(hil.CheckFailure):
            hil.validate_manifest(incomplete)

    def test_source_addresses_must_be_distinct_and_room_policy_explicit(self):
        value = {"admin_password": "secret", "source_ips": ["192.0.2.20", "192.0.2.21"]}
        hil.validate_private(value, "webconfig")
        with self.assertRaisesRegex(hil.CheckFailure, "read_only_expectation"):
            hil.validate_private(value, "room")
        value["source_ips"] = ["192.0.2.20", "192.0.2.20"]
        with self.assertRaisesRegex(hil.CheckFailure, "must_differ"):
            hil.validate_private(value, "webconfig")
        value["source_ips"] = [True, "192.0.2.20"]
        with self.assertRaises(hil.CheckFailure):
            hil.validate_private(value, "webconfig")

    def test_http_binds_and_observes_source_and_preserves_origin_host(self):
        connection = FakeConnection(FakeResponse(body={"auth": False}))
        client, factory = self.client(connection)
        reply = client.request("GET", "/api/status", use_cookie=False)
        factory.assert_called_once_with("192.0.2.10", 80, timeout=2,
                                        source_address=("192.0.2.20", 0))
        self.assertEqual(connection.sent[2]["headers"]["Host"], "board.example")
        self.assertNotIn("Cookie", connection.sent[2]["headers"])
        self.assertEqual(reply.body, {"auth": False})
        self.assertTrue(connection.closed)
        connection.source = "192.0.2.99"
        with self.assertRaisesRegex(hil.CheckFailure, "source_binding"):
            client.request("GET", "/api/status")

    def test_mutating_room_or_config_operations_never_connect(self):
        connection = FakeConnection(FakeResponse())
        client, factory = self.client(connection)
        with self.assertRaises(hil.CheckFailure):
            client.request("POST", "/api/config", {"set": {"name": "change"}})
        with self.assertRaises(hil.CheckFailure):
            client.request("POST", "/api/room", {"op": "board.save", "password": "secret"})
        factory.assert_not_called()

    def test_json_failures_and_transport_exceptions_do_not_leak_private_text(self):
        connection = FakeConnection(FakeResponse(), error=OSError("password=private-value"))
        client, _ = self.client(connection)
        with self.assertRaisesRegex(hil.CheckFailure, "http_transport_or_json_failure") as error:
            client.request("GET", "/api/status")
        self.assertNotIn("private-value", str(error.exception))
        self.assertTrue(connection.closed)
        connection.error = None
        connection.response.headers = [("Content-Type", "text/html")]
        with self.assertRaisesRegex(hil.CheckFailure, "not_json_content_type"):
            client.request("GET", "/api/status")

    def test_success_cookie_is_private_and_error_response_keeps_existing_session(self):
        connection = FakeConnection(FakeResponse(body={"ok": True}, headers=[
            ("Content-Type", "application/json"),
            ("Set-Cookie", "wcs=" + "a" * 32 + "; HttpOnly; SameSite=Lax; Path=/")]))
        client, _ = self.client(connection)
        client.login("never-print-password")
        self.assertEqual(client.cookie, "wcs=" + "a" * 32)
        connection.response = FakeResponse(429, {"error": hil.SHARED_ERROR})
        self.assertEqual(client.login("never-print-password").status, 429)
        self.assertEqual(client.cookie, "wcs=" + "a" * 32)

    def test_room_retries_busy_and_polls_owned_200_json_error_without_cookie(self):
        client = mock.Mock()
        client.request.side_effect = [
            hil.Reply(429, {"error": "room service busy"}, [], 0, 0),
            hil.Reply(202, {"accepted": True}, [], 0, 0),
            hil.Reply(202, {"pending": True}, [], 0, 0),
            hil.Reply(200, {"error": hil.ROOM_ERROR}, [], 0, 0)]
        room = hil.RoomClient(client, 12, clock=lambda: 0, sleep=lambda _: None)
        reply = room.status("private-room-password")
        self.assertEqual(reply.status, 200)
        self.assertEqual(reply.body["error"], hil.ROOM_ERROR)
        calls = client.request.call_args_list
        self.assertEqual([call.args[1] for call in calls],
                         ["/api/room", "/api/room", "/api/room/result", "/api/room/result"])
        self.assertEqual(calls[0].args[2], calls[1].args[2])
        self.assertEqual({call.kwargs["headers"]["X-Room-Token"] for call in calls}, {room.token})
        self.assertEqual({call.kwargs["headers"]["X-Room-Seq"] for call in calls}, {"1"})
        self.assertTrue(all(call.kwargs["use_cookie"] is False for call in calls))

    def test_room_reboot_is_failure_and_200_transport_is_not_enough(self):
        client = mock.Mock()
        room = hil.RoomClient(client, 12, clock=lambda: 0, sleep=lambda _: None)
        client.request.side_effect = [
            hil.Reply(202, {"accepted": True}, [], 0, 0),
            hil.Reply(200, {"role": 3, "boot": 1}, [], 0, 0),
            hil.Reply(202, {"accepted": True}, [], 0, 0),
            hil.Reply(200, {"role": 3, "boot": 2}, [], 0, 0)]
        room.status("secret")
        with self.assertRaisesRegex(hil.CheckFailure, "reboot"):
            room.status("secret")
        client.request.side_effect = [hil.Reply(200, {"ok": True}, [], 0, 0)]
        with self.assertRaisesRegex(hil.CheckFailure, "submission_rejected"):
            room.status("secret")

    def test_public_read_only_browser_access_uses_guest_wire_role_zero(self):
        # Public readers are GUEST (0), distinct from an explicitly assigned
        # READ_ONLY (1) ACL role. The live board exposed this distinction.
        acceptance = hil.Acceptance.__new__(hil.Acceptance)
        acceptance.private = {"room_public_read_only": True}
        actor = mock.Mock()
        actor.status.return_value = hil.Reply(200, {"role": 0}, [], 0, 0)
        acceptance.room_empty(actor)
        acceptance.room_wrong(actor, "wrong-password")
        actor.status.return_value = hil.Reply(200, {"role": 1}, [], 0, 0)
        with self.assertRaisesRegex(hil.CheckFailure, "public_read_only_access"):
            acceptance.room_empty(actor)

    def test_summary_and_report_exclude_private_inputs_and_mark_hash_as_external(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            identity, private, report = [directory / name for name in
                                         ("identity.json", "private.json", "report.json")]
            identity.write_text(json.dumps(manifest()))
            private.write_text(json.dumps({"admin_password": "never-output-password",
                                          "source_ips": ["192.0.2.20", "192.0.2.21"]}))
            private.chmod(0o600)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), \
                    mock.patch.object(hil, "resolve_endpoint", return_value={}), \
                    mock.patch.object(hil, "Acceptance"):
                result = hil.main(["--endpoint", "http://192.0.2.10", "--manifest", str(identity),
                                   "--secrets-file", str(private), "--run-id", "private-output-test",
                                   "--scenario", "webconfig", "--allow-auth-attempts",
                                   "--report", str(report)])
            self.assertEqual(result, 0)
            for text in (output.getvalue(), report.read_text()):
                for sensitive in ("never-output-password", "192.0.2.10", "192.0.2.20",
                                  "192.0.2.21", "private-node-name", "0123456789abcdef"):
                    self.assertNotIn(sensitive, text)
            self.assertFalse(json.loads(report.read_text())["live_flash_hash_verified"])
            # A report path is an exclusive run artifact, never silently replaced.
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(hil, "resolve_endpoint") as resolve:
                result = hil.main(["--endpoint", "http://192.0.2.10", "--manifest", str(identity),
                                   "--secrets-file", str(private), "--run-id", "private-output-test",
                                   "--scenario", "webconfig", "--allow-auth-attempts",
                                   "--report", str(report)])
            self.assertEqual(result, 1)
            resolve.assert_not_called()


class BrowserStartupTests(unittest.TestCase):
    def websocket(self):
        return SimpleNamespace(
            WebSocketTimeoutException=type("SocketTimeout", (Exception,), {}),
            WebSocketConnectionClosedException=type("SocketClosed", (Exception,), {}))

    def test_partial_observer_socket_is_closed_after_enable_failure(self):
        websocket = self.websocket()
        for failed_method in ("Log.enable", "Network.enable"):
            with self.subTest(failed_method=failed_method):
                socket = mock.Mock()

                def initialize(page, *args):
                    page.socket = socket

                def enable(_page, method, *args):
                    if method == failed_method:
                        raise websocket.WebSocketConnectionClosedException()

                with mock.patch.object(browser_hil.CDP, "__init__", initialize), \
                        mock.patch.object(browser_hil.PassiveCDP, "call", enable), \
                        self.assertRaises(websocket.WebSocketConnectionClosedException):
                    browser_hil.PassiveCDP(websocket, 12345, Path("/owned"), "http://127.0.0.1:1234")
                socket.close.assert_called_once_with()

    def test_owned_page_startup_retries_both_transient_websocket_errors(self):
        websocket = self.websocket()
        page = object()
        process = SimpleNamespace(poll=lambda: None)
        with mock.patch.object(browser_hil, "PassiveCDP", side_effect=[
                websocket.WebSocketTimeoutException(),
                websocket.WebSocketConnectionClosedException(), page]) as attach, \
                mock.patch.object(browser_hil.time, "sleep") as pause:
            actual = browser_hil.wait_for_room_browser(
                websocket, 12345, Path("/owned"), "http://127.0.0.1:1234", process)
        self.assertIs(actual, page)
        self.assertEqual(attach.call_count, 3)
        self.assertEqual(pause.call_count, 2)

    def test_ownership_refusal_is_not_retried(self):
        with mock.patch.object(browser_hil, "PassiveCDP", side_effect=hil.CheckFailure(
                "CDP_browser_does_not_match_owned_profile")) as attach, \
                mock.patch.object(browser_hil.time, "sleep") as pause, \
                self.assertRaisesRegex(hil.CheckFailure, "does_not_match_owned_profile"):
            browser_hil.wait_for_room_browser(self.websocket(), 12345, Path("/owned"),
                                              "http://127.0.0.1:1234",
                                              SimpleNamespace(poll=lambda: None))
        attach.assert_called_once()
        pause.assert_not_called()


if __name__ == "__main__":
    unittest.main()
