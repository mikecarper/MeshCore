#!/usr/bin/env python3
"""Seeded, idealized RF load against extracted production scanner methods.

Run from the repository root:
  python tools/sim/radio_profile_load.py --seed 20261124 --output receipt.json
  python tools/sim/radio_profile_load.py --seed 7 --sanitize --output receipt.json

No RF capture, fading, frequency error, or hardware acquisition probability is
modeled. Four continuous preamble symbols trigger the simulated packet guard;
same-profile overlapping airtimes fail CRC (no capture). Fixed receivers have
independent, unlimited completion buffers. Their matched-TX result includes the
scanner's admitted blackouts; their continuous result has no local TX. Neither
baseline is a claim about real receiver performance. Service gaps leave RX on,
but delay scanning and consumption of the single simulated completion FIFO.
Startup calibration/self-test runs before traffic; noise-floor refresh is then
disabled, matching the existing scan fixture's ordinary-cadence tests.

The only replaced code is hardware/clock stubs in test_radio_profile_scan.HARNESS.
Production serviceProfileScan, tuneProfile, begin/endReconfigure, programProfile,
prepareTransmitProfile and standby policy are extracted without modification.
"""

import argparse
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test"))
from test_radio_profile_scan import HARNESS  # noqa: E402
from test_radio_receive_contract import method  # noqa: E402
from test_serial_wifi_sessions import SANITIZER_FLAGS  # noqa: E402

FIXTURE = ROOT / "test/fixtures/radio_profiles/traffic_load.cpp"
PRODUCTION = ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp"
PAIRS = (
    ((7, 500, 5), (10, 125, 5)),
    ((12, 62.5, 8), (7, 250, 5)),
    ((9, 62.5, 5), (8, 500, 6)),
    ((7, 125, 5), (11, 250, 5)),
)


@dataclass
class Scenario:
    name: str
    seed: int
    profiles: tuple = PAIRS[0]
    duration_us: int = 60_000_000
    dual: bool = True
    switch_us: int = 1200
    # Frame: arrival_us, profile, payload_bytes, wire_preamble_symbols.
    frames: list = field(default_factory=list)
    services: list = field(default_factory=list)
    # Delay: start_us, end_us, kind (1 CPU, 2 UI, 3 storage).
    delays: list = field(default_factory=list)
    # Blackout request: start_us, duration_us, transmit_profile.
    transmissions: list = field(default_factory=list)
    # Explicit receiver-side legacy preambles, zero means production automatic.
    profile_preambles: tuple = (0, 0)

    def native_input(self):
        rows = [f"{self.duration_us} {int(self.dual)} {self.switch_us}"]
        rows.extend(f"{sf} {bw} {cr} {preamble}" for (sf, bw, cr), preamble
                    in zip(self.profiles, self.profile_preambles))
        rows.append(str(len(self.frames)))
        rows.extend(" ".join(map(str, row)) for row in self.frames)
        rows.append(str(len(self.services)))
        rows.extend(map(str, self.services))
        rows.append(str(len(self.delays)))
        rows.extend(" ".join(map(str, row)) for row in self.delays)
        rows.append(str(len(self.transmissions)))
        rows.extend(" ".join(map(str, row)) for row in self.transmissions)
        return "\n".join(rows) + "\n"


