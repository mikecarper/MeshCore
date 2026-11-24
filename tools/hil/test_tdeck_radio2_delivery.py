"""Deterministic evidence/transport tests. No hardware or PlatformIO required."""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import tdeck_radio2_delivery as delivery

ROOT = Path(__file__).resolve().parents[2]


def fixture_trial(profile=1, mode=2):
    seq = 12345
    wire = delivery.make_probe(b"abcdefgh", "R" if profile else "L", seq,
                               payload_size=62, direct=True)
    return dict(sequence=seq, profile=profile, mode=mode, wire_hex=wire.hex(),
                seconds=5, fps=20, spi_mhz=8, bulk=True, sender_ack_hex="00",
                sender_before={"counters": {"tx": [10, 0]}},
                sender_after={"counters": {"tx": [11, 0]}},
                witness_before={"counters": {"tx": [0, 0]}},
                witness_after={"counters": {"tx": [0, 0]}},
                result=dict(result=seq, delivery=dict(enabled=True, sequence=seq, records=1, dropped=0, rx_errors=0),
                            display_mode=mode, spi_mhz=8, bulk=True, seconds=5, requested_fps=20,
                            elapsed_us=5000020, frames=0 if mode == 0 else 99,
                            received_packets=1, switches=100, switch_failures=0, failed_retunes=0,
                            chip_errors_before=0, chip_errors_after=0, rx_mode=1,
                            busy_timeout=False, cache_correct=True),
                records=[dict(delivery_record=seq, index=0, profile=profile, len=len(wire),
                              wire_hex=wire.hex(), at_us=500000, frame=0 if mode == 0 else 10,
                              during_redraw=mode == 2)],
                witness_frames=[dict(frame_hex=(bytes((0x84, 0x10, 0xA0, 0xA0 + profile)) + wire[2:]).hex(),
                                     host_monotonic=1.5)])


class JsonLinesTests(unittest.TestCase):
    def test_fragmented_batched_lines_and_partial_next_message(self):
        reader = delivery.JsonLines()
        reader.feed(b'boot\r\n{"delivery_re')
        self.assertIsNone(reader.take("delivery_record", 9))
        reader.feed(b'cord":9,"index":0}\r\n{"result":9}\n{"rea')
        self.assertEqual(reader.take("delivery_record", 9)["index"], 0)
        self.assertEqual(reader.take("result", 9), {"result": 9})
        reader.feed(b'dy":true}\n')
        self.assertTrue(reader.take("ready")["ready"])

    def test_malformed_error_non_object_and_overflow_fail_closed(self):
        for raw in (b'{broken}\n', b'[]\n', b'{"error":"not retained"}\n',
                    b'{"delivery":{"dropped":1,"dropped":0}}\n', b'x' * 16385):
            with self.subTest(raw=raw[:30]), self.assertRaises(delivery.EvidenceError):
                delivery.JsonLines().feed(raw)
        with self.assertRaises(UnicodeDecodeError):
            delivery.JsonLines().feed(b'{"x":"\xff"}\n')

    def test_stale_or_boolean_sequence_never_matches_integer(self):
        for value in (4, True, "5"):
            reader = delivery.JsonLines()
            reader.feed((json.dumps(dict(result=value)) + "\n").encode())
            with self.assertRaises(delivery.EvidenceError):
                reader.take("result", 5)

    def test_partial_line_survives_receive_timeout(self):
        class Device:
            def readline(self):
                return b''
        bench = delivery.TDeck.__new__(delivery.TDeck)
        bench.lines, bench.device = delivery.JsonLines(), Device()
        bench.lines.feed(b'{"result":')
        with self.assertRaises(TimeoutError):
            bench.receive("result", 99, 0.001)
        bench.lines.feed(b'99}\n')
        self.assertEqual(bench.receive("result", 99), {"result": 99})


