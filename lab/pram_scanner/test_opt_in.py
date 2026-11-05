#!/usr/bin/env python3
"""W12 infrastructure includes and automatically starts the scanner when eligible."""
from pathlib import Path
import configparser
import re

root = Path(__file__).resolve().parents[2]
configs = [root / 'platformio.ini', *sorted((root / 'variants').rglob('*.ini')),
           *sorted(root.glob('platformio.*.ini'))]
flag = re.compile(r'(?:^|\s)-D\s*MESH_W12_PRAM_EXPERIMENT(?:\s|=|$)')
allowed_file = root / 'variants/meshnology_w12/platformio.ini'
experimental_file = root / 'variants/meshtracker_x1/platformio.ini'
for path in configs:
    section = ''
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith((';', '#')):
            continue
        if stripped.startswith('[') and stripped.endswith(']'):
            section = stripped[1:-1]
        if flag.search(line):
            assert (path == allowed_file and section == 'meshnology_w12_pram') or (
                path == experimental_file and section == 'MeshTracker_X1_pram'
            ), (path, number, section)

config = configparser.ConfigParser(interpolation=None)
config.read(allowed_file, encoding='utf-8')
assert flag.search(config['meshnology_w12_pram']['build_flags'])
roles = {'meshnology_w12_repeater', 'meshnology_w12_repeater_bridge_espnow',
         'meshnology_w12_repeater_observer_mqtt', 'meshnology_w12_room_server',
         'meshnology_w12_sensor'}
for section in config.sections():
    if not section.startswith('env:meshnology_w12_'):
        continue
    enabled = '${meshnology_w12_pram.build_flags}' in config[section].get('build_flags', '')
    assert enabled == (section[4:] in roles), (section, enabled)

source = (root / 'src/helpers/radiolib/W12PramScannerLab.inc').read_text(encoding='utf-8')
assert 'bool _lab_scan_active = false;' in source
assert 'bool _lab_resume_pending = false;' in source
assert 'labServiceAutomatic(rx_allowed, radio_owned)' in source
assert 'bool enabled_=true' in (root / 'src/helpers/radiolib/W12PramAutoPolicy.h').read_text(encoding='utf-8')
readme = (root / 'docs/radio_profiles.md').read_text(encoding='utf-8')
assert 'set pramscan on' in readme and 'set pramscan off' in readme
print('PASS W12 infrastructure scanner availability and automatic policy')
