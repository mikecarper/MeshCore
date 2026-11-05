#!/usr/bin/env python3
"""The scanner build must not silently enable LR2021 receive duty cycling."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
source = (root / 'src/helpers/radiolib/CustomLR2021Wrapper.h').read_text()
capability = (root / 'src/helpers/radiolib/LR2021RxpsCapability.h').read_text().replace('#pragma once', '')

# Preprocess actual wrapper sections instead of duplicating their guard logic.
support_marker = '  static constexpr bool SupportsRxPowerSaving = true;'
support_start = source.rfind('#if ', 0, source.index(support_marker))
support_end = source.index('  uint8_t regulatorModes()', support_start)
support = source[support_start:support_end]
receive_start = source.index('  int startReceiveMode() override {')
receive_end = source.index('  bool radioDeepInit() override {', receive_start)
receive = source[receive_start:receive_end]


def preprocessed(*defines):
    args = ['g++', '-E', '-P', '-x', 'c++', '-']
    args.extend('-D' + define for define in defines)
    return subprocess.run(args, input=capability + support + receive, text=True,
                          stdout=subprocess.PIPE, check=True).stdout


for flags in ((), ('MESH_W12_PRAM_EXPERIMENT=1',),
              ('MESH_W12_RXPS_EXPERIMENT=1',),
              ('MESHNOLOGY_W12=1', 'MESH_W12_PRAM_EXPERIMENT=1', 'MESH_W12_RXPS_EXPERIMENT=0'),
              ('MESHNOLOGY_W12=1', 'MESH_W12_PRAM_EXPERIMENT=0', 'MESH_W12_RXPS_EXPERIMENT=1')):
    result = preprocessed(*flags)
    assert support_marker not in result, flags
    assert 'startReceivePowerSaving()' not in result, flags
    assert 'labStartRxDutyCycle(' not in result, flags
    assert '_radio->startReceive()' in result, flags

for flags in (('MESHNOLOGY_W12=1', 'MESH_W12_PRAM_EXPERIMENT=1', 'MESH_W12_RXPS_EXPERIMENT=1'),
              ('MESH_LR2021_RXPS_ENABLE=1', 'MESH_LR2021_RXPS_QUALIFIED=1',
               'MESH_LR2021_RXPS_TRANSITION_US=1000')):
    result = preprocessed(*flags)
    assert support_marker in result, flags
    assert 'startReceivePowerSaving()' in result, flags
    assert 'labStartRxDutyCycle(' in result, flags
for flags in (('MESH_LR2021_RXPS_ENABLE=1',),
              ('MESH_LR2021_RXPS_ENABLE=1', 'MESH_LR2021_RXPS_QUALIFIED=1'),
              ('MESH_LR2021_RXPS_ENABLE=1', 'MESH_LR2021_RXPS_QUALIFIED=1',
               'MESH_LR2021_RXPS_TRANSITION_US=999')):
    args = ['g++', '-E', '-P', '-x', 'c++', '-'] + ['-D' + f for f in flags]
    result = subprocess.run(args, input=capability, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    assert result.returncode != 0, flags
# The HAL tracing and busy-pin diagnostics exist solely to inspect RXPS.
# Scanner-only firmware must not retain their buffers or singleton state.
hal = (root / 'src/helpers/radiolib/TunableRadioHal.h').read_text()
hal = '\n'.join(line for line in hal.splitlines() if not line.lstrip().startswith('#include'))
lab = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
for flags, enabled in ((('MESH_W12_PRAM_EXPERIMENT=1',), False),
                       (('MESH_W12_PRAM_EXPERIMENT=1', 'MESH_W12_RXPS_EXPERIMENT=1'), True)):
    args = ['g++', '-E', '-P', '-x', 'c++', '-'] + ['-D' + f for f in flags]
    expanded_hal = subprocess.run(args, input=hal, text=True, stdout=subprocess.PIPE, check=True).stdout
    expanded_lab = subprocess.run(args, input=lab, text=True, stdout=subprocess.PIPE, check=True).stdout
    assert ('_trace_out' in expanded_hal) == enabled, flags
    assert ('spiTrace().add' in expanded_hal) == enabled, flags
    assert ('_lab_busy_high' in expanded_lab) == enabled, flags
    assert ('labRxpsBusyStatus' in expanded_lab) == enabled, flags
print('PASS W12 scanner does not implicitly enable experimental RXPS')
