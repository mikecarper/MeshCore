#!/usr/bin/env python3
"""Pin local UF2 reset support across every nRF52 text CLI family."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

main_board = (ROOT / "src/MeshCore.h").read_text()
nrf_board_h = (ROOT / "src/helpers/NRF52Board.h").read_text()
nrf_board_cpp = (ROOT / "src/helpers/NRF52Board.cpp").read_text()
common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
chat = (ROOT / "examples/simple_secure_chat/main.cpp").read_text()

# One board capability owns the retained-register transaction. This reaches
# every NRF52Board subclass, including variants which override handleCommand().
assert "virtual bool rebootToUf2Bootloader() { return false; }" in main_board
assert "bool rebootToUf2Bootloader() override;" in nrf_board_h
assert "bool NRF52Board::rebootToUf2Bootloader()" in nrf_board_cpp
assert "sd_softdevice_is_enabled(&sd_enabled) != NRF_SUCCESS" in nrf_board_cpp
clear = nrf_board_cpp.index("sd_power_gpregret_clr(0, 0xFF)")
set_magic = nrf_board_cpp.index(
    "sd_power_gpregret_set(0, DFU_MAGIC_UF2_RESET)"
)
reset = nrf_board_cpp.index("NVIC_SystemReset();", set_magic)
assert clear < set_magic < reset
assert "NRF_POWER->GPREGRET = DFU_MAGIC_UF2_RESET" in nrf_board_cpp

# CommonCLI covers repeater, room-server, and sensor roles. Companion and
# Terminal Chat have independent parsers, so each must delegate explicitly.
assert "mesh::cli::isUf2ResetCommand(command)" in common
assert "_board->rebootToUf2Bootloader()" in common
assert "sender_timestamp == 0" in common

assert "sender_timestamp == 0 && mesh::cli::isUf2ResetCommand(command)" in companion
assert "board.rebootToUf2Bootloader()" in companion
assert 'terminalOutput().println("  uf2reset");' in companion

assert "mesh::cli::isUf2ResetCommand(command)" in chat
assert "board.rebootToUf2Bootloader()" in chat
assert 'Serial.println("   uf2reset");' in chat

# Platform register manipulation must not be copied into role-specific CLI
# files; otherwise one role can drift again.
for role_source in (common, companion, chat):
    assert "DFU_MAGIC_UF2_RESET" not in role_source
    assert "sd_power_gpregret_set" not in role_source

print("test_nrf52_uf2reset_cli: PASS")
