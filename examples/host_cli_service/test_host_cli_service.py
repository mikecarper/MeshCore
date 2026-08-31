from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import host_cli_service as service
import meshcore_clock_control as clock_helper


PUBLIC_KEY = "11" * 32
PRIVATE_KEY = "22" * 64
SIGNATURE = bytes([0xA5]) * 64


def request_payload(
    text: str = "cpu-temp",
    request_id: str = "89ABCDEF",
) -> tuple[str, bytes]:
    encoded = base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()
    signed = f"HOSTCLI/1 REQUEST {request_id} 0123456789ABCDEF {encoded}"
    message = "DEBUG " + signed + " " + SIGNATURE.hex().upper()
    payload = json.dumps(
        {"type": "DEBUG", "origin_id": PUBLIC_KEY, "message": message}
    ).encode()
    return signed, payload


def claim_payload(
    challenge: str = "FEDCBA9876543210",
    request_id: str = "89ABCDEF",
) -> tuple[str, bytes]:
    signed = (
        f"HOSTCLI/1 CLAIMED {request_id} 0123456789ABCDEF {challenge}"
    )
    message = "DEBUG " + signed + " " + SIGNATURE.hex().upper()
    payload = json.dumps(
        {"type": "DEBUG", "origin_id": PUBLIC_KEY, "message": message}
    ).encode()
    return signed, payload


class HostRequestTests(unittest.TestCase):
    def test_accepts_only_a_valid_repeater_signature(self) -> None:
        expected, payload = request_payload()

        def verifier(signature: bytes, message: bytes, public: bytes) -> bool:
            return (
                signature == SIGNATURE
                and message == expected.encode()
                and public == bytes.fromhex(PUBLIC_KEY)
            )

        request = service.parse_and_verify_request(
            payload, PUBLIC_KEY, verifier=verifier,
        )
        self.assertEqual(
            service.HostRequest(
                "89ABCDEF", "0123456789ABCDEF", "cpu-temp"
            ),
            request,
        )

    def test_rejects_forged_content_and_wrong_origin(self) -> None:
        _expected, payload = request_payload("cpu-temp")
        forged = payload.replace(b"Y3B1LXRlbXA", b"ZXJhc2U")
        with self.assertRaisesRegex(ValueError, "signature"):
            service.parse_and_verify_request(
                forged, PUBLIC_KEY, verifier=lambda *_args: False,
            )

        document = json.loads(payload)
        document["origin_id"] = "33" * 32
        with self.assertRaisesRegex(ValueError, "origin"):
            service.parse_and_verify_request(
                json.dumps(document), PUBLIC_KEY,
                verifier=lambda *_args: True,
            )

    def test_ignores_unrelated_debug_records(self) -> None:
        payload = json.dumps(
            {"type": "DEBUG", "origin_id": PUBLIC_KEY,
             "message": "DEBUG ordinary log"}
        )
        self.assertIsNone(
            service.parse_and_verify_request(
                payload, PUBLIC_KEY, verifier=lambda *_args: True,
            )
        )

    def test_accepts_only_a_valid_signed_claim(self) -> None:
        expected, payload = claim_payload()

        def verifier(signature: bytes, message: bytes, public: bytes) -> bool:
            return (
                signature == SIGNATURE
                and message == expected.encode()
                and public == bytes.fromhex(PUBLIC_KEY)
            )

        self.assertEqual(
            service.HostClaim(
                "89ABCDEF", "0123456789ABCDEF", "FEDCBA9876543210"
            ),
            service.parse_and_verify_claim(
                payload, PUBLIC_KEY, verifier=verifier
            ),
        )
        with self.assertRaisesRegex(ValueError, "signature"):
            service.parse_and_verify_claim(
                payload, PUBLIC_KEY, verifier=lambda *_args: False
            )

    def test_rejects_a_zero_claim_challenge(self) -> None:
        _signed, payload = claim_payload("0000000000000000")
        with self.assertRaisesRegex(ValueError, "must not be zero"):
            service.parse_and_verify_claim(
                payload, PUBLIC_KEY, verifier=lambda *_args: True
            )


