#!/usr/bin/env python3
"""Static integration contract for TempRadio's single-copy reply barrier."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/simple_repeater/MyMesh.cpp"


def function_body(text: str, signature: str, next_signature: str) -> str:
    start = text.index(signature)
    end = text.index(next_signature, start)
    return text[start:end]


class TempRadioReplyDeliveryContractTest(unittest.TestCase):
    def test_exact_packet_tracking_suppresses_all_untracked_copies(self):
        source = SOURCE.read_text(encoding="utf-8")
        reply = function_body(
            source,
            "bool MyMesh::sendRemoteCliReply(",
            "void MyMesh::onUserGpioTimerCompleted(",
        )
        delivery = function_body(
            source,
            "bool MyMesh::sendClientReplyWithFallbackScope(",
            "bool MyMesh::floodChannelDataHopApplies(",
        )

        self.assertIn(
            "const bool allow_redundant_copies = queued_packet == NULL;", reply
        )
        self.assertIn("fallback_scope,\n      allow_redundant_copies", reply)
        self.assertIn(
            "if (allow_redundant_copies\n"
            "      && mesh::Packet::isValidPathLen(client->alt_path_len)",
            delivery,
        )
        self.assertIn(
            "if (!allow_redundant_copies) _prefs.direct_retry_enabled = 0;",
            delivery,
        )
        self.assertIn(
            "if (!allow_redundant_copies) _prefs.flood_retry_attempts = 0;",
            delivery,
        )

    def test_only_successful_parameterized_temp_radio_requests_track_reply(self):
        source = SOURCE.read_text(encoding="utf-8")
        receive = function_body(
            source,
            "void MyMesh::onPeerDataRecv(",
            "bool MyMesh::sendRemoteCliReply(",
        )
        command = function_body(
            source,
            "void __attribute__((noinline)) MyMesh::processDeferredCliCommand()",
            "bool MyMesh::onPeerPathRecv(",
        )

        self.assertIn(
            'strncmp(deferred_cli_command.command, "tempradio ", 10)', command
        )
        self.assertIn('strncmp(reply, "OK - temp params for ", 21)', command)
        self.assertIn("arms_temp_radio ? &queued_reply : NULL", command)

        # A packet-level retry can arrive after the success text was cached but
        # before the authoritative reply drains. It must not create a second,
        # untracked success packet either.
        self.assertIn("const bool cached_temp_radio_success", receive)
        suppress_start = receive.index("if (cached_temp_radio_success)")
        replay_start = receive.index("} else {", suppress_start)
        replay_end = receive.index(
            "} else if (deferred_cli_command.matches", replay_start
        )
        self.assertNotIn(
            "sendRemoteCliReply(", receive[suppress_start:replay_start]
        )
        self.assertIn("sendRemoteCliReply(", receive[replay_start:replay_end])


if __name__ == "__main__":
    unittest.main()