class EvidenceTests(unittest.TestCase):
    def test_both_profiles_and_all_display_modes(self):
        for profile in (0, 1):
            for mode in (0, 1, 2):
                with self.subTest(profile=profile, mode=mode):
                    assessment = delivery.evaluate(fixture_trial(profile, mode))
                    self.assertTrue(assessment["passed"], assessment)

    def test_every_required_evidence_field_is_mandatory(self):
        base = fixture_trial()
        for location in ("result", "delivery", "record", "trial"):
            original = {"result": base["result"], "delivery": base["result"]["delivery"],
                        "record": base["records"][0], "trial": base}[location]
            keys = list(original) if location != "trial" else ["sender_ack_hex", "sender_before", "sender_after",
                                                                "witness_before", "witness_after", "witness_frames"]
            for key in keys:
                trial = copy.deepcopy(base)
                target = {"result": trial["result"], "delivery": trial["result"]["delivery"],
                          "record": trial["records"][0], "trial": trial}[location]
                del target[key]
                with self.subTest(location=location, key=key):
                    self.assertFalse(delivery.evaluate(trial)["passed"])

    def test_wrong_profile_sequence_bytes_length_duplicates_and_window(self):
        mutations = [
            lambda t: t.update(sender_ack_hex="0000"),
            lambda t: t["result"].update(result=True),
            lambda t: t["result"].update(switches=0),
            lambda t: t["result"].update(frames=1),
            lambda t: t["result"].update(chip_errors_after=1),
            lambda t: t["result"].update(busy_timeout=True),
            lambda t: t["result"].update(spi_mhz=2),
            lambda t: t["result"].update(received_packets=0),
            lambda t: t["result"]["delivery"].update(dropped=1),
            lambda t: t["result"]["delivery"].update(rx_errors=1),
            lambda t: t["result"]["delivery"].update(sequence=12344),
            lambda t: t["records"][0].update(profile=0),
            lambda t: t["records"][0].update(profile=True),
            lambda t: t["records"][0].update(delivery_record=12344),
            lambda t: t["records"][0].update(len=63),
            lambda t: t["records"][0].update(index=1),
            lambda t: t["records"][0].update(index=False),
            lambda t: t["records"][0].update(wire_hex=t["wire_hex"][:-2] + "ff"),
            lambda t: t["records"][0].update(wire_hex="garbled"),
            lambda t: t["records"][0].update(at_us=5000021),
            lambda t: t["records"][0].update(frame=0),
            lambda t: t["sender_after"]["counters"].update(tx=[12, 0]),
            lambda t: t["sender_after"]["counters"].update(tx=[0, 0]),
            lambda t: t["sender_after"]["counters"].update(tx=[10, 1]),
            lambda t: t["witness_after"]["counters"].update(tx=[1, 0]),
            lambda t: t["witness_frames"][0].update(frame_hex="8410a0a0" + t["wire_hex"][4:]),
            lambda t: t["witness_frames"][0].update(frame_hex="8410a0ff" + t["wire_hex"][4:]),
            lambda t: t["witness_frames"][0].update(frame_hex="8810a0" + t["wire_hex"]),
            lambda t: t["witness_frames"].append(copy.deepcopy(t["witness_frames"][0])),
            lambda t: t["records"].append(copy.deepcopy(t["records"][0])),
            lambda t: t.update(records=[]),
            lambda t: t.update(witness_frames=[]),
        ]
        for index, mutate in enumerate(mutations):
            trial = fixture_trial()
            mutate(trial)
            with self.subTest(mutation=index):
                self.assertFalse(delivery.evaluate(trial)["passed"])

    def test_probe_identity_is_not_inferred_from_result_label(self):
        for seq, source in ((12344, "R"), (12345, "L")):
            trial = fixture_trial()
            wire = delivery.make_probe(b"abcdefgh", source, seq, payload_size=62, direct=True)
            trial["wire_hex"] = trial["records"][0]["wire_hex"] = wire.hex()
            trial["witness_frames"][0]["frame_hex"] = (b"\x84\x10\xa0\xa1" + wire[2:]).hex()
            self.assertIn("expected_probe_identity_invalid", delivery.evaluate(trial)["failures"])

    def test_ambient_packet_is_retained_but_not_credited(self):
        trial = fixture_trial()
        ambient = copy.deepcopy(trial["records"][0])
        ambient.update(index=1, wire_hex="1234", len=2)
        trial["records"].append(ambient)
        trial["result"]["delivery"]["records"] = trial["result"]["received_packets"] = 2
        result = delivery.evaluate(trial)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["exact_tdeck_packets"], 1)