def randomized_scenario(seed, pair=0, duration_us=60_000_000):
    rng = random.Random(seed)
    scenario = Scenario(f"mixed-{pair}", seed, PAIRS[pair], duration_us)
    # Integer renewal arrivals, not a fitted Poisson or loss-free workload.
    for profile, (sf, bw, _) in enumerate(scenario.profiles):
        mean_gap = max(140_000, int((2 ** sf) * 1000 / bw * 650))
        at = rng.randrange(1, mean_gap)
        while at < duration_us:
            payload = rng.choice((1, 12, 32, 64, 128, 255))
            preamble = rng.choice((8, 16, 32, 64, 128, 512))
            scenario.frames.append((at, profile, payload, preamble))
            at += rng.randrange(mean_gap // 4, mean_gap * 7 // 4)
    scenario.frames.sort()
    now = 0
    while now < duration_us:
        now += rng.randrange(200, 701)
        if rng.randrange(90) == 0:
            kind = rng.randrange(1, 4)
            ranges = {1: (1000, 8000), 2: (8000, 40000), 3: (40000, 180000)}
            start = now
            now += rng.randrange(*ranges[kind])
            scenario.delays.append((start, now, kind))
        if now <= duration_us:
            scenario.services.append(now)
    now = rng.randrange(200_000, 700_000)
    while now < duration_us:
        scenario.transmissions.append((now, rng.randrange(20_000, 160_000), rng.randrange(2)))
        now += rng.randrange(300_000, 1_100_000)
    return scenario


def _replace_once(source, old, new):
    if source.count(old) != 1:
        raise AssertionError(f"Scanner fixture seam changed: {old}")
    return source.replace(old, new, 1)


def production_harness():
    prefix = HARNESS.split("int main() {", 1)[0]
    prefix = "void simulationStandby();\nvoid simulationRx(unsigned);\nvoid simulationAdvance(unsigned);\n" + prefix
    prefix = _replace_once(prefix, "int standby() { return 0; }", "int standby() { simulationStandby(); return 0; }")
    prefix = _replace_once(prefix, "elapsed_us+=switch_apply_us;", "simulationAdvance(switch_apply_us);")
    prefix = _replace_once(prefix, "_rx_ps_armed=_rx_ps_enabled;", "_rx_ps_armed=_rx_ps_enabled; simulationRx(_active_profile);")
    source = PRODUCTION.read_text(encoding="utf-8")
    signatures = (
        "uint8_t RadioLibWrapper::beginReconfigure(",
        "void RadioLibWrapper::endReconfigure(",
        "void RadioLibWrapper::serviceProfileScan(",
        "mesh::RadioParamApplyResult RadioLibWrapper::tuneProfile(",
        "mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile)",
        "mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile, bool",
    )
    methods = "\n".join(method(source, signature) for signature in signatures)
    wrapper = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text(encoding="utf-8")
    methods += "\n" + method(wrapper, "void setProfileStandbyWarm(").replace(
        "void setProfileStandbyWarm(bool enabled) override",
        "void RadioLibWrapper::setProfileStandbyWarm(bool enabled)")
    return prefix.replace("@METHODS@", methods) + FIXTURE.read_text(encoding="utf-8")


class NativeSimulation:
    def __init__(self, directory, sanitize=False):
        directory = Path(directory)
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("Native g++ or clang++ is required")
        self.executable = directory / ("traffic_load.exe" if os.name == "nt" else "traffic_load")
        cpp = directory / "traffic_load.cpp"
        self.source = production_harness()
        self.source_hashes = {str(path.relative_to(ROOT)).replace("\\", "/"):
                              hashlib.sha256(path.read_bytes()).hexdigest() for path in (
                                  PRODUCTION, ROOT / "src/RadioProfiles.h",
                                  ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h",
                                  ROOT / "test/test_radio_profile_scan.py", FIXTURE)}
        cpp.write_text(self.source, encoding="utf-8")
        (directory / "Arduino.h").write_text("#pragma once\n#include <cstdint>\n#include <cmath>\n", encoding="ascii")
        command = [compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror"]
        if sanitize:
            if not SANITIZER_FLAGS:
                raise RuntimeError("Sanitized load builds require a supported Linux host")
            command += [*SANITIZER_FLAGS, "-fno-omit-frame-pointer"]
        command += ["-I", str(directory), "-I", str(ROOT / "src"), str(cpp), "-o", str(self.executable)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        self.sanitized = sanitize

    def run(self, scenario):
        result = subprocess.run([str(self.executable)], input=scenario.native_input(),
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(f"{scenario.name} seed={scenario.seed}\n{result.stdout}\n{result.stderr}")
        receipt = json.loads(result.stdout)
        spec = asdict(scenario)
        receipt.update(schema="radio2-traffic-load-v1", scenario=spec,
                       scenario_sha256=hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
                       harness_sha256=hashlib.sha256(self.source.encode()).hexdigest(),
                       source_sha256=self.source_hashes,
                       sanitized=self.sanitized,
                       model={"acquisition_symbols": 4, "capture_effect": False,
                              "fixed_fifos": "independent-unlimited",
                              "tail_service_interval_us": 1000,
                              "zero_loss_guarantee": False},
                       exposure_bits={"off_profile": 1, "retune": 2, "packet_hold": 4,
                                      "fifo_hold": 8, "own_tx": 16, "cpu_delay": 32,
                                      "ui_delay": 64, "storage_delay": 128})
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, action="append", help="repeat for multiple seeds")
    parser.add_argument("--pair", type=int, choices=range(len(PAIRS)), action="append")
    parser.add_argument("--duration-us", type=int, default=60_000_000)
    parser.add_argument("--sanitize", action="store_true")
    parser.add_argument("--output", type=Path, help="JSON receipts; defaults to stdout")
    args = parser.parse_args()
    if args.duration_us <= 0:
        parser.error("--duration-us must be positive")
    with tempfile.TemporaryDirectory(prefix="radio2-load-") as directory:
        native = NativeSimulation(directory, args.sanitize)
        receipts = [native.run(randomized_scenario(seed, pair, args.duration_us))
                    for seed in (args.seed or [20261124]) for pair in (args.pair or range(len(PAIRS)))]
    output = json.dumps({"schema": "radio2-traffic-load-receipts-v1", "receipts": receipts}, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
