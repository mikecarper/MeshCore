#!/usr/bin/env python3
"""Integration contract for tracked TempRadio replies on both profiles."""

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
        self.assertLess(reply.index('temp_radio_reply_barrier.prepare(packet)'),
                        reply.index('const bool queued = sendClientReplyWithFallbackScope'))
        self.assertIn('if (!queued && !allow_redundant_copies) temp_radio_reply_barrier.clear();', reply)
        self.assertIn('temp_radio_reply_barrier.trackCopy(original, packet);', source)
        self.assertIn('mesh::Mesh::onRadioProfileCopyQueued(packet, original, priority);', source)
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

    def test_accepted_mutations_use_typed_identity_not_raw_command_spelling(self):
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

        self.assertIn('primary_radio_mutation_generation != primary_mutation_before', command)
        self.assertIn('replyMutationGeneration() != secondary_mutation_before', command)
        self.assertNotIn('strncmp(deferred_cli_command.command, "tempradio ', command)
        self.assertIn("arms_temp_radio ? &queued_reply : NULL", command)

        # A packet-level retry can arrive after the success text was cached but
        # before the authoritative reply drains. It must not create a second,
        # untracked success packet either.
        self.assertIn("bool cached_authoritative_reply", receive)
        suppress_start = receive.index("if (cached_authoritative_reply)")
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
