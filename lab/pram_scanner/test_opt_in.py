#!/usr/bin/env python3
"""Keep the experimental flag out of all checked-in PlatformIO environments."""
from pathlib import Path
import configparser
import re
import shlex

root = Path(__file__).resolve().parents[2]
configs = [root / 'platformio.ini', *sorted((root / 'variants').rglob('*.ini')),
           *sorted(root.glob('platformio.*.ini'))]
define = re.compile(r'(?:^|\s)-D\s*MESH_W12_PRAM_EXPERIMENT(?:\s|=|$)')
for path in configs:
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if line.lstrip().startswith((';', '#')):
            continue
        assert not define.search(line), f'experimental flag enabled in {path}:{number}'

config = configparser.ConfigParser(interpolation=None)
config.read(root / 'variants/meshnology_w12/platformio.ini', encoding='utf-8')
flags = config['env:meshnology_w12_repeater_observer_mqtt']['build_flags']
assert '${meshnology_w12.build_flags}' in flags and '${meshnology_w12_auto_trim.build_flags}' in flags
assert not define.search(flags)
readme = (root / 'lab/pram_scanner/README.md').read_text(encoding='utf-8')
commands = [line.strip() for line in readme.splitlines()
            if line.strip().startswith('PLATFORMIO_BUILD_FLAGS=')]
assert len(commands) == 1
words = shlex.split(commands[0])
assert words[0].startswith('PLATFORMIO_BUILD_FLAGS=')
assert '-DMESH_W12_PRAM_EXPERIMENT=1' in words[0].split('=', 1)[1].split()
assert 'meshnology_w12_repeater_observer_mqtt' in words and '--full-exact' in words
print('PASS W12 PRAM experiment requires an explicit build flag')
