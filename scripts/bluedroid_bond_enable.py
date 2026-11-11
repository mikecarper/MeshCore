"""Select the matched SDK before PlatformIO clones compilation environments."""
Import('env')
import hashlib
import json
import os
from pathlib import Path

def enable():
    definitions = env.ParseFlags(env.get('BUILD_FLAGS', []))['CPPDEFINES']
    names = {d[0] if isinstance(d, (tuple, list)) else str(d).split('=')[0]
             for d in definitions}
    if 'BLE_PIN_CODE' not in names or 'MESH_USE_NIMBLE_ARDUINO' in names:
        return
    if os.environ.get('MESHCORE_BLUEDROID_BOND_PATCH') == '0':
        return
    mcu = env.BoardConfig().get('build.mcu', '')
    root = Path(env.subst('$PROJECT_DIR'))
    entry = json.loads((root/'scripts/bluedroid_bond_manifest.json').read_text())['mcus'].get(mcu)
    if not entry:
        return
    framework = env.PioPlatform().get_package_dir('framework-arduinoespressif32')
    if not framework:
        return
    stock = Path(framework)/'tools/sdk'/mcu/'lib/libbt.a'
    if stock.is_file() and hashlib.sha256(stock.read_bytes()).hexdigest() == entry['stock_sha256']:
        env.AppendUnique(BUILD_FLAGS=['-D MESH_BLUEDROID_BOND_PROTECTION=1'])

enable()
