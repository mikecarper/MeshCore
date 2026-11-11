"""Install matched Bluedroid bond-preservation objects in a private SDK copy."""
Import('env')

import hashlib
import json
import os
import sys
from pathlib import Path

project = Path(env.subst('$PROJECT_DIR'))
sys.path.insert(0, str(project / 'scripts'))
from bluedroid_bond_archive import ArchiveError, stage_archive, verify_link_map


def install():
    definitions = env.ParseFlags(env.get('BUILD_FLAGS', []))['CPPDEFINES']
    names = {d[0] if isinstance(d, (tuple, list)) else str(d).split('=')[0]
             for d in definitions}
    if 'BLE_PIN_CODE' not in names or 'MESH_USE_NIMBLE_ARDUINO' in names:
        return
    if os.environ.get('MESHCORE_BLUEDROID_BOND_PATCH') == '0':
        return
    mcu = env.BoardConfig().get('build.mcu', '')
    manifest = project / 'scripts' / 'bluedroid_bond_manifest.json'
    entry = json.loads(manifest.read_text())['mcus'].get(mcu)
    if entry is None:
        return
    framework = env.PioPlatform().get_package_dir('framework-arduinoespressif32')
    stock = Path(framework) / 'tools' / 'sdk' / mcu / 'lib' / 'libbt.a'
    if not stock.is_file() or hashlib.sha256(stock.read_bytes()).hexdigest() != entry['stock_sha256']:
        if 'MESH_BLUEDROID_BOND_PROTECTION' in names or os.environ.get('MESHCORE_BLUEDROID_BOND_PATCH') == '1':
            raise ArchiveError('unsupported Bluedroid SDK for requested bond protection')
        print('Bluetooth: bond-preservation patch unavailable for this SDK; using its original library')
        return
    staged = Path(env.subst('$BUILD_DIR')) / 'bluedroid-bond' / 'libbt.a'
    if 'MESH_BLUEDROID_BOND_PROTECTION' not in names:
        raise ArchiveError('matching SDK selected without the early application build flag')
    stage_archive(manifest, mcu, stock,
                  project / 'vendor' / 'bluedroid' / (mcu + '-idf4-bond-objects.zip'),
                  staged, env.subst('$AR'), dict(env['ENV']))
    env.Prepend(LIBPATH=[str(staged.parent)])
    env.AppendUnique(LINKFLAGS=['-Wl,-u,mesh_bl_narrow_sdk_guard'])

    def verify_link(source, target, env):
        verify_link_map(Path(env.subst('$BUILD_DIR')) / (env.subst('${PROGNAME}') + '.map'),
                        project, staged)

    env.AddPostAction('$BUILD_DIR/${PROGNAME}.elf', verify_link)
    print('Bluetooth: preserving bonds with verified ' + mcu + ' Bluedroid objects')


try:
    install()
except Exception as exc:
    print('Bluetooth archive rejected: ' + str(exc), file=sys.stderr)
    env.Exit(1)
