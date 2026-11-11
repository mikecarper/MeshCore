#!/usr/bin/env python3
"""Run actual warm-maintenance, ownership and driver methods on a host adapter."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/warm_rx_refresh"


def field(source, name):
    match = re.search(r"^\s*[^\n;]+\b" + re.escape(name) + r"\s*=\s*[^;]+;", source, re.MULTILINE)
    if not match:
        raise AssertionError("Missing production field: " + name)
    return match.group(0).strip()


def methods(source, signatures):
    return "\n".join(body(source, signature).replace(" override", "") for signature in signatures)


def lab_timer_fields(source):
    return "#if MESH_WARM_RX_LAB_TIMER\n" + "\n".join(field(source, name) for name in (
        "_warm_rx_lab_timer_active", "_warm_rx_lab_timer_ms")) + "\n#endif"


def lab_timer_methods(source, virtual=False):
    prefix = "virtual " if virtual else ""
    return "#if MESH_WARM_RX_LAB_TIMER\n" + methods(source, (
        prefix + "bool setWarmRxLabInterval(", prefix + "bool getWarmRxLabInterval(",
        prefix + "uint32_t warmRxLabTimerRevision(")) + "\n#endif"


# Immediate FS maintenance was replaced by an asynchronous RXPS pulse.
# Production pulse/ownership/command fault tests live in
# test_rxps_pulse_integration.py and test_rxps_pulse_fsm.py. Keep these small
# source-extraction helpers available to the independent Companion CLI tests.
