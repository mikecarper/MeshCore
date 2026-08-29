#!/usr/bin/env python3

from pathlib import Path
import re


root = Path(__file__).resolve().parents[1]
source = (root / "examples/companion_radio/MyMesh.cpp").read_text()
features = (root / "examples/companion_radio/CompanionFeatures.h").read_text()

local_start = source.index("bool MyMesh::handleLocalControlCommand(")
local_end = source.index("\n#if COMPANION_FEATURE_TEMP_RADIO\nvoid MyMesh::serviceTempRadio()", local_start)
local = source[local_start:local_end]

assert local.count('strcmp(command, "board") == 0') == 1
assert 'board.getManufacturerName()' in local
assert re.search(
    r"#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS\s+"
    r'if \(strcmp\(command, "memory"\) == 0\).*?'
    r"formatCompanionMemoryDiagnostics.*?#endif",
    local,
    re.DOTALL,
)
assert "offline_queue_len" in local
assert "getOfflineQueueCapacity()" in local

terminal_start = source.index("void MyMesh::handleTerminalCommand(")
terminal_end = source.index("\nvoid MyMesh::enterCLIRescue()", terminal_start)
terminal = source[terminal_start:terminal_end]
assert 'terminalOutput().print("  board\\r\\n")' in terminal
assert re.search(
    r"#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS\s+"
    r'terminalOutput\(\)\.print\("  memory\\r\\n"\);\s+#endif',
    terminal,
)

# This compile-time contract prevents either the implementation or help entry
# from leaking onto a non-ESP32 build merely because a recipe set the flag.
assert re.search(
    r"#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS && !defined\(ESP32_PLATFORM\)\s+"
    r'#error "COMPANION_FEATURE_MEMORY_DIAGNOSTICS requires ESP32"',
    features,
)

print("test_companion_terminal_profile: PASS")
