from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

import host_cli_service as endpoint
import meshcore_host_actions as broker


OPERATION_ID = "A1" * 16


def operation(
    operation_id: str,
    action: str,
    state_name: str,
    sequence: int,
) -> broker.OperationRecord:
    return broker.OperationRecord(
        operation_id, action, state_name, sequence
    )


class ProtocolTests(unittest.TestCase):
    def test_accepts_only_the_bounded_canonical_ascii_protocol(self) -> None:
        self.assertEqual(
            ("prepare", "reboot", OPERATION_ID),
            broker.parse_request(f"prepare reboot {OPERATION_ID}\n".encode()),
        )
        self.assertEqual(
            ("commit", "network-restart", OPERATION_ID),
            broker.parse_request(
                f"commit network-restart {OPERATION_ID}\n".encode()
            ),
        )
        self.assertEqual(
            ("status", None, OPERATION_ID),
            broker.parse_request(f"status {OPERATION_ID}\n".encode()),
        )

        invalid = (
            b"",
            f"prepare reboot {OPERATION_ID}".encode(),
            f"prepare reboot {OPERATION_ID}\nstatus {OPERATION_ID}\n".encode(),
            f"prepare reboot {OPERATION_ID.lower()}\n".encode(),
            f"prepare reboot {OPERATION_ID};reboot\n".encode(),
            f"prepare reboot {OPERATION_ID}\x00\n".encode(),
            f"prepare reboot {OPERATION_ID} \n".encode(),
            f"prepare shutdown {OPERATION_ID}\n".encode(),
            f"run reboot {OPERATION_ID}\n".encode(),
            b"\xff\n",
            b"x" * (broker.MAX_REQUEST_BYTES + 1),
        )
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                broker.parse_request(request)

    def test_injection_never_reaches_scheduler(self) -> None:
        state = broker.OperationState()
        scheduler = mock.Mock(return_value=True)
        invalid = (
            f"commit reboot {OPERATION_ID}\nreboot\n".encode(),
            f"commit reboot {OPERATION_ID};x\n".encode(),
            f"commit network-restart {OPERATION_ID.lower()}\n".encode(),
        )
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                broker.process_request(
                    request,
                    state,
                    broker.VALID_ACTIONS,
                    scheduler,
                )
        scheduler.assert_not_called()

    def test_endpoint_sends_exact_line_and_authenticates_root_listener(self) -> None:
        class Connection:
            def __init__(self, uid: int = 0) -> None:
                self.uid = uid
                self.responses = [
                    f"PREPARED reboot {OPERATION_ID}\n".encode(), b""
                ]

            def __enter__(self) -> "Connection":
                return self

            def __exit__(self, *_args: object) -> None:
                pass

            def settimeout(self, timeout: float) -> None:
                self.timeout = timeout

            def connect(self, path: str) -> None:
                self.connected = path

            def getsockopt(self, *_args: object) -> bytes:
                return struct.pack("3i", 1, self.uid, 0)

            def sendall(self, value: bytes) -> None:
                self.sent = value

            def shutdown(self, how: int) -> None:
                self.shutdown_how = how

            def recv(self, _length: int) -> bytes:
                return self.responses.pop(0)

        connection = Connection()
        with mock.patch.object(endpoint, "validate_host_actions_socket") as validate, \
             mock.patch.object(endpoint.socket, "socket", return_value=connection):
            self.assertEqual(
                f"PREPARED reboot {OPERATION_ID}",
                endpoint.exchange_host_action(
                    "prepare", OPERATION_ID, "reboot"
                ),
            )
        validate.assert_called_once_with(
            endpoint.HOST_ACTIONS_SOCKET,
            expected_gid=os.getegid(),
        )
        self.assertEqual(str(endpoint.HOST_ACTIONS_SOCKET), connection.connected)
        self.assertEqual(
            f"prepare reboot {OPERATION_ID}\n".encode(), connection.sent
        )
        self.assertEqual(socket.SHUT_WR, connection.shutdown_how)

        with mock.patch.object(endpoint, "validate_host_actions_socket"), \
             mock.patch.object(
                 endpoint.socket, "socket", return_value=Connection(uid=1000)
             ), self.assertRaises(PermissionError):
            endpoint.exchange_host_action("prepare", OPERATION_ID, "reboot")

    def test_endpoint_rejects_mismatched_or_impossible_responses(self) -> None:
        invalid = (
            f"PREPARED reboot {'B2' * 16}",
            f"PREPARED network-restart {OPERATION_ID}",
            f"PREPARED reboot {OPERATION_ID}\nreboot",
            f"UNKNOWN {OPERATION_ID}",
            f"PREPARED reboot {OPERATION_ID}",
        )
        verbs = ("prepare", "prepare", "prepare", "prepare", "commit")
        for response, verb in zip(invalid, verbs):
            with self.subTest(response=response, verb=verb), self.assertRaises(
                ValueError
            ):
                endpoint._validate_host_action_response(
                    response, verb, "reboot", OPERATION_ID
                )

    def test_socket_metadata_requires_root_service_group_and_0660(self) -> None:
        safe = os.stat_result(
            (stat.S_IFSOCK | 0o660, 1, 1, 1, 0, 456, 0, 0, 0, 0)
        )
        broker.validate_socket_metadata(safe, 456)
        with mock.patch.object(Path, "lstat", return_value=safe):
            endpoint.validate_host_actions_socket(
                Path("/fixed.sock"), expected_gid=456
            )

        variants = (
            os.stat_result((stat.S_IFREG | 0o660, 1, 1, 1, 0, 456, 0, 0, 0, 0)),
            os.stat_result((stat.S_IFSOCK | 0o660, 1, 1, 1, 1000, 456, 0, 0, 0, 0)),
            os.stat_result((stat.S_IFSOCK | 0o660, 1, 1, 1, 0, 999, 0, 0, 0, 0)),
            os.stat_result((stat.S_IFSOCK | 0o666, 1, 1, 1, 0, 456, 0, 0, 0, 0)),
        )
        for metadata in variants:
            with self.subTest(mode=metadata.st_mode, uid=metadata.st_uid), \
                 self.assertRaises(ValueError):
                broker.validate_socket_metadata(metadata, 456)
            with self.subTest(endpoint=True), mock.patch.object(
                Path, "lstat", return_value=metadata
            ), self.assertRaises(ValueError):
                endpoint.validate_host_actions_socket(
                    Path("/fixed.sock"), expected_gid=456
                )

    def test_operation_id_is_stable_and_does_not_include_action_text(self) -> None:
        first = endpoint.HostRequest(
            "12345678", "FEDCBA9876543210", "reboot"
        )
        same_identity = endpoint.HostRequest(
            "12345678", "FEDCBA9876543210", "network restart"
        )
        self.assertEqual(
            "40CF367EF6D9C3305FB71021DA3E7D92",
            endpoint.host_action_operation_id("44" * 32, first),
        )
        self.assertEqual(
            endpoint.host_action_operation_id("44" * 32, first),
            endpoint.host_action_operation_id("44" * 32, same_identity),
        )


