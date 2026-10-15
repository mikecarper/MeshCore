"""Primary/Ethernet coexistence harness guards; never opens hardware."""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/hil"))
import ethernet_primary_probe as probe


class FakeNode:
    def __init__(self, radio="> 909.5,500.000,5,5", ethernet="> 909.500,500,5,5,32; sync=0x16; saved"):
        self.identity = {"port": "fake-only"}
        self.commands = []
        self.radio = radio
        self.ethernet = ethernet

    def command(self, text):
        self.commands.append(text)
        return {"get radio": self.radio, "get ethernet.radio": self.ethernet,
                "get ethernet": "> ethernet=on; active (RX time-shared); TCP 5003 connected",
                "get tx.channel 0": '> tx.channel 0 "Public" radio; active=radio',
                "set tx.channel 0 radio": 'OK - tx.channel 0 "Public" radio; active=radio',
                "show channels on": "Terminal display: channels on"}.get(text, "> public")


class EthernetPrimaryProbeTests(unittest.TestCase):
    def test_lab_tuple_parser_is_strict_and_accepts_known_cli_formats(self):
        for text in ("> 909.5,500,5,5", "get radio\r\n> 909.500,500.000,5,5\r\n> ",
                     "get radio\r\n  -> > 909.5,500,5,5\r\n> ",
                     "> 909.500,500,5,5,32; sync=0x16; saved"):
            probe.assert_lab_radio(text)
        for text in ("> 910.525,500,5,5", "> 909.5,125,5,5", "> 909.5,500,7,5",
                     "> 909.5,500,5,7", "Error: unavailable", "set radio 909.5,500,5,5"):
            with self.assertRaises(RuntimeError):
                probe.assert_lab_radio(text)

    def test_own_command_echo_is_never_an_incoming_message(self):
        marker = "ETH-PRIMARY-12345678-a"
        for text in ("channel 0 " + marker, "Sent to Public.\r\n> ",
                     "debug incoming " + marker, "CHANNEL MSG -> Public (FLOOD)\r\n wrong-message"):
            self.assertFalse(probe.received_channel_marker(text, marker))
        self.assertTrue(probe.received_channel_marker(
            "\r\nCHANNEL MSG -> Public (FLOOD)\r\n  EthernetA: " + marker + "\r\n> ", marker))
        self.assertFalse(probe.received_channel_marker(
            "CHANNEL MSG -> Public (FLOOD)\r\n  Node: " + marker + "-different", marker))

    def test_bad_primary_or_ethernet_profile_fails_before_any_tx(self):
        for name in ("radio", "ethernet"):
            a, b = FakeNode(), FakeNode()
            setattr(b, name, "> 910.525,500,5,5")
            factory = Mock()
            with self.assertRaises(RuntimeError):
                probe.run_primary(a, b, "tcp://a:5003", "tcp://b:5003", factory=factory)
            factory.assert_not_called()
            self.assertFalse(any(command.startswith("set ") or command.startswith("channel ")
                                 for node in (a, b) for command in node.commands))

    def test_channel_zero_primary_policy_is_set_and_confirmed_before_kiss(self):
        a, b = FakeNode(), FakeNode()
        factory = Mock(side_effect=RuntimeError("stop before opening test socket"))
        with self.assertRaisesRegex(RuntimeError, "stop before"):
            probe.run_primary(a, b, "tcp://a:5003", "tcp://b:5003", factory=factory)
        for node in (a, b):
            self.assertIn("set tx.channel 0 radio", node.commands)
            self.assertIn("show channels on", node.commands)
            self.assertLess(node.commands.index("set tx.channel 0 radio"),
                            len(node.commands) - 1 - node.commands[::-1].index("get tx.channel 0"))


if __name__ == "__main__":
    unittest.main()