class RequestHandlerTests(unittest.TestCase):
    def test_reads_pi_cpu_temperature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "temp"
            path.write_text("47250\n", encoding="ascii")
            self.assertEqual(
                service.HostResult("CPU 47.2 C"),
                service.handle_request("cpu-temp", path),
            )

    def test_never_executes_or_accepts_injected_commands(self) -> None:
        response = service.handle_request(
            "cpu-temp\r\nerase", Path("/does/not/matter")
        )
        self.assertEqual(
            service.HostResult("Err - unsupported host request"), response
        )

        request = service.HostRequest(
            "12345678", "FEDCBA9876543210", "cpu-temp"
        )
        command = service.make_serial_reply(request, "OK\r\nerase")
        self.assertEqual(
            "host.reply 12345678 FEDCBA9876543210 OK  erase", command
        )
        self.assertNotIn("\r", command)
        self.assertNotIn("\n", command)
        self.assertEqual(
            "OK 31mnot-a-terminal-control",
            service.bounded_line_text("OK\N{CONTROL SEQUENCE INTRODUCER}31m"
                                      "not-a-terminal-control"),
        )

    def test_exact_fixed_commands_and_opt_in_recovery_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uptime = root / "uptime"
            load = root / "loadavg"
            memory = root / "meminfo"
            uptime.write_text("183845.2 0.0\n", encoding="ascii")
            load.write_text("0.12 0.34 0.56 1/1 1\n", encoding="ascii")
            memory.write_text(
                "MemTotal: 1024000 kB\nMemAvailable: 512000 kB\n",
                encoding="ascii",
            )
            kwargs = {
                "uptime_path": uptime,
                "load_path": load,
                "memory_path": memory,
                "disk_path": root,
            }
            self.assertEqual(
                "Uptime 2d 3h 4m",
                service.handle_request("uptime", root, **kwargs).text,
            )
            self.assertEqual(
                "Load 0.12 0.34 0.56",
                service.handle_request("load", root, **kwargs).text,
            )
            self.assertEqual(
                "Memory 500/1000 MiB available",
                service.handle_request("memory", root, **kwargs).text,
            )
            self.assertTrue(
                service.handle_request("disk-free", root, **kwargs).text.startswith(
                    "Disk "
                )
            )

        disabled = service.handle_request("reboot", Path("/unused"))
        self.assertEqual("Err - host reboot is disabled", disabled.text)
        enabled = service.handle_request(
            "reboot", Path("/unused"), allow_reboot=True
        )
        self.assertEqual(service.HostResult("", host_action="reboot"), enabled)
        self.assertEqual(
            "Err - host network restart is disabled",
            service.handle_request("network restart", Path("/unused")).text,
        )
        self.assertEqual(
            service.HostResult("", host_action="network-restart"),
            service.handle_request(
                "network restart",
                Path("/unused"),
                allow_network_restart=True,
            ),
        )
        operation_id = "A1" * 16
        self.assertEqual(
            service.HostResult(
                "", host_action="status", operation_id=operation_id
            ),
            service.handle_request(
                f"action status {operation_id}",
                Path("/unused"),
                allow_reboot=True,
            ),
        )
        self.assertEqual(
            service.HostResult("Err - unsupported host request"),
            service.handle_request("reboot now", Path("/unused"),
                                   allow_reboot=True),
        )
        for injected in (
            "network restart now",
            "network restart\nreboot",
            f"action status {operation_id};reboot",
            f"action status {operation_id.lower()}",
        ):
            with self.subTest(injected=injected):
                self.assertIsNone(
                    service.handle_request(
                        injected,
                        Path("/unused"),
                        allow_reboot=True,
                        allow_network_restart=True,
                    ).host_action
                )

    def test_clock_status_is_read_only_and_strictly_bounded(self) -> None:
        status_result = mock.Mock(returncode=0, stdout="yes\n")
        with mock.patch.object(service.time, "time", return_value=1788147000), \
             mock.patch.object(
                 service.subprocess, "run", return_value=status_result
             ) as run:
            status = service.handle_request("clock status", Path("/unused"))
        self.assertEqual(
            "Clock epoch 1788147000; NTP synchronized yes", status.text
        )
        self.assertEqual(
            [
                "/usr/bin/timedatectl", "show",
                "--property=NTPSynchronized", "--value",
            ],
            run.call_args.args[0],
        )
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(
            service.CLOCK_STATUS_CHILD_TIMEOUT_SECONDS,
            run.call_args.kwargs["timeout"],
        )

        disabled = service.handle_request(
            "clock set 1788147000", Path("/unused")
        )
        self.assertEqual("Err - host clock control is disabled", disabled.text)
        disabled_sync = service.handle_request("clock sync", Path("/unused"))
        self.assertEqual(
            "Err - host clock control is disabled", disabled_sync.text
        )

        rejected = (
            "clock set",
            "clock set ",
            "clock set +1788147000",
            "clock set -1",
            "clock set 01788147000",
            "clock set 1788147000 extra",
            "clock set 1788147000\nreboot",
            "clock set 999999999999999999999999999999999999",
        )
        with mock.patch.object(service, "_exchange_clock_control") as exchange:
            for request in rejected:
                with self.subTest(request=request):
                    result = service.handle_request(
                        request, Path("/unused"), allow_clock_control=True
                    )
                    self.assertTrue(result.text.startswith("Err -"))
        exchange.assert_not_called()

    def test_clock_control_uses_only_the_fixed_unix_socket_protocol(self) -> None:
        with mock.patch.object(
            service,
            "_exchange_clock_control",
            return_value=(
                "OK clock set to 1788147000; NTP sync requested"
            ),
        ) as exchange, mock.patch.object(service.subprocess, "run") as run:
            result = service.handle_request(
                "clock set 1788147000",
                Path("/unused"),
                allow_clock_control=True,
            )
        self.assertEqual(
            "OK - clock set to 1788147000; NTP sync requested", result.text
        )
        exchange.assert_called_once_with("set 1788147000")
        run.assert_not_called()

        with mock.patch.object(
            service,
            "_exchange_clock_control",
            return_value="OK NTP sync requested",
        ) as exchange, mock.patch.object(service.subprocess, "run") as run:
            result = service.handle_request(
                "clock sync", Path("/unused"), allow_clock_control=True
            )
        self.assertEqual("OK - NTP sync requested", result.text)
        exchange.assert_called_once_with("sync")
        run.assert_not_called()

    def test_clock_status_uses_chrony_when_systemd_marker_is_wrong(self) -> None:
        systemd = mock.Mock(returncode=0, stdout="no\n")
        chrony = mock.Mock(
            returncode=0,
            stdout=(
                "Stratum         : 4\n"
                "System time     : 0.000000000 seconds fast of NTP time\n"
                "Leap status     : Normal\n"
            ),
        )
        with mock.patch.object(service.time, "time", return_value=1788147000), \
             mock.patch.object(
                 service.subprocess, "run", side_effect=[systemd, chrony]
             ) as run, mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(service.os, "access", return_value=True):
            result = service.handle_request("clock status", Path("/unused"))
        self.assertEqual(
            "Clock epoch 1788147000; NTP synchronized yes (chrony)",
            result.text,
        )
        self.assertEqual(
            ["/usr/bin/chronyc", "-n", "tracking"],
            run.call_args.args[0],
        )
        self.assertEqual(1.5, run.call_args.kwargs["timeout"])

    def test_clock_epoch_boundaries_in_both_layers(self) -> None:
        for epoch in (service.MIN_CLOCK_EPOCH, service.MAX_CLOCK_EPOCH):
            with self.subTest(epoch=epoch), mock.patch.object(
                service,
                "_exchange_clock_control",
                return_value=f"OK clock set to {epoch}; NTP sync requested",
            ) as exchange:
                result = service.handle_request(
                    f"clock set {epoch}",
                    Path("/unused"),
                    allow_clock_control=True,
                )
            self.assertEqual(
                f"OK - clock set to {epoch}; NTP sync requested", result.text
            )
            exchange.assert_called_once_with(f"set {epoch}")

        with mock.patch.object(service, "_exchange_clock_control") as exchange:
            for epoch in (
                service.MIN_CLOCK_EPOCH - 1,
                service.MAX_CLOCK_EPOCH + 1,
            ):
                with self.subTest(epoch=epoch):
                    result = service.handle_request(
                        f"clock set {epoch}",
                        Path("/unused"),
                        allow_clock_control=True,
                    )
                    self.assertIn("2020 through 2099", result.text)
        exchange.assert_not_called()

        for epoch in (
            clock_helper.MIN_CLOCK_EPOCH,
            clock_helper.MAX_CLOCK_EPOCH,
        ):
            self.assertEqual(
                ("set", epoch),
                clock_helper.parse_request(f"set {epoch}\n".encode()),
            )
        for epoch in (
            clock_helper.MIN_CLOCK_EPOCH - 1,
            clock_helper.MAX_CLOCK_EPOCH + 1,
        ):
            with self.assertRaises(ValueError):
                clock_helper.parse_request(f"set {epoch}\n".encode())

    def test_clock_helper_timeouts_are_bounded_and_fail_closed(self) -> None:
        self.assertLess(
            2 * clock_helper.CHILD_TIMEOUT_SECONDS,
            service.CLOCK_CONTROL_TIMEOUT_SECONDS,
        )
        self.assertLess(service.CLOCK_CONTROL_TIMEOUT_SECONDS, 6)
        timeout = subprocess.TimeoutExpired(["timedatectl"], 1.5)
        with mock.patch.object(
            clock_helper.subprocess, "run", side_effect=timeout
        ) as run:
            self.assertEqual(
                "NTP enable request failed", clock_helper.request_ntp_sync()
            )
        self.assertEqual(1, run.call_count)
        self.assertEqual(
            clock_helper.CHILD_TIMEOUT_SECONDS,
            run.call_args.kwargs["timeout"],
        )

        enabled = mock.Mock(returncode=0)
        with mock.patch.object(
            clock_helper.subprocess,
            "run",
            side_effect=[enabled, timeout],
        ) as run, mock.patch.object(
            clock_helper.Path, "is_file", return_value=False
        ):
            self.assertEqual(
                "systemd-timesyncd restart failed",
                clock_helper.request_ntp_sync(),
            )
        self.assertEqual(2, run.call_count)
        self.assertEqual(
            [
                "/usr/bin/systemctl",
                "restart",
                "systemd-timesyncd.service",
            ],
            run.call_args.args[0],
        )

        with mock.patch.object(
            clock_helper.subprocess, "run", side_effect=FileNotFoundError
        ) as run:
            self.assertEqual(
                "NTP enable request failed", clock_helper.request_ntp_sync()
            )
        self.assertEqual(1, run.call_count)

    def test_clock_set_never_disables_ntp_and_reports_partial_success(
        self,
    ) -> None:
        with mock.patch.object(clock_helper.time, "clock_settime") as settime, \
             mock.patch.object(
                 clock_helper,
                 "request_ntp_sync",
                 return_value="chrony step request failed",
             ) as sync, \
             mock.patch.object(clock_helper, "run_fixed") as run_fixed:
            self.assertEqual(
                (True, "chrony step request failed"),
                clock_helper.set_clock(1788147000),
            )
        settime.assert_called_once_with(
            clock_helper.time.CLOCK_REALTIME, 1788147000.0
        )
        sync.assert_called_once_with()
        run_fixed.assert_not_called()

        with mock.patch.object(
            clock_helper,
            "set_clock",
            return_value=(True, clock_helper.CHRONY_STEP_ERROR),
        ):
            self.assertEqual(
                "PARTIAL clock set to 1788147000; NTP enabled; "
                "chrony step request failed",
                clock_helper.process_request(b"set 1788147000\n"),
            )
        self.assertEqual(
            "Warning - clock set to 1788147000; NTP enabled; "
            "chrony step request failed",
            service._clock_control_result(
                "PARTIAL clock set to 1788147000; NTP enabled; "
                "chrony step request failed",
                "set",
                1788147000,
            ).text,
        )

        with mock.patch.object(
            clock_helper.time, "clock_settime", side_effect=OSError
        ), mock.patch.object(clock_helper, "request_ntp_sync") as sync:
            self.assertEqual(
                (False, None), clock_helper.set_clock(1788147000)
            )
        sync.assert_not_called()

    def test_reply_is_utf8_bounded(self) -> None:
        text = "x" * 161 + "\N{EURO SIGN}"
        bounded = service.bounded_line_text(text)
        self.assertLessEqual(len(bounded.encode()), service.MAX_REPLY_BYTES)
        self.assertEqual("x" * 161, bounded)

    def test_runs_only_a_fixed_program_with_validated_arguments(self) -> None:
        programs = {
            "fan": service.ProgramDefinition(
                alias="fan",
                argv=("/usr/local/bin/mesh-fan-control", "--source", "lora"),
                arguments=(
                    service.ProgramArgumentRule(
                        "state", "choice", choices=("on", "off")
                    ),
                    service.ProgramArgumentRule(
                        "minutes", "integer", minimum=1, maximum=60
                    ),
                ),
                timeout_seconds=3,
            )
        }
        completed = subprocess_result = mock.Mock(
            returncode=0, stdout="fan scheduled\n"
        )
        with mock.patch.object(
            service.subprocess, "run", return_value=completed
        ) as run:
            result = service.handle_request(
                "run fan on 15", Path("/unused"), programs=programs
            )

        self.assertEqual("fan scheduled", result.text)
        self.assertIs(completed, subprocess_result)
        argv = run.call_args.args[0]
        self.assertEqual(
            [
                "/usr/local/bin/mesh-fan-control",
                "--source",
                "lora",
                "on",
                "15",
            ],
            argv,
        )
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual("/", run.call_args.kwargs["cwd"])

    def test_rejects_program_alias_and_argument_injection(self) -> None:
        programs = {
            "fan": service.ProgramDefinition(
                alias="fan",
                argv=("/usr/local/bin/mesh-fan-control",),
                arguments=(
                    service.ProgramArgumentRule(
                        "state", "choice", choices=("on", "off")
                    ),
                ),
            )
        }
        rejected = (
            "run other on",
            "run fan --help",
            "run fan on;reboot",
            "run fan on\nreboot",
            "run fan on extra",
            "run fan -1",
        )
        with mock.patch.object(service.subprocess, "run") as run:
            for request in rejected:
                with self.subTest(request=request):
                    self.assertTrue(
                        service.handle_request(
                            request, Path("/unused"), programs=programs
                        ).text.startswith("Err -")
                    )
        run.assert_not_called()

    def test_loads_a_bounded_program_allowlist(self) -> None:
        executable = Path("/bin/echo").resolve()
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "programs.json"
            config.write_text(
                json.dumps(
                    {
                        "programs": {
                            "sample": {
                                "argv": [str(executable), "fixed"],
                                "timeout_seconds": 2,
                                "arguments": [
                                    {
                                        "name": "mode",
                                        "type": "choice",
                                        "choices": ["short", "full"],
                                    },
                                    {
                                        "name": "count",
                                        "type": "integer",
                                        "min": 1,
                                        "max": 5,
                                    },
                                    {
                                        "name": "label",
                                        "type": "token",
                                        "max_bytes": 12,
                                    },
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            config.chmod(0o644)
            programs = service.load_programs_file(config)

        self.assertEqual((str(executable), "fixed"), programs["sample"].argv)
        self.assertEqual(3, len(programs["sample"].arguments))

    def test_rejects_writable_program_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "programs.json"
            config.write_text('{"programs": {}}', encoding="ascii")
            config.chmod(0o666)
            with self.assertRaisesRegex(ValueError, "group/world writable"):
                service.load_programs_file(config)


class ClockControlServiceTests(unittest.TestCase):
    def test_daemon_protocol_rejects_injection_before_mutation(self) -> None:
        rejected = (
            b"",
            b"sync",
            b"sync\r\n",
            b" sync\n",
            b"sync \n",
            b"sync\nset 1788147000\n",
            b"set\n",
            b"set +1788147000\n",
            b"set -1788147000\n",
            b"set 01788147000\n",
            b"set 1788147000 extra\n",
            b"set 1788147000\x00\n",
            b"set 1788147000\nreboot",
            b"set 999999999999999999999999\n",
            b"\xff\n",
            b"x" * (clock_helper.MAX_REQUEST_BYTES + 1),
        )
        with mock.patch.object(clock_helper, "set_clock") as set_clock, \
             mock.patch.object(clock_helper, "request_ntp_sync") as sync:
            for request in rejected:
                with self.subTest(request=request):
                    with self.assertRaises(ValueError):
                        clock_helper.process_request(request)
        set_clock.assert_not_called()
        sync.assert_not_called()

        self.assertEqual(
            ("sync", None), clock_helper.parse_request(b"sync\n")
        )
        self.assertEqual(
            ("set", 1788147000),
            clock_helper.parse_request(b"set 1788147000\n"),
        )

    def test_daemon_reports_success_failure_and_partial_outcomes(self) -> None:
        with mock.patch.object(
            clock_helper, "request_ntp_sync", return_value=None
        ):
            self.assertEqual(
                "OK NTP sync requested",
                clock_helper.process_request(b"sync\n"),
            )
        with mock.patch.object(
            clock_helper,
            "request_ntp_sync",
            return_value=clock_helper.NTP_ENABLE_ERROR,
        ):
            self.assertEqual(
                "ERR NTP enable request failed",
                clock_helper.process_request(b"sync\n"),
            )
        with mock.patch.object(
            clock_helper,
            "request_ntp_sync",
            return_value=clock_helper.CHRONY_STEP_ERROR,
        ):
            self.assertEqual(
                "PARTIAL NTP enabled; chrony step request failed",
                clock_helper.process_request(b"sync\n"),
            )

        outcomes = (
            ((False, None), "ERR clock set failed"),
            (
                (True, None),
                "OK clock set to 1788147000; NTP sync requested",
            ),
            (
                (True, clock_helper.NTP_ENABLE_ERROR),
                "PARTIAL clock set to 1788147000; "
                "NTP enable request failed",
            ),
            (
                (True, clock_helper.TIMESYNCD_RESTART_ERROR),
                "PARTIAL clock set to 1788147000; NTP enabled; "
                "systemd-timesyncd restart failed",
            ),
        )
        for outcome, expected in outcomes:
            with self.subTest(outcome=outcome), mock.patch.object(
                clock_helper, "set_clock", return_value=outcome
            ):
                self.assertEqual(
                    expected,
                    clock_helper.process_request(b"set 1788147000\n"),
                )

    def test_peer_credentials_reject_wrong_uid_or_primary_gid(self) -> None:
        for credentials in ((123, 999, 456), (123, 123, 999)):
            connection = mock.Mock()
            with self.subTest(credentials=credentials), mock.patch.object(
                clock_helper, "peer_credentials", return_value=credentials
            ), mock.patch.object(
                clock_helper, "process_request"
            ) as process:
                self.assertFalse(
                    clock_helper.handle_connection(
                        connection, allowed_uid=123, allowed_gid=456
                    )
                )
            process.assert_not_called()
            connection.sendall.assert_called_once_with(
                b"ERR unauthorized peer\n"
            )

    def test_authorized_peer_gets_one_bounded_request(self) -> None:
        connection = mock.Mock()
        with mock.patch.object(
            clock_helper, "peer_credentials", return_value=(99, 123, 456)
        ), mock.patch.object(
            clock_helper, "receive_request", return_value=b"sync\n"
        ) as receive, mock.patch.object(
            clock_helper, "process_request", return_value="OK NTP sync requested"
        ) as process:
            self.assertTrue(
                clock_helper.handle_connection(
                    connection, allowed_uid=123, allowed_gid=456
                )
            )
        receive.assert_called_once_with(connection)
        process.assert_called_once_with(b"sync\n")
        connection.sendall.assert_called_once_with(b"OK NTP sync requested\n")

    def test_client_and_daemon_require_root_group_mode_0660_socket(self) -> None:
        safe = mock.Mock(
            st_mode=stat.S_IFSOCK | 0o660,
            st_uid=0,
            st_gid=456,
        )
        with mock.patch.object(Path, "lstat", return_value=safe):
            service.validate_clock_control_socket(
                Path("/run/test.sock"), expected_gid=456
            )
        clock_helper.validate_socket_metadata(safe, 456)

        listener = mock.Mock(family=socket.AF_UNIX)
        listener.getsockopt.side_effect = (socket.SOCK_STREAM, 1)
        listener.getsockname.return_value = "/run/test.sock"
        with mock.patch.object(Path, "lstat", return_value=safe):
            clock_helper.validate_listening_socket(
                listener,
                path=Path("/run/test.sock"),
                expected_gid=456,
            )

        wrong_type = mock.Mock(family=socket.AF_UNIX)
        wrong_type.getsockopt.return_value = socket.SOCK_SEQPACKET
        with self.assertRaisesRegex(ValueError, "SOCK_STREAM"):
            clock_helper.validate_listening_socket(
                wrong_type,
                path=Path("/run/test.sock"),
                expected_gid=456,
            )

        unsafe = (
            mock.Mock(st_mode=stat.S_IFREG | 0o660, st_uid=0, st_gid=456),
            mock.Mock(st_mode=stat.S_IFSOCK | 0o660, st_uid=1, st_gid=456),
            mock.Mock(st_mode=stat.S_IFSOCK | 0o666, st_uid=0, st_gid=456),
            mock.Mock(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=999),
        )
        for metadata in unsafe:
            with self.subTest(metadata=metadata), mock.patch.object(
                Path, "lstat", return_value=metadata
            ):
                with self.assertRaises(ValueError):
                    service.validate_clock_control_socket(
                        Path("/run/test.sock"), expected_gid=456
                    )
                with self.assertRaises(ValueError):
                    clock_helper.validate_socket_metadata(metadata, 456)

    def test_endpoint_sends_exact_line_and_authenticates_root_server(self) -> None:
        class Connection:
            def __init__(self, server_uid: int = 0) -> None:
                self.server_uid = server_uid
                self.response = [b"OK NTP sync requested\n", b""]
                self.sent = b""
                self.connected = ""
                self.shutdown_how: int | None = None

            def __enter__(self) -> "Connection":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def settimeout(self, _timeout: float) -> None:
                pass

            def connect(self, path: str) -> None:
                self.connected = path

            def getsockopt(self, *_args: object) -> bytes:
                return struct.pack("3i", 99, self.server_uid, 0)

            def sendall(self, value: bytes) -> None:
                self.sent += value

            def shutdown(self, how: int) -> None:
                self.shutdown_how = how

            def recv(self, _size: int) -> bytes:
                return self.response.pop(0)

        connection = Connection()
        with mock.patch.object(
            service, "validate_clock_control_socket"
        ) as validate, mock.patch.object(
            service.socket, "socket", return_value=connection
        ) as constructor:
            self.assertEqual(
                "OK NTP sync requested",
                service._exchange_clock_control("sync"),
            )
        constructor.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
        validate.assert_called_once_with(
            service.CLOCK_CONTROL_SOCKET,
            expected_gid=os.getegid(),
        )
        self.assertEqual(str(service.CLOCK_CONTROL_SOCKET), connection.connected)
        self.assertEqual(b"sync\n", connection.sent)
        self.assertEqual(socket.SHUT_WR, connection.shutdown_how)

        with mock.patch.object(
            service, "validate_clock_control_socket"
        ), mock.patch.object(
            service.socket, "socket", return_value=Connection(server_uid=1000)
        ):
            with self.assertRaises(PermissionError):
                service._exchange_clock_control("sync")

    def test_endpoint_fails_closed_on_timeout_errors_and_bad_responses(self) -> None:
        failures = (
            (TimeoutError(), "Err - clock control timed out"),
            (socket.timeout(), "Err - clock control timed out"),
            (PermissionError(), "Err - clock control peer authentication failed"),
            (FileNotFoundError(), "Err - clock control unavailable"),
            (ValueError(), "Err - clock control unavailable"),
        )
        for failure, expected in failures:
            with self.subTest(failure=failure), mock.patch.object(
                service, "_exchange_clock_control", side_effect=failure
            ):
                self.assertEqual(
                    expected,
                    service.run_clock_control("sync").text,
                )

        invalid = (
            "OK clock set to 1788147001; NTP sync requested",
            "OK NTP sync requested; reboot",
            "PARTIAL clock set to 1788147001; NTP enable request failed",
            "PARTIAL clock set to 1788147000; NTP enabled; "
            "NTP enable request failed",
            "PARTIAL clock set to 1788147000; chrony step request failed",
            "PARTIAL clock set to 1788147000; "
            "systemd-timesyncd restart failed",
            "ERR attacker supplied text",
        )
        for response in invalid:
            with self.subTest(response=response):
                self.assertEqual(
                    "Err - invalid clock control response",
                    service._clock_control_result(
                        response, "set", 1788147000
                    ).text,
                )

    def test_request_timeout_returns_error_without_mutation(self) -> None:
        connection = mock.Mock()
        with mock.patch.object(
            clock_helper, "peer_credentials", return_value=(99, 123, 456)
        ), mock.patch.object(
            clock_helper, "receive_request", side_effect=socket.timeout
        ), mock.patch.object(clock_helper, "process_request") as process:
            self.assertFalse(
                clock_helper.handle_connection(
                    connection, allowed_uid=123, allowed_gid=456
                )
            )
        process.assert_not_called()
        connection.sendall.assert_called_once_with(b"ERR request timed out\n")

        trickle = mock.Mock()
        trickle.recv.return_value = b"s"
        with mock.patch.object(
            clock_helper.time,
            "monotonic",
            side_effect=(0.0, 0.2, 1.1),
        ):
            with self.assertRaises(socket.timeout):
                clock_helper.receive_request(trickle)
        trickle.recv.assert_called_once()

    def test_child_commands_are_exact_bounded_argv_without_a_shell(self) -> None:
        success = mock.Mock(returncode=0)
        with mock.patch.object(
            clock_helper.subprocess, "run", return_value=success
        ) as run, mock.patch.object(
            clock_helper.Path, "is_file", return_value=True
        ), mock.patch.object(clock_helper.os, "access", return_value=True):
            self.assertIsNone(clock_helper.request_ntp_sync())
        self.assertEqual(2, run.call_count)
        self.assertEqual(
            ["/usr/bin/timedatectl", "set-ntp", "true"],
            run.call_args_list[0].args[0],
        )
        self.assertEqual(
            [
                "/usr/bin/systemctl",
                "start",
                clock_helper.CHRONY_STEP_SERVICE,
            ],
            run.call_args_list[1].args[0],
        )
        self.assertNotIn(
            "/usr/bin/chronyc",
            (argument for call in run.call_args_list for argument in call.args[0]),
        )
        for call in run.call_args_list:
            self.assertFalse(call.kwargs["shell"])
            self.assertEqual(
                clock_helper.CHILD_TIMEOUT_SECONDS, call.kwargs["timeout"]
            )
            self.assertEqual("/", call.kwargs["cwd"])

    def test_systemd_examples_pin_socket_owner_group_and_mode(self) -> None:
        directory = Path(__file__).resolve().parent
        socket_unit = (directory / "meshcore-clock-control.socket").read_text(
            encoding="utf-8"
        )
        service_unit = (directory / "meshcore-clock-control.service").read_text(
            encoding="utf-8"
        )
        chrony_unit = (directory / "meshcore-chrony-step.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("ListenStream=/run/meshcore-clock-control.sock", socket_unit)
        self.assertIn("SocketUser=root", socket_unit)
        self.assertIn("SocketGroup=mctomqtt", socket_unit)
        self.assertIn("SocketMode=0660", socket_unit)
        self.assertIn("User=root", service_unit)
        self.assertIn("--service-user mctomqtt", service_unit)
        self.assertIn("--service-group mctomqtt", service_unit)
        self.assertIn("CapabilityBoundingSet=CAP_SYS_TIME", service_unit)
        self.assertNotIn("CAP_DAC_OVERRIDE", service_unit)

        self.assertIn("Type=oneshot", chrony_unit)
        self.assertIn("User=_chrony", chrony_unit)
        self.assertIn("Group=_chrony", chrony_unit)
        self.assertIn("ExecStart=/usr/bin/chronyc -a makestep", chrony_unit)
        self.assertIn("TimeoutStartSec=1s", chrony_unit)
        self.assertIn("NoNewPrivileges=yes", chrony_unit)
        self.assertIn("\nCapabilityBoundingSet=\n", chrony_unit)
        self.assertIn("\nAmbientCapabilities=\n", chrony_unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", chrony_unit)
        self.assertIn("ProtectClock=yes", chrony_unit)
        self.assertIn("ProtectSystem=strict", chrony_unit)
        self.assertNotIn("CAP_", chrony_unit)
        self.assertNotIn("[Install]", chrony_unit)

    def test_activation_requires_one_named_systemd_descriptor_and_root(self) -> None:
        listener = mock.Mock()
        environment = {
            "LISTEN_PID": "4242",
            "LISTEN_FDS": "1",
            "LISTEN_FDNAMES": "clock-control",
        }
        with mock.patch.object(
            clock_helper.os, "getpid", return_value=4242
        ), mock.patch.object(
            clock_helper.socket, "socket", return_value=listener
        ) as constructor:
            self.assertIs(listener, clock_helper.activation_socket(environment))
        constructor.assert_called_once_with(
            fileno=clock_helper.SYSTEMD_LISTEN_FDS_START
        )

        invalid = (
            {**environment, "LISTEN_PID": "7"},
            {**environment, "LISTEN_FDS": "2"},
            {**environment, "LISTEN_FDNAMES": "other"},
        )
        with mock.patch.object(clock_helper.os, "getpid", return_value=4242):
            for variables in invalid:
                with self.subTest(variables=variables), self.assertRaises(
                    ValueError
                ):
                    clock_helper.activation_socket(variables)

        with mock.patch.object(clock_helper.os, "geteuid", return_value=1000), \
             mock.patch.object(clock_helper, "activation_socket") as activate, \
             mock.patch("builtins.print"):
            self.assertEqual(
                1,
                clock_helper.main(
                    ["--service-user", "mctomqtt", "--service-group", "mctomqtt"]
                ),
            )
        activate.assert_not_called()


class TokenTests(unittest.TestCase):
    def test_signed_remote_serial_token_has_required_claims(self) -> None:
        key = service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY)

        def signer(message: bytes, public: bytes, private: bytes) -> bytes:
            self.assertEqual(bytes.fromhex(PUBLIC_KEY), public)
            self.assertEqual(bytes.fromhex(PRIVATE_KEY), private)
            self.assertEqual(1, message.count(b"."))
            return SIGNATURE

        token = service.create_auth_token(
            key,
            {
                "command": (
                    "host.reply 12345678 FEDCBA9876543210 CPU 47.2 C"
                ),
                "target": "44" * 32,
                "nonce": "nonce-value",
            },
            now=1000,
            signer=signer,
        )
        header_part, payload_part, signature_part = token.split(".")
        del header_part
        padding = "=" * ((4 - len(payload_part) % 4) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_part + padding))
        self.assertEqual(PUBLIC_KEY, claims["publicKey"])
        self.assertEqual(1000, claims["iat"])
        self.assertEqual(1030, claims["exp"])
        self.assertEqual("44" * 32, claims["target"])
        self.assertEqual(SIGNATURE.hex().upper(), signature_part)


class EndpointTests(unittest.TestCase):
    def test_reboot_pending_broker_error_is_specific_and_validated(self) -> None:
        operation_id = "A1" * 16
        self.assertEqual(
            "ERR reboot pending",
            service._validate_host_action_response(
                "ERR reboot pending", "prepare", "reboot", operation_id
            ),
        )
        self.assertEqual(
            f"Err - another reboot is pending; {operation_id} not reserved",
            service.HostCliEndpoint._prepare_failure_reply(
                "reboot", operation_id, "ERR reboot pending"
            ),
        )

    def test_request_needs_matching_live_claim_before_action_is_queued(self) -> None:
        events: list[object] = []

        class PublishResult:
            rc = 0

        class Client:
            def publish(self, topic: str, token: str, qos: int) -> PublishResult:
                events.append(("publish", topic, token, qos))
                return PublishResult()

        endpoint = service.HostCliEndpoint(
            client=Client(),
            repeater_public_key="44" * 32,
            service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
            command_topic="serial/commands",
            temperature_path=Path("/unused"),
            allow_reboot=True,
            challenge_generator=lambda: "A1B2C3D4E5F60718",
            monotonic=lambda: 100.0,
        )
        endpoint._enqueue_host_action = mock.Mock(return_value=True)
        request = service.HostRequest(
            "12345678", "FEDCBA9876543210", "reboot"
        )
        claim = service.HostClaim(
            request.request_id,
            request.request_nonce,
            "A1B2C3D4E5F60718",
        )

        def make_token(
            _key: service.ServiceKey, claims: dict[str, object]
        ) -> str:
            events.append(("token", claims["command"]))
            return "signed"

        with mock.patch.object(
            service, "parse_and_verify_request", return_value=request
        ), mock.patch.object(
            service, "create_auth_token", side_effect=make_token
        ), mock.patch.object(service, "exchange_host_action") as broker:
            self.assertTrue(endpoint.handle_mqtt_message(b"request"))
            broker.assert_not_called()

            with mock.patch.object(
                service, "parse_and_verify_request", return_value=None
            ), mock.patch.object(
                service, "parse_and_verify_claim", return_value=claim
            ):
                self.assertTrue(endpoint.handle_mqtt_message(b"claim"))

        self.assertEqual(
            (
                "token",
                "host.reply 12345678 FEDCBA9876543210 "
                "@claim=A1B2C3D4E5F60718",
            ),
            events[0],
        )
        self.assertEqual("publish", events[1][0])
        self.assertEqual(2, len(events))
        expected_operation_id = service.host_action_operation_id(
            "44" * 32, request
        )
        endpoint._enqueue_host_action.assert_called_once_with(
            service.VerifiedHostAction(
                request=request,
                action="reboot",
                operation_id=expected_operation_id,
            )
        )
        broker.assert_not_called()

    def test_real_action_worker_keeps_mqtt_callback_nonblocking(self) -> None:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        class PublishResult:
            rc = 0

            def wait_for_publish(self, timeout: float) -> None:
                self.timeout = timeout

            def is_published(self) -> bool:
                return True

        class Client:
            def publish(
                self, _topic: str, _token: str, qos: int
            ) -> PublishResult:
                self.qos = qos
                return PublishResult()

        def exchange(
            verb: str,
            operation_id: str,
            action: str | None,
            *,
            deadline: float,
        ) -> str:
            del deadline
            if verb == "prepare":
                started.set()
                self.assertTrue(release.wait(timeout=2.0))
                return f"PREPARED {action} {operation_id}"
            finished.set()
            return f"SCHEDULED {action} {operation_id}"

        endpoint = service.HostCliEndpoint(
            client=Client(),
            repeater_public_key="44" * 32,
            service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
            command_topic="serial/commands",
            temperature_path=Path("/unused"),
            allow_reboot=True,
            host_action_exchange=exchange,
            challenge_generator=lambda: "A1B2C3D4E5F60718",
            monotonic=lambda: 100.0,
        )
        request = service.HostRequest(
            "12345678", "FEDCBA9876543210", "reboot"
        )
        claim = service.HostClaim(
            request.request_id,
            request.request_nonce,
            "A1B2C3D4E5F60718",
        )
        with mock.patch.object(
            service, "create_auth_token", return_value="signed"
        ):
            endpoint._handle_request(request)
            self.assertTrue(endpoint._handle_claim(claim))
            self.assertTrue(started.wait(timeout=1.0))
            self.assertFalse(release.is_set())
            release.set()
            self.assertTrue(finished.wait(timeout=2.0))
            endpoint._action_queue.join()

    def test_action_worker_orders_prepare_publish_confirmation_then_commit(
        self,
    ) -> None:
        events: list[object] = []
        operation_id = "A1" * 16

        class PublishResult:
            rc = 0

            def wait_for_publish(self, timeout: float) -> None:
                events.append(("wait", timeout))

            def is_published(self) -> bool:
                events.append("is-published")
                return True

        class Client:
            def publish(self, topic: str, token: str, qos: int) -> PublishResult:
                events.append(("publish", topic, token, qos))
                return PublishResult()

        def exchange(
            verb: str,
            sent_operation_id: str,
            action: str | None,
            *,
            deadline: float,
        ) -> str:
            self.assertGreater(deadline, service.time.monotonic())
            events.append((verb, sent_operation_id, action))
            state_name = "PREPARED" if verb == "prepare" else "SCHEDULED"
            return f"{state_name} {action} {sent_operation_id}"

        endpoint = service.HostCliEndpoint(
            client=Client(),
            repeater_public_key="44" * 32,
            service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
            command_topic="serial/commands",
            temperature_path=Path("/unused"),
            allow_reboot=True,
            host_action_exchange=exchange,
        )
        request = service.HostRequest(
            "12345678", "FEDCBA9876543210", "reboot"
        )
        with mock.patch.object(
            service, "create_auth_token", return_value="signed"
        ):
            endpoint._process_host_action(
                service.VerifiedHostAction(request, "reboot", operation_id)
            )

        self.assertEqual(
            [
                ("prepare", operation_id, "reboot"),
                ("publish", "serial/commands", "signed", 1),
                ("wait", service.HOST_ACTION_PUBLISH_TIMEOUT_SECONDS),
                "is-published",
                ("commit", operation_id, "reboot"),
            ],
            events,
        )

    def test_action_worker_fails_closed_without_mqtt_confirmation(self) -> None:
        operation_id = "A2" * 16
        request = service.HostRequest(
            "12345678", "FEDCBA9876543210", "network restart"
        )

        class Client:
            def __init__(self, publish_result: object) -> None:
                self.publish_result = publish_result

            def publish(self, _topic: str, _token: str, qos: int) -> object:
                self.qos = qos
                return self.publish_result

        class Result:
            rc = 0

            def __init__(self, outcome: str) -> None:
                self.outcome = outcome

            def wait_for_publish(self, timeout: float) -> None:
                self.timeout = timeout
                if self.outcome == "raise":
                    raise RuntimeError("disconnected")

            def is_published(self) -> bool:
                return self.outcome == "published"

        for label, publish_result in (
            ("missing-methods", mock.Mock(rc=0, spec=["rc"])),
            ("wait-error", Result("raise")),
            ("unpublished", Result("unpublished")),
        ):
            calls: list[str] = []

            def exchange(
                verb: str,
                sent_operation_id: str,
                action: str | None,
                *,
                deadline: float,
            ) -> str:
                del deadline
                calls.append(verb)
                return f"PREPARED {action} {sent_operation_id}"

            endpoint = service.HostCliEndpoint(
                client=Client(publish_result),
                repeater_public_key="44" * 32,
                service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
                command_topic="serial/commands",
                temperature_path=Path("/unused"),
                allow_network_restart=True,
                host_action_exchange=exchange,
            )
            with self.subTest(label=label), mock.patch.object(
                service, "create_auth_token", return_value="signed"
            ):
                endpoint._process_host_action(
                    service.VerifiedHostAction(
                        request, "network-restart", operation_id
                    )
                )
                self.assertEqual(["prepare"], calls)

    def test_action_worker_never_retries_an_ambiguous_commit(self) -> None:
        operation_id = "A3" * 16
        calls: list[str] = []

        class PublishResult:
            rc = 0

            def wait_for_publish(self, timeout: float) -> None:
                self.timeout = timeout

            def is_published(self) -> bool:
                return True

        client = mock.Mock()
        client.publish.return_value = PublishResult()

        def exchange(
            verb: str,
            sent_operation_id: str,
            action: str | None,
            *,
            deadline: float,
        ) -> str:
            del deadline
            calls.append(verb)
            if verb == "commit":
                raise socket.timeout("reply lost")
            return f"PREPARED {action} {sent_operation_id}"

        endpoint = service.HostCliEndpoint(
            client=client,
            repeater_public_key="44" * 32,
            service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
            command_topic="serial/commands",
            temperature_path=Path("/unused"),
            allow_reboot=True,
            host_action_exchange=exchange,
        )
        with mock.patch.object(
            service, "create_auth_token", return_value="signed"
        ):
            endpoint._process_host_action(
                service.VerifiedHostAction(
                    service.HostRequest(
                        "12345678", "FEDCBA9876543210", "reboot"
                    ),
                    "reboot",
                    operation_id,
                )
            )
        self.assertEqual(["prepare", "commit"], calls)

    def test_action_worker_does_not_commit_after_total_deadline(self) -> None:
        operation_id = "A6" * 16
        calls: list[str] = []

        class PublishResult:
            rc = 0

            def wait_for_publish(self, timeout: float) -> None:
                self.timeout = timeout

            def is_published(self) -> bool:
                return True

        client = mock.Mock()
        client.publish.return_value = PublishResult()

        def exchange(
            verb: str,
            sent_operation_id: str,
            action: str | None,
            *,
            deadline: float,
        ) -> str:
            del deadline
            calls.append(verb)
            return f"PREPARED {action} {sent_operation_id}"

        endpoint = service.HostCliEndpoint(
            client=client,
            repeater_public_key="44" * 32,
            service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
            command_topic="serial/commands",
            temperature_path=Path("/unused"),
            allow_reboot=True,
            host_action_exchange=exchange,
        )
        with mock.patch.object(
            service, "create_auth_token", return_value="signed"
        ), mock.patch.object(
            service.time, "monotonic", side_effect=(0.0, 1.0, 6.0)
        ), self.assertLogs(service.LOGGER, level="ERROR"):
            endpoint._process_host_action(
                service.VerifiedHostAction(
                    service.HostRequest(
                        "12345678", "FEDCBA9876543210", "reboot"
                    ),
                    "reboot",
                    operation_id,
                )
            )
        self.assertEqual(["prepare"], calls)

    def test_action_status_reports_exact_broker_state_without_commit(self) -> None:
        operation_id = "A4" * 16
        calls: list[str] = []
        client = mock.Mock()
        client.publish.return_value = mock.Mock(rc=0)

        def exchange(
            verb: str,
            sent_operation_id: str,
            action: str | None,
            *,
            deadline: float,
        ) -> str:
            del deadline
            calls.append(verb)
            self.assertIsNone(action)
            return f"AMBIGUOUS reboot {sent_operation_id}"

        endpoint = service.HostCliEndpoint(
            client=client,
            repeater_public_key="44" * 32,
            service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
            command_topic="serial/commands",
            temperature_path=Path("/unused"),
            allow_reboot=True,
            host_action_exchange=exchange,
        )
        request = service.HostRequest(
            "12345678",
            "FEDCBA9876543210",
            f"action status {operation_id}",
        )
        with mock.patch.object(
            service, "create_auth_token", return_value="signed"
        ) as token:
            endpoint._process_host_action(
                service.VerifiedHostAction(request, "status", operation_id)
            )
        self.assertEqual(["status"], calls)
        self.assertIn(
            f"Host action {operation_id}: reboot ambiguous",
            token.call_args.args[1]["command"],
        )

    def test_overlapping_action_is_rejected_before_broker_reservation(self) -> None:
        endpoint = service.HostCliEndpoint(
            client=mock.Mock(),
            repeater_public_key="44" * 32,
            service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
            command_topic="serial/commands",
            temperature_path=Path("/unused"),
            allow_reboot=True,
        )
        self.assertTrue(endpoint._action_slot.acquire(blocking=False))
        work = service.VerifiedHostAction(
            service.HostRequest(
                "12345678", "FEDCBA9876543210", "reboot"
            ),
            "reboot",
            "A5" * 16,
        )
        endpoint._publish_serial_reply = mock.Mock()
        self.assertFalse(endpoint._enqueue_host_action(work))
        endpoint._publish_serial_reply.assert_called_once_with(
            work.request,
            "Err - another host action is active; action was not reserved",
        )
        self.assertFalse(endpoint._action_worker_started)
        endpoint._action_slot.release()

    def test_mismatched_expired_and_replayed_claims_do_not_execute(self) -> None:
        now = [10.0]

        class Client:
            def publish(self, _topic: str, _token: str, qos: int) -> mock.Mock:
                self.qos = qos
                return mock.Mock(rc=0)

        endpoint = service.HostCliEndpoint(
            client=Client(),
            repeater_public_key="44" * 32,
            service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
            command_topic="serial/commands",
            temperature_path=Path("/unused"),
            challenge_generator=lambda: "1111222233334444",
            monotonic=lambda: now[0],
        )
        request = service.HostRequest(
            "12345678", "FEDCBA9876543210", "cpu-temp"
        )
        matching = service.HostClaim(
            request.request_id, request.request_nonce, "1111222233334444"
        )
        wrong = service.HostClaim(
            request.request_id, request.request_nonce, "9999888877776666"
        )
        with mock.patch.object(
            service, "create_auth_token", return_value="signed"
        ), mock.patch.object(
            service, "handle_request", return_value=service.HostResult("OK")
        ) as handler:
            endpoint._handle_request(request)
            endpoint._handle_claim(wrong)
            handler.assert_not_called()
            endpoint._handle_claim(matching)
            handler.assert_called_once()
            endpoint._handle_claim(matching)
            handler.assert_called_once()

            restarted = service.HostCliEndpoint(
                client=Client(),
                repeater_public_key="44" * 32,
                service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
                command_topic="serial/commands",
                temperature_path=Path("/unused"),
            )
            restarted._handle_claim(matching)
            handler.assert_called_once()

            later = service.HostCliEndpoint(
                client=Client(),
                repeater_public_key="44" * 32,
                service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
                command_topic="serial/commands",
                temperature_path=Path("/unused"),
                challenge_generator=lambda: "5555666677778888",
                challenge_timeout_seconds=8.0,
                monotonic=lambda: now[0],
            )
            later._handle_request(request)
            now[0] = 19.0
            later._handle_claim(
                service.HostClaim(
                    request.request_id,
                    request.request_nonce,
                    "5555666677778888",
                )
            )
            handler.assert_called_once()

    def test_run_program_is_not_started_by_request_record_alone(self) -> None:
        client = mock.Mock()
        client.publish.return_value = mock.Mock(rc=0)
        programs = {
            "sample": service.ProgramDefinition(
                alias="sample",
                argv=("/usr/bin/printf", "%s"),
                arguments=(
                    service.ProgramArgumentRule(
                        "value", "choice", choices=("safe",)
                    ),
                ),
            )
        }
        endpoint = service.HostCliEndpoint(
            client=client,
            repeater_public_key="44" * 32,
            service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
            command_topic="serial/commands",
            temperature_path=Path("/unused"),
            programs=programs,
            challenge_generator=lambda: "ABCDEF0123456789",
        )
        request = service.HostRequest(
            "12345678", "FEDCBA9876543210", "run sample safe"
        )
        with mock.patch.object(
            service, "create_auth_token", return_value="signed"
        ), mock.patch.object(service.subprocess, "run") as run:
            endpoint._handle_request(request)
            run.assert_not_called()
            run.return_value = mock.Mock(returncode=0, stdout="done\n")
            endpoint._handle_claim(
                service.HostClaim(
                    request.request_id,
                    request.request_nonce,
                    "ABCDEF0123456789",
                )
            )
            run.assert_called_once()

    def test_failed_challenge_publish_clears_pending_state(self) -> None:
        client = mock.Mock()
        client.publish.return_value = mock.Mock(rc=7)
        endpoint = service.HostCliEndpoint(
            client=client,
            repeater_public_key="44" * 32,
            service_key=service.ServiceKey(PUBLIC_KEY, PRIVATE_KEY),
            command_topic="serial/commands",
            temperature_path=Path("/unused"),
            challenge_generator=lambda: "ABCDEF0123456789",
        )
        request = service.HostRequest(
            "12345678", "FEDCBA9876543210", "cpu-temp"
        )
        with mock.patch.object(
            service, "create_auth_token", return_value="signed"
        ), self.assertRaisesRegex(RuntimeError, "publish failed"):
            endpoint._handle_request(request)
        self.assertIsNone(endpoint.pending)
        self.assertEqual({}, endpoint.seen)


if __name__ == "__main__":
    unittest.main()
