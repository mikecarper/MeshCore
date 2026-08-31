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

# Mutable local settings must be dispatched before the terminal's generic
# `set` fallback.  `set pin` used to exist only in handleCommand(), so the
# text terminal intercepted it as an unknown setting while framed/rescue CLI
# happened to work.
assert local.count('strncmp(command, "set pin", 7) == 0') == 1
assert "mesh::cli::parseIntegerStrict(value, parsed)" in local
assert "const uint32_t previous = _prefs.ble_pin;" in local
assert "_prefs.ble_pin = previous;" in local

terminal_start = source.index("void MyMesh::handleTerminalCommand(")
terminal_end = source.index("\nvoid MyMesh::enterCLIRescue()", terminal_start)
terminal = source[terminal_start:terminal_end]
assert terminal.index("handleLocalControlCommand(") < terminal.index(
    'strncmp(command, "set ", 4) == 0'
)
assert 'terminalOutput().print("  board\\r\\n")' in terminal
assert 'terminalOutput().print("  set pin <0-999999>\\r\\n")' in terminal
assert re.search(
    r"#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS\s+"
    r'terminalOutput\(\)\.print\("  memory\\r\\n"\);\s+#endif',
    terminal,
)

# Terminal-specific handlers keep precedence, then both the generic `set`
# fallthrough and the final unknown-command fallthrough delegate to the shared
# framed/rescue command surface.  This keeps get/set radio, get name, and
# variant commands available without routing terminal-only commands twice.
assert terminal.count("handleCommand(command, 0, local_reply)") == 2
assert terminal.index('strncmp(config, "tx ", 3)') < terminal.index(
    "handleCommand(command, 0, local_reply)"
)
assert terminal.rindex('strcmp(command, "ver") == 0') < terminal.rindex(
    "handleCommand(command, 0, local_reply)"
)

command_start = source.index("bool MyMesh::handleCommand(")
command_end = source.index("\nvoid MyMesh::checkCLIRescueCmd()", command_start)
command_handler = source[command_start:command_end]
assert 'strncmp(command, "set pin' not in command_handler

# This compile-time contract prevents either the implementation or help entry
# from leaking onto a non-ESP32 build merely because a recipe set the flag.
assert re.search(
    r"#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS && !defined\(ESP32_PLATFORM\)\s+"
    r'#error "COMPANION_FEATURE_MEMORY_DIAGNOSTICS requires ESP32"',
    features,
)

print("test_companion_terminal_profile: PASS")