class OperationStateTests(unittest.TestCase):
    def test_prepare_commit_and_duplicates_schedule_exactly_once(self) -> None:
        snapshots: list[dict[str, broker.OperationRecord]] = []
        state = broker.OperationState(
            persist=lambda records, _next: snapshots.append(dict(records))
        )
        scheduler = mock.Mock(return_value=True)
        self.assertEqual(
            "prepared",
            state.prepare("reboot", OPERATION_ID, broker.VALID_ACTIONS),
        )
        self.assertEqual(
            "prepared",
            state.prepare("reboot", OPERATION_ID, broker.VALID_ACTIONS),
        )
        self.assertEqual(
            "scheduled",
            state.commit(
                "reboot", OPERATION_ID, broker.VALID_ACTIONS, scheduler
            ),
        )
        self.assertEqual(
            "scheduled",
            state.commit(
                "reboot", OPERATION_ID, broker.VALID_ACTIONS, scheduler
            ),
        )
        scheduler.assert_called_once_with("reboot")
        self.assertEqual(
            ["prepared", "in-progress", "scheduled"],
            [snapshot[OPERATION_ID].state for snapshot in snapshots],
        )

    def test_same_id_different_action_conflicts_and_policy_is_rechecked(self) -> None:
        state = broker.OperationState()
        state.prepare("reboot", OPERATION_ID, broker.VALID_ACTIONS)
        with self.assertRaisesRegex(ValueError, "conflict"):
            state.prepare("network-restart", OPERATION_ID, broker.VALID_ACTIONS)
        with self.assertRaises(PermissionError):
            state.prepare("reboot", OPERATION_ID, frozenset())

    def test_new_reboot_supersedes_only_uncommitted_reboot_reservations(
        self,
    ) -> None:
        first = "A1" * 16
        second = "B2" * 16
        network = "C3" * 16
        snapshots: list[dict[str, broker.OperationRecord]] = []
        state = broker.OperationState(
            persist=lambda records, _next: snapshots.append(dict(records))
        )
        state.prepare("network-restart", network, broker.VALID_ACTIONS)
        state.prepare("reboot", first, broker.VALID_ACTIONS)

        self.assertEqual(
            "prepared", state.prepare("reboot", second, broker.VALID_ACTIONS)
        )
        self.assertNotIn(first, state.records)
        self.assertIn(second, state.records)
        self.assertIn(network, state.records)
        self.assertNotIn(first, snapshots[-1])

        scheduler = mock.Mock(return_value=True)
        with self.assertRaisesRegex(LookupError, "not prepared"):
            state.commit("reboot", first, broker.VALID_ACTIONS, scheduler)
        scheduler.assert_not_called()

    def test_distinct_reboot_is_rejected_after_commit_starts(self) -> None:
        second = "B2" * 16
        for state_name in ("in-progress", "scheduled", "ambiguous"):
            with self.subTest(state=state_name):
                state = broker.OperationState(
                    {
                        OPERATION_ID: operation(
                            OPERATION_ID, "reboot", state_name, 1
                        )
                    },
                    next_sequence=2,
                )
                with self.assertRaisesRegex(
                    broker.RebootPendingError, "already pending"
                ):
                    state.prepare("reboot", second, broker.VALID_ACTIONS)
                effective_state = (
                    "ambiguous" if state_name == "in-progress" else state_name
                )
                self.assertEqual(
                    effective_state, state.status(OPERATION_ID).state
                )
                self.assertIsNone(state.status(second))

                # Exact duplicate requests still report their durable state.
                self.assertEqual(
                    effective_state,
                    state.prepare(
                        "reboot", OPERATION_ID, broker.VALID_ACTIONS
                    ),
                )

    def test_reboot_pending_has_fixed_protocol_error(self) -> None:
        state = broker.OperationState(
            {
                OPERATION_ID: operation(
                    OPERATION_ID, "reboot", "scheduled", 1
                )
            },
            next_sequence=2,
        )
        second = "B2" * 16
        self.assertEqual(
            "ERR reboot pending",
            broker.process_request(
                f"prepare reboot {second}\n".encode(),
                state,
                broker.VALID_ACTIONS,
            ),
        )

    def test_recovered_in_progress_is_ambiguous_and_never_retried(self) -> None:
        saved: list[dict[str, broker.OperationRecord]] = []
        records = {
            OPERATION_ID: operation(
                OPERATION_ID, "network-restart", "in-progress", 1
            )
        }
        state = broker.OperationState(
            records,
            next_sequence=2,
            persist=lambda candidate, _next: saved.append(dict(candidate)),
        )
        self.assertEqual("ambiguous", state.status(OPERATION_ID).state)
        self.assertEqual("ambiguous", saved[-1][OPERATION_ID].state)
        scheduler = mock.Mock(return_value=True)
        self.assertEqual(
            "ambiguous",
            state.commit(
                "network-restart",
                OPERATION_ID,
                broker.VALID_ACTIONS,
                scheduler,
            ),
        )
        scheduler.assert_not_called()

    def test_scheduler_or_final_persist_failure_is_ambiguous_without_retry(
        self,
    ) -> None:
        for failure in ("scheduler", "persist"):
            calls = [0]

            def persist(
                _records: dict[str, broker.OperationRecord], _next: int
            ) -> None:
                calls[0] += 1
                if failure == "persist" and calls[0] == 3:
                    raise broker.StateError("disk failure")

            state = broker.OperationState(persist=persist)
            state.prepare("reboot", OPERATION_ID, broker.VALID_ACTIONS)
            scheduler = mock.Mock(
                side_effect=(RuntimeError("failure") if failure == "scheduler" else None),
                return_value=True,
            )
            with self.subTest(failure=failure):
                self.assertEqual(
                    "ambiguous",
                    state.commit(
                        "reboot",
                        OPERATION_ID,
                        broker.VALID_ACTIONS,
                        scheduler,
                    ),
                )
                self.assertEqual(
                    "ambiguous", state.status(OPERATION_ID).state
                )
                state.commit(
                    "reboot", OPERATION_ID, broker.VALID_ACTIONS, scheduler
                )
                scheduler.assert_called_once()

    def test_commit_never_schedules_before_in_progress_is_durable(self) -> None:
        state = broker.OperationState(
            {
                OPERATION_ID: operation(
                    OPERATION_ID, "reboot", "prepared", 1
                )
            },
            next_sequence=2,
            persist=mock.Mock(side_effect=broker.StateError("disk failure")),
        )
        scheduler = mock.Mock(return_value=True)
        with self.assertRaises(broker.StateError):
            state.commit(
                "reboot", OPERATION_ID, broker.VALID_ACTIONS, scheduler
            )
        scheduler.assert_not_called()
        self.assertEqual("prepared", state.status(OPERATION_ID).state)

    def test_bounded_store_evicts_only_prepared_records(self) -> None:
        records = {
            f"{index:032X}": operation(
                f"{index:032X}", "network-restart", "scheduled", index + 1
            )
            for index in range(broker.MAX_OPERATIONS)
        }
        state = broker.OperationState(records, broker.MAX_OPERATIONS + 1)
        with self.assertRaises(broker.StateFullError):
            state.prepare(
                "network-restart", "F" * 32, broker.VALID_ACTIONS
            )

        oldest = next(iter(records))
        records[oldest] = operation(
            oldest, "network-restart", "prepared", 1
        )
        state = broker.OperationState(records, broker.MAX_OPERATIONS + 1)
        self.assertEqual(
            "prepared",
            state.prepare(
                "network-restart", "F" * 32, broker.VALID_ACTIONS
            ),
        )
        self.assertNotIn(oldest, state.records)

    def test_state_schema_rejects_boolean_and_unbounded_values(self) -> None:
        valid = {
            "version": 1,
            "next_sequence": 2,
            "operations": [
                {
                    "id": OPERATION_ID,
                    "action": "reboot",
                    "state": "prepared",
                    "sequence": 1,
                }
            ],
        }
        broker.parse_state_document(valid)
        for path, value in (
            (("version",), True),
            (("next_sequence",), True),
            (("operations", 0, "sequence"), True),
            (("operations", 0, "state"), "complete"),
        ):
            document = {
                "version": valid["version"],
                "next_sequence": valid["next_sequence"],
                "operations": [dict(valid["operations"][0])],
            }
            target: object = document
            for component in path[:-1]:
                target = target[component]  # type: ignore[index]
            target[path[-1]] = value  # type: ignore[index]
            with self.subTest(path=path), self.assertRaises(broker.StateError):
                broker.parse_state_document(document)

        valid["next_sequence"] = broker.MAX_SEQUENCE + 1
        with self.assertRaises(broker.StateError):
            broker.parse_state_document(valid)

    def test_status_is_read_only_and_reports_exact_persisted_state(self) -> None:
        state = broker.OperationState()
        self.assertEqual(
            f"UNKNOWN {OPERATION_ID}",
            broker.process_request(
                f"status {OPERATION_ID}\n".encode(),
                state,
                frozenset(),
            ),
        )
        self.assertEqual(
            f"PREPARED reboot {OPERATION_ID}",
            broker.process_request(
                f"prepare reboot {OPERATION_ID}\n".encode(),
                state,
                broker.VALID_ACTIONS,
            ),
        )
        scheduler = mock.Mock()
        self.assertEqual(
            f"PREPARED reboot {OPERATION_ID}",
            broker.process_request(
                f"status {OPERATION_ID}\n".encode(),
                state,
                frozenset(),
                scheduler,
            ),
        )
        scheduler.assert_not_called()


