from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import host_cli_service as service


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

    def test_exact_fixed_commands_and_opt_in_reboot(self) -> None:
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
        self.assertFalse(disabled.reboot_requested)
        enabled = service.handle_request(
            "reboot", Path("/unused"), allow_reboot=True, reboot_delay=7
        )
        self.assertEqual("OK - host reboot scheduled in 7s", enabled.text)
        self.assertTrue(enabled.reboot_requested)
        self.assertEqual(
            service.HostResult("Err - unsupported host request"),
            service.handle_request("reboot now", Path("/unused"),
                                   allow_reboot=True),
        )

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
    def test_request_needs_matching_live_claim_before_reboot(self) -> None:
        events: list[object] = []

        class PublishResult:
            rc = 0

            def wait_for_publish(self, timeout: float) -> None:
                events.append(("wait", timeout))

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
            reboot_delay=7,
            reboot_scheduler=lambda delay: events.append(("reboot", delay)),
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

        def make_token(
            _key: service.ServiceKey, claims: dict[str, object]
        ) -> str:
            events.append(("token", claims["command"]))
            return "signed"

        reboot_result = service.HostResult(
            "OK - host reboot scheduled in 7s", reboot_requested=True
        )
        with mock.patch.object(
            service, "parse_and_verify_request", return_value=request
        ), mock.patch.object(
            service, "create_auth_token", side_effect=make_token
        ), mock.patch.object(
            service, "handle_request", return_value=reboot_result
        ) as handler:
            self.assertTrue(endpoint.handle_mqtt_message(b"request"))
            handler.assert_not_called()

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
        self.assertEqual(
            (
                "token",
                "host.reply 12345678 FEDCBA9876543210 "
                "OK - host reboot scheduled in 7s",
            ),
            events[2],
        )
        self.assertEqual("publish", events[3][0])
        self.assertEqual(("wait", 2.0), events[4])
        self.assertEqual(("reboot", 7), events[5])

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