class Node:
    def __init__(self, profile=1, witness=False):
        rf = delivery.PROFILES[0 if witness else profile]
        self.witness = witness
        self.replies = {
            "get radio": f"> {rf['freq_mhz']},{rf['bw_khz']},{rf['sf']},5",
            "get radio.preamble": "> 32", "get radio.mode": "> rx" if witness else "> tx",
            "get radio2": "> 910.500,500.000,8,5,rx,32" if witness else "> off (manual)",
            "get tempradio": "> off", "get tempradio2": "> off",
            "get repeat": "> off", "get radio2.cross": "> off",
            "get radio.rxps.config": "> off,level=3,preamble=32,rx=65625,sleep=60000",
            "get radioat": "-none-", "get tempradioat": "-none-",
            "get radioat2": "> slots:", "get tempradioat2": "> slots:",
            "get tx": "> -9", "get tx.status": "> desired=-9,apply=applied",
        }
        self.commands, self.requests = [], []
        self.count = 0
        self.fail_send = False

    def cli(self, command):
        self.commands.append(command)
        if command == "get radio2.status":
            return f"> {'rx' if self.witness else 'off'}; RX=0,0 TX={self.count},0 switches=0 errors=0 max=0us preamble=32,32"
        return self.replies[command]

    def request(self, payload, expected, timeout=5):
        self.requests.append(payload)
        self.count += 1
        if self.fail_send:
            raise TimeoutError("lost ACK")
        return b"\x00"


class Bench:
    def __init__(self, trial):
        self.commands, self.replays = [], []
        self.trial = copy.deepcopy(trial)
        self.result_timeout = False
        self.ready = dict(delivery_ready=trial["sequence"], display_mode=trial["mode"],
                          spi_mhz=8, bulk=True, seconds=5, fps=20, capture_slots=32,
                          profiles=list(delivery.PROFILES))

    def send(self, command):
        self.commands.append(command)

    def receive(self, key, expected=None, timeout=5):
        if key == "delivery_ready":
            return self.ready
        if self.result_timeout:
            raise TimeoutError("lost result")
        return self.trial["result"]

    def retained(self, command, key, expected, timeout=5):
        self.replays.append(command)
        return self.trial["result"] if key == "result" else self.trial["records"][int(command.split()[-1])]


