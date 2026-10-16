"""Companion USB/TCP identity compatibility tests; no radios or transmissions."""

import argparse
import contextlib
import io
import subprocess
import unittest
from unittest import mock

import lora_ota as ota


KEY = "a5" * 32
VERSION = "Companion v1.17.1.5 (protocol 1, build test)"


def banner(key=KEY, name="W4JEC MC OTA2", *, full=True):
    role = "Full Companion" if full else "Companion"
    return (
        f"\r\n===== MeshCore {role} Terminal =====\r\n\r\n"
        f"WELCOME  {name}\r\n{key}\r\nCompanion v1.17.1.5\r\n"
        "  (enter 'help' for commands)\r\n\r\n> "
    )


def connection(greeting=None, reply=VERSION, *, chunks=None):
    conn = mock.MagicMock()
    conn.__enter__.return_value = conn
    conn.recv.side_effect = chunks or [
        (greeting if greeting is not None else banner()).encode("utf-8"),
        (reply + "\r\n> ").encode("utf-8"),
    ]
    return conn


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            source_cli_serial=None, source_serial=None,
            source_cli_tcp="192.0.2.10:5002", source_baud=115200,
            meshcli="meshcli", source_full_companion=True,
        )
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_full_tcp_uses_fresh_banner_and_supported_ver_not_get_public_key(self):
        conn = connection()
        with mock.patch.object(ota.socket, "create_connection", return_value=conn):
            self.assertEqual(ota.read_source_public_key_bounded(self.args), KEY)
        conn.sendall.assert_called_once_with(b"ver\r\n")

    def test_tcp_fragmented_banner_and_version_reply(self):
        greeting = banner(KEY.upper()).encode("utf-8")
        conn = connection(chunks=[greeting[:41], greeting[41:103], greeting[103:], VERSION.encode(), b"\r\n> "])
        with mock.patch.object(ota.socket, "create_connection", return_value=conn):
            self.assertEqual(ota.read_source_public_key_bounded(self.args), KEY)

    def test_tcp_probe_does_not_reuse_previous_connection_identity(self):
        first, second = connection(), connection(banner("b6" * 32))
        with mock.patch.object(ota.socket, "create_connection", side_effect=[first, second]) as connect:
            self.assertEqual(ota.read_source_public_key_bounded(self.args), KEY)
            self.assertEqual(ota.read_source_public_key_bounded(self.args), "b6" * 32)
        self.assertEqual(connect.call_count, 2)

    def test_usb_ascii_first_full_source_gets_fresh_wrapped_banner(self):
        self.args.source_cli_tcp = None
        self.args.source_serial = "COM42"
        self.args.source_companion_terminal = False
        result = subprocess.CompletedProcess([], 0, banner() + VERSION + "\r\n> \r\nBinary mode\r\n", "")
        with mock.patch.object(ota, "run_checked", return_value=result) as run:
            self.assertEqual(ota.read_source_public_key_bounded(self.args), KEY)
        command = run.call_args.args[0]
        self.assertIn("COM42", command)
        self.assertEqual(command[-1],
            f"{ota.COMPANION_TERMINAL_STOP}\r{ota.COMPANION_TERMINAL_START}\rver\r{ota.COMPANION_TERMINAL_STOP}\r")
        self.assertNotIn("get public.key", command[-1])
        self.assertFalse(self.args.source_companion_terminal)  # No persistent mode flag change.

    def test_usb_identity_accepts_error_words_in_valid_welcome_names(self):
        self.args.source_cli_tcp = None
        self.args.source_serial = "COM42"
        for name in ("Error Ridge", "ERR Station", "Unknown command Ridge"):
            result = subprocess.CompletedProcess([], 0, banner(name=name) + VERSION + "\r\n", "")
            with self.subTest(name=name), mock.patch.object(ota, "run_checked", return_value=result):
                self.assertEqual(ota.read_source_public_key_bounded(self.args), KEY)

    def test_usb_ordinary_commands_accept_error_words_in_welcome_and_get_values(self):
        self.args.source_cli_tcp = None
        self.args.source_serial = "COM42"
        for command, reply in (("ota status", "OTA seeder | install:disabled | serving:0"),
                               ("get name", "> Error Ridge")):
            output = banner(name="Error Ridge") + "\r\n  -> " + reply + "\r\n> "
            result = subprocess.CompletedProcess([], 0, output, "")
            with self.subTest(command=command), mock.patch.object(ota, "run_checked", return_value=result):
                self.assertIn(reply, ota.source_cli_command(self.args, command))

    def test_tcp_identity_and_ordinary_commands_accept_error_words_in_names(self):
        for command, reply in (("ver", VERSION), ("get name", "> Error Ridge"),
                               ("ota status", "OTA seeder | install:disabled | serving:0")):
            conn = connection(banner(name="Error Ridge"), reply)
            with self.subTest(command=command), mock.patch.object(
                    ota.socket, "create_connection", return_value=conn):
                if command == "ver":
                    self.assertEqual(ota.read_source_public_key_bounded(self.args), KEY)
                else:
                    self.assertEqual(ota.source_cli_command(self.args, command), reply)

    def test_usb_real_rejections_survive_valid_welcome_and_identity_modes(self):
        self.args.source_cli_tcp = None
        self.args.source_serial = "COM42"
        for reply in ("ERR permission denied", "ERROR: unavailable", "FAIL", "Unknown command"):
            for identity in (False, True):
                output = banner(name="Error Ridge") + "\r\n  -> " + reply + "\r\n> "
                result = subprocess.CompletedProcess([], 0, output, "")
                with self.subTest(reply=reply, identity=identity), mock.patch.object(
                        ota, "run_checked", return_value=result):
                    with self.assertRaisesRegex(ota.OtaError, "source rejected"):
                        ota.source_cli_command(self.args, "ver" if identity else "ota status",
                                               full_companion_identity=identity)

    def test_valid_usb_banner_cannot_hide_errors_before_or_inside_it(self):
        self.args.source_cli_tcp = None
        self.args.source_serial = "COM42"
        welcome = banner(name="Error Ridge")
        for output in ("ERROR: old mode failed\r\n" + welcome + VERSION,
                       welcome.replace("\r\n> ", "\r\nERR terminal unavailable\r\n> ") + VERSION):
            result = subprocess.CompletedProcess([], 0, output, "")
            with self.subTest(output=output), mock.patch.object(ota, "run_checked", return_value=result):
                with self.assertRaisesRegex(ota.OtaError, "source rejected"):
                    ota.read_source_public_key_bounded(self.args)

    def test_tcp_real_rejections_remain_errors_after_valid_welcome(self):
        for reply in ("ERR permission denied", "ERROR: unavailable", "FAIL", "Unknown command"):
            conn = connection(banner(name="Error Ridge"), reply)
            with self.subTest(reply=reply), mock.patch.object(ota.socket, "create_connection", return_value=conn):
                with self.assertRaisesRegex(ota.OtaError, "source rejected"):
                    ota.source_cli_command(self.args, "ota status")

    def test_usb_can_use_explicit_cli_port(self):
        self.args.source_cli_tcp = None
        self.args.source_cli_serial = "COM43"
        self.args.source_serial = "COM42"
        result = subprocess.CompletedProcess([], 0, banner() + VERSION + "\r\n", "")
        with mock.patch.object(ota, "run_checked", return_value=result) as run:
            self.assertEqual(ota.read_source_public_key_bounded(self.args), KEY)
        self.assertIn("COM43", run.call_args.args[0])
        self.assertNotIn("COM42", run.call_args.args[0])

    def test_usb_meshcli_trimmed_prompt_is_accepted(self):
        self.args.source_cli_tcp = None
        self.args.source_serial = "COM42"
        rendered = "\n".join(line.rstrip() for line in (banner() + "\r\n" + VERSION).split("\n"))
        result = subprocess.CompletedProcess([], 0, rendered, "")
        with mock.patch.object(ota, "run_checked", return_value=result):
            self.assertEqual(ota.read_source_public_key_bounded(self.args), KEY)

    def test_usb_banner_alone_is_not_a_live_version_reply(self):
        self.args.source_cli_tcp = None
        self.args.source_serial = "COM42"
        result = subprocess.CompletedProcess([], 0, banner() + "Binary mode\r\n", "")
        with mock.patch.object(ota, "run_checked", return_value=result):
            with self.assertRaisesRegex(ota.OtaError, "live ver reply"):
                ota.read_source_public_key_bounded(self.args)

    def test_unknown_command_falls_back_only_to_fresh_companion_identity(self):
        self.args.source_full_companion = False
        legacy = connection(reply="ERROR: unknown command: get public.key")
        companion = connection()
        with mock.patch.object(ota.socket, "create_connection", side_effect=[legacy, companion]):
            self.assertEqual(ota.read_source_public_key_bounded(self.args), KEY)
        legacy.sendall.assert_called_once_with(b"get public.key\r\n")
        companion.sendall.assert_called_once_with(b"ver\r\n")
        self.assertTrue(self.args.source_full_companion)

    def test_repeater_keeps_its_supported_get_public_key_command(self):
        self.args.source_full_companion = False
        conn = connection("Repeater console\r\n> ", "> " + KEY)
        with mock.patch.object(ota.socket, "create_connection", return_value=conn):
            self.assertEqual(ota.read_source_public_key_bounded(self.args), KEY)
        conn.sendall.assert_called_once_with(b"get public.key\r\n")

    def test_timeouts_and_denials_do_not_trigger_companion_fallback(self):
        self.args.source_full_companion = False
        for error in (ota.TransmissionError("timeout"), ota.OtaError("permission denied")):
            with self.subTest(error=error), mock.patch.object(ota, "source_cli_command", side_effect=error) as command:
                with self.assertRaises(type(error)):
                    ota.read_source_public_key_bounded(self.args)
                command.assert_called_once_with(self.args, "get public.key", bounded=True, deadline=None)

    def test_malformed_repeater_key_does_not_trigger_fallback(self):
        self.args.source_full_companion = False
        with mock.patch.object(ota, "source_cli_command", return_value="> incomplete") as command:
            with self.assertRaisesRegex(ota.OtaError, "exact public key"):
                ota.read_source_public_key_bounded(self.args)
        command.assert_called_once()

    def test_full_tcp_does_not_accept_wrong_or_multiple_version_replies(self):
        for reply in ("OK", "Companion not-a-version", VERSION + "\r\n" + VERSION):
            with self.subTest(reply=reply), mock.patch.object(ota.socket, "create_connection", return_value=connection(reply=reply)):
                with self.assertRaisesRegex(ota.OtaError, "live ver reply"):
                    ota.read_source_public_key_bounded(self.args)

    def test_missing_or_ambiguous_banner_stops_before_sending_command(self):
        for greeting in (
            "Old OTA console\r\n> ", banner().replace(KEY, "incomplete"),
            banner().replace(KEY, KEY + "\r\n" + "b6" * 32),
            banner().replace("WELCOME", "RECEIVED ADVERT"),
        ):
            conn = connection(greeting)
            with self.subTest(greeting=greeting), mock.patch.object(ota.socket, "create_connection", return_value=conn):
                with self.assertRaises(ota.OtaError):
                    ota.read_source_public_key_bounded(self.args)
            conn.sendall.assert_not_called()

    def test_shared_tcp_expected_key_mismatch_still_stops_before_command(self):
        self.args.shared_source_public_key = "b6" * 32
        conn = connection()
        with mock.patch.object(ota.socket, "create_connection", return_value=conn):
            with self.assertRaisesRegex(ota.OtaError, "terminal identity mismatch"):
                ota.read_source_public_key_bounded(self.args)
        conn.sendall.assert_not_called()

    def test_status_detects_full_role_and_contact_binding_uses_supported_identity(self):
        self.args.source_full_companion = False
        self.args.target = "destination"
        self.args.relay_values = []
        self.args.source_shares_controller = False
        self.args.source_contact_value = None
        target_key = "c7" * 32
        controller = mock.Mock()
        controller._run.return_value = [{
            target_key: {"public_key": target_key, "adv_name": "destination"},
            KEY: {"public_key": KEY, "adv_name": "Saved old source name"},
        }]
        status = connection(reply="OTA seeder | install:disabled | serving:0")
        identity = connection(banner(name="W4JEC MC OTA2 \U0001f4e1"))
        with mock.patch.object(ota.socket, "create_connection", side_effect=[status, identity]):
            ota.preflight_source_cli(self.args)
            self.assertTrue(self.args.source_full_companion)
            ota.bind_contact_selectors(controller, self.args)
        self.assertEqual(self.args.source_contact_value, KEY)
        status.sendall.assert_called_once_with(b"ota status\r\n")
        identity.sendall.assert_called_once_with(b"ver\r\n")
        controller.remote_command.assert_not_called()

    def test_deadline_is_forwarded_to_companion_probe(self):
        with mock.patch.object(ota, "source_cli_command", return_value="> " + KEY) as command:
            self.assertEqual(ota.read_source_public_key_bounded(self.args, deadline=123.0), KEY)
        command.assert_called_once_with(self.args, "ver", bounded=True, deadline=123.0, full_companion_identity=True)

    def test_identity_mode_refuses_non_read_only_commands(self):
        with self.assertRaisesRegex(ota.OtaError, "read-only ver"):
            ota.source_cli_command(self.args, "reboot", full_companion_identity=True)


class TerminalBannerTests(unittest.TestCase):
    def test_both_banner_roles_and_emoji_names(self):
        for full in (True, False):
            value = banner(KEY.upper(), "Name \U0001f4e1", full=full)
            self.assertEqual(ota.parse_source_terminal_banner(value + VERSION), (KEY, VERSION))

    def test_duplicate_banners_are_not_one_identity(self):
        with self.assertRaisesRegex(ota.OtaError, "one fresh"):
            ota.parse_source_terminal_banner(banner() + banner())

    def test_banner_without_prompt_is_incomplete(self):
        with self.assertRaisesRegex(ota.OtaError, "prompt"):
            ota.parse_source_terminal_banner(banner().removesuffix("> "))


if __name__ == "__main__":
    unittest.main()