class BrokerExecutionTests(unittest.TestCase):
    def test_peer_uid_and_primary_gid_are_both_required_before_read(self) -> None:
        connection = mock.Mock()
        connection.sendall = mock.Mock()
        for credentials in ((1, 999, 456), (1, 123, 999)):
            with self.subTest(credentials=credentials), mock.patch.object(
                broker, "peer_credentials", return_value=credentials
            ), mock.patch.object(broker, "receive_request") as receive:
                self.assertFalse(
                    broker.handle_connection(
                        connection,
                        allowed_uid=123,
                        allowed_gid=456,
                        allowed_actions=broker.VALID_ACTIONS,
                        state=broker.OperationState(),
                    )
                )
                receive.assert_not_called()

    def test_authorized_timeout_returns_fixed_error_without_scheduling(self) -> None:
        connection = mock.Mock()
        scheduler = mock.Mock()
        with mock.patch.object(
            broker, "peer_credentials", return_value=(1, 123, 456)
        ), mock.patch.object(
            broker, "receive_request", side_effect=socket.timeout
        ), mock.patch.object(broker, "process_request") as process:
            self.assertFalse(
                broker.handle_connection(
                    connection,
                    allowed_uid=123,
                    allowed_gid=456,
                    allowed_actions=broker.VALID_ACTIONS,
                    state=broker.OperationState(),
                )
            )
        process.assert_not_called()
        connection.sendall.assert_called_once_with(b"ERR request timed out\n")
        scheduler.assert_not_called()

    def test_scheduler_uses_only_fixed_systemctl_argv_and_bounds(self) -> None:
        completed = mock.Mock(returncode=0)
        with mock.patch.object(
            broker.subprocess, "run", return_value=completed
        ) as run:
            self.assertTrue(broker.schedule_action("network-restart"))
        self.assertEqual(
            [
                "/usr/bin/systemctl",
                "--no-block",
                "start",
                "meshcore-networkmanager-restart.service",
            ],
            run.call_args.args[0],
        )
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual("/", run.call_args.kwargs["cwd"])
        self.assertEqual(
            broker.SCHEDULE_TIMEOUT_SECONDS,
            run.call_args.kwargs["timeout"],
        )
        self.assertEqual(broker.MINIMAL_ENV, run.call_args.kwargs["env"])

        with mock.patch.object(
            broker.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("systemctl", 1.5),
        ):
            self.assertFalse(broker.schedule_action("reboot"))
        self.assertFalse(broker.schedule_action("shutdown"))

    def test_default_root_policy_is_empty_and_parser_is_exact(self) -> None:
        self.assertEqual(frozenset(), broker.parse_allowed_actions(""))
        self.assertEqual(
            broker.VALID_ACTIONS,
            broker.parse_allowed_actions("network-restart,reboot"),
        )
        for value in (
            "reboot,network-restart,reboot",
            "reboot,",
            " reboot",
            "shutdown",
            "*",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                broker.parse_allowed_actions(value)

    def test_activation_requires_one_named_descriptor_and_root(self) -> None:
        listener = mock.Mock()
        environment = {
            "LISTEN_PID": "4242",
            "LISTEN_FDS": "1",
            "LISTEN_FDNAMES": "host-actions",
        }
        with mock.patch.object(broker.os, "getpid", return_value=4242), \
             mock.patch.object(broker.socket, "socket", return_value=listener) as create:
            self.assertIs(listener, broker.activation_socket(environment))
        create.assert_called_once_with(fileno=broker.SYSTEMD_LISTEN_FDS_START)

        for invalid in (
            {**environment, "LISTEN_PID": "1"},
            {**environment, "LISTEN_FDS": "2"},
            {**environment, "LISTEN_FDNAMES": "clock-control"},
        ):
            with self.subTest(invalid=invalid), mock.patch.object(
                broker.os, "getpid", return_value=4242
            ), self.assertRaises(ValueError):
                broker.activation_socket(invalid)

        with mock.patch.object(broker.os, "geteuid", return_value=1000), \
             mock.patch.object(broker, "activation_socket") as activate, \
             mock.patch("builtins.print"):
            self.assertEqual(
                1,
                broker.main(
                    ["--service-user", "mctomqtt", "--service-group", "mctomqtt"]
                ),
            )
        activate.assert_not_called()

    def test_state_paths_require_root_only_directory_and_regular_file(self) -> None:
        directory_safe = os.stat_result(
            (stat.S_IFDIR | 0o700, 1, 1, 1, 0, 0, 0, 0, 0, 0)
        )
        file_safe = os.stat_result(
            (stat.S_IFREG | 0o600, 1, 1, 1, 0, 0, 10, 0, 0, 0)
        )
        with mock.patch.object(Path, "lstat", return_value=directory_safe):
            broker.validate_state_directory(Path("/fixed"))
        broker._validate_state_file_metadata(file_safe)

        bad_directories = (
            os.stat_result((stat.S_IFDIR | 0o755, 1, 1, 1, 0, 0, 0, 0, 0, 0)),
            os.stat_result((stat.S_IFDIR | 0o700, 1, 1, 1, 1000, 0, 0, 0, 0, 0)),
            os.stat_result((stat.S_IFREG | 0o700, 1, 1, 1, 0, 0, 0, 0, 0, 0)),
        )
        for metadata in bad_directories:
            with self.subTest(directory=metadata), mock.patch.object(
                Path, "lstat", return_value=metadata
            ), self.assertRaises(broker.StateError):
                broker.validate_state_directory(Path("/fixed"))

        bad_files = (
            os.stat_result((stat.S_IFREG | 0o644, 1, 1, 1, 0, 0, 10, 0, 0, 0)),
            os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, 1000, 0, 10, 0, 0, 0)),
            os.stat_result((stat.S_IFREG | 0o600, 1, 1, 2, 0, 0, 10, 0, 0, 0)),
            os.stat_result(
                (
                    stat.S_IFREG | 0o600,
                    1, 1, 1, 0, 0,
                    broker.MAX_STATE_BYTES + 1,
                    0, 0, 0,
                )
            ),
        )
        for metadata in bad_files:
            with self.subTest(file=metadata), self.assertRaises(broker.StateError):
                broker._validate_state_file_metadata(metadata)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "target"
            target.write_text("{}", encoding="ascii")
            link = directory / "state.json"
            link.symlink_to(target)
            state_file = broker.StateFile(link, directory)
            with mock.patch.object(broker, "validate_state_directory"), \
                 self.assertRaises(broker.StateError):
                state_file.load()

        self.assertRaises(
            broker.StateError,
            broker._strict_json_object,
            [("version", 1), ("version", 1)],
        )