class CampaignTests(unittest.TestCase):
    def run_case(self, *, fail_send=False, lost_result=False):
        original = fixture_trial()
        trial = {key: copy.deepcopy(original[key]) for key in
                 ("sequence", "profile", "mode", "wire_hex", "seconds", "fps", "spi_mhz", "bulk")}
        bench, sender, witness = Bench(original), Node(), Node(witness=True)
        sender.fail_send, bench.result_timeout = fail_send, lost_result
        passed = delivery.run_trial(bench, sender, witness, trial,
                                    capture=lambda node, seconds: original["witness_frames"])
        self.assertEqual(len(sender.requests), 1)
        self.assertEqual(sender.requests[0], b"\x41\x00" + bytes.fromhex(trial["wire_hex"]))
        self.assertTrue(all(command.startswith("get ") for command in sender.commands + witness.commands))
        return passed, trial, bench

    def test_happy_path_uses_one_production_send_and_read_only_snapshots(self):
        passed, trial, _ = self.run_case()
        self.assertTrue(passed, trial)

    def test_missing_serial_result_replays_only_retained_result(self):
        passed, _, bench = self.run_case(lost_result=True)
        self.assertTrue(passed)
        self.assertEqual(bench.commands, ["delivery 2 8 1 5 20 12345"])
        self.assertEqual(bench.replays, ["result 12345", "delivery-record 12345 0"])

    def test_unknown_send_outcome_keeps_RX_evidence_but_never_retransmits(self):
        passed, trial, _ = self.run_case(fail_send=True)
        self.assertFalse(passed)
        self.assertIn("sender_error", trial)
        self.assertEqual(len(trial["records"]), 1)
        self.assertEqual(len(trial["witness_frames"]), 1)
        self.assertFalse(trial["assessment"]["sent_confirmed"])
        self.assertTrue(trial["assessment"]["received_confirmed"])

    def test_single_profile_or_wrong_secondary_witness_cannot_arm_sender(self):
        for key, value in (("get radio2", "> off"), ("get radio2", "> 910.500,500.000,7,5,rx,32"),
                           ("get radio.mode", "> both")):
            trial = fixture_trial()
            bench, sender, witness = Bench(trial), Node(), Node(witness=True)
            witness.replies[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(delivery.EvidenceError):
                delivery.run_trial(bench, sender, witness, trial)
            self.assertEqual(sender.requests, [])
            self.assertEqual(bench.commands, [])

    def test_bad_readiness_refuses_transmission(self):
        trial = fixture_trial()
        bench, sender = Bench(trial), Node()
        bench.ready["profiles"] = [delivery.PROFILES[0]] * 2
        with self.assertRaises(delivery.EvidenceError):
            delivery.run_trial(bench, sender, Node(witness=True), trial)
        self.assertEqual(sender.requests, [])

    def test_bad_fixture_refuses_even_receiver_start(self):
        for key, value in (("get tx", "> 2"), ("get tx.status", "> desired=-9,apply=pending"),
                           ("get radio", "> 910.6,500,8,5"), ("get radio.preamble", "> auto"),
                           ("get tempradio", "> pending"), ("get radioat", "slot1"),
                           ("get radio.rxps.config", "> on,level=3"), ("get repeat", "> on")):
            trial = fixture_trial()
            bench, sender = Bench(trial), Node()
            sender.replies[key] = value
            with self.subTest(key=key), self.assertRaises(delivery.EvidenceError):
                delivery.run_trial(bench, sender, Node(witness=True), trial)
            self.assertEqual(sender.requests, [])
            self.assertEqual(bench.commands, [])

    def test_no_consent_never_opens_ports_and_plan_works_without_site_packages(self):
        script = str(ROOT / "tools/hil/tdeck_radio2_delivery.py")
        result = subprocess.run([sys.executable, "-S", script, "--plan"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["packets"], 18)
        result = subprocess.run([sys.executable, "-S", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--allow-transmit is required", result.stderr)
        self.assertNotIn("No module named", result.stderr)

    def test_port_aliases_and_existing_report_rejected(self):
        args = ["--allow-transmit", "--tdeck-port", "COM1", "--tdeck-serial", "one",
                "--sender-port", "com1", "--sender-serial", "two", "--witness-port", "COM3",
                "--witness-serial", "three", "--output", "report.json"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            delivery.arguments(args)
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            report.touch()
            args[args.index("com1")] = "COM2"
            args[-1] = str(report)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                delivery.arguments(args)


class NativeCaptureTests(unittest.TestCase):
    def test_actual_capture_helpers_bound_bytes_profiles_and_retrieval(self):
        compiler = os.environ.get("CXX", "g++")
        if not shutil.which(compiler):
            self.skipTest("native C++ compiler unavailable")
        source = (ROOT / "tools/hil/tdeck_display_switch.cpp").read_text()
        helpers = source[source.index("struct DeliveryRecord {"):source.index("void configureRadioClock(")]
        stub = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cassert>
#include <string>
uint32_t now=20;
uint32_t micros(){return now;}
struct Driver {uint8_t profile=1;uint8_t receiveProfile(){return profile;}} driver;
struct Console {
  std::string output;
  void println(const char* s){output=s;}
  template<class... T> void printf(const char* f,T... args){char b[2048];snprintf(b,sizeof(b),f,args...);output=b;}
} Serial;
'''
        main = r'''
int main() {
  uint8_t data[256];for(unsigned i=0;i<256;++i)data[i]=i;
  captureDelivery(data,255);assert(deliveryCount==0);
  deliveryCapturing=true;deliverySequence=7;deliveryStarted=10;
  duringRedraw=true;deliveryFrame=3;
  captureDelivery(data,255);assert(deliveryCount==1);
  memset(data,0,sizeof(data));
  const auto& r=deliveryRecords[0];
  assert(r.atUs==10 && r.frame==3 && r.profile==1 && r.duringRedraw && r.length==255 && r.bytes[254]==254);
  driver.profile=0;
  for(unsigned i=1;i<32;++i)captureDelivery(data,16);
  assert(deliveryCount==32 && deliveryRecords[31].profile==0);
  captureDelivery(data,16);assert(deliveryCount==32 && deliveryDropped==1);
  deliveryCount=0;captureDelivery(data,256);assert(deliveryCount==0 && deliveryDropped==2);
  deliveryCount=32;printDeliveryRecord(7,0);
  assert(Serial.output.find("\"delivery_record\":7,\"index\":0,\"profile\":1,\"len\":255")!=std::string::npos);
  assert(Serial.output.find("fcfdfe\"")!=std::string::npos);
  const auto original=Serial.output;printDeliveryRecord(7,0);assert(Serial.output==original);
  printDeliveryRecord(8,0);assert(Serial.output.find("not retained")!=std::string::npos);
  printDeliveryRecord(7,32);assert(Serial.output.find("not retained")!=std::string::npos);
  deliveryCapturing=false;captureDelivery(data,16);assert(deliveryCount==32);
}
'''
        with tempfile.TemporaryDirectory(prefix="tdeck-delivery-") as temporary:
            directory = Path(temporary)
            path, binary = directory / "capture.cpp", directory / "capture.exe"
            path.write_text(stub + helpers + main)
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                     str(path), "-o", str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