class SystemdUnitTests(unittest.TestCase):
    def test_units_pin_socket_policy_delay_argv_and_empty_capabilities(self) -> None:
        directory = Path(__file__).parent
        socket_unit = (directory / "meshcore-host-actions.socket").read_text()
        broker_unit = (directory / "meshcore-host-actions.service").read_text()
        network_unit = (
            directory / "meshcore-networkmanager-restart.service"
        ).read_text()
        reboot_timer = (directory / "meshcore-host-reboot.timer").read_text()
        reboot_unit = (directory / "meshcore-host-reboot.service").read_text()

        self.assertIn(
            "ListenStream=/run/meshcore-host-actions.sock", socket_unit
        )
        self.assertIn("SocketUser=root", socket_unit)
        self.assertIn("SocketGroup=mctomqtt", socket_unit)
        self.assertIn("SocketMode=0660", socket_unit)
        self.assertIn("Environment=MESHCORE_HOST_ACTIONS=\n", broker_unit)
        self.assertIn("User=root", broker_unit)
        self.assertIn("CapabilityBoundingSet=\n", broker_unit)
        self.assertIn("AmbientCapabilities=\n", broker_unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", broker_unit)
        self.assertIn("RuntimeDirectoryPreserve=yes", broker_unit)
        self.assertIn(
            "ExecStart=/usr/bin/systemctl --no-block restart NetworkManager.service",
            network_unit,
        )
        self.assertIn("OnActiveSec=10s", reboot_timer)
        self.assertIn("Persistent=no", reboot_timer)
        self.assertIn("RemainAfterElapse=no", reboot_timer)
        self.assertIn(
            "ExecStart=/usr/bin/systemctl reboot", reboot_unit
        )
        for action_unit in (network_unit, reboot_unit):
            self.assertIn("CapabilityBoundingSet=\n", action_unit)
            self.assertIn("AmbientCapabilities=\n", action_unit)
            self.assertIn("RestrictAddressFamilies=AF_UNIX", action_unit)
        for static_unit in (network_unit, reboot_timer, reboot_unit):
            self.assertNotIn("[Install]", static_unit)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd unavailable")
    def test_systemd_examples_have_no_parser_errors(self) -> None:
        directory = Path(__file__).parent
        units = [
            directory / "meshcore-host-actions.socket",
            directory / "meshcore-host-actions.service",
            directory / "meshcore-networkmanager-restart.service",
            directory / "meshcore-host-reboot.timer",
            directory / "meshcore-host-reboot.service",
        ]
        completed = subprocess.run(
            ["systemd-analyze", "verify", *(str(path) for path in units)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
        output = completed.stdout
        for marker in ("Unknown key", "Failed to parse", "Invalid argument"):
            self.assertNotIn(marker, output)


if __name__ == "__main__":
    unittest.main()
