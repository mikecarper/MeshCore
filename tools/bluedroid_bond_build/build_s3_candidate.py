#!/usr/bin/env python3
"""Build an *unqualified* ESP32-S3 Bluedroid archive for laboratory use.

Rebuilds the three stock objects first and rejects any allocated-section,
relocation, or exported-symbol difference. Does not approve or enable the
result in firmware builds and does not write to the installed SDK.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LIB_SHA = '1cc7d2371f7df850b9cdfcb5f4f688f8bffbac007fd6de5828589f84b7d32af7'
CONFIG_SHA = '6fb31ef78f105d86f3c48a9ca23fa2fe583549ad305fa487667703c6c40ddf18'
GCC_SHA = 'cefe2b9941ac0829338c56f372ddb9dbd3b30e8320121670a686b8e1cdac911a'
AR_SHA = '0ca14e7a07bc598dbad7a532b4b8f279dfabf50957d310c508f587108ac2ba03'
SRC = {
    'btc_dm.c.obj': 'host/bluedroid/btc/core/btc_dm.c',
    'bta_dm_act.c.obj': 'host/bluedroid/bta/dm/bta_dm_act.c',
    'btm_sec.c.obj': 'host/bluedroid/stack/btm/btm_sec.c',
}
INCLUDES = [
    ('sdk', 'dio_qspi/include'), ('bt', 'host/bluedroid/common/include'),
    ('bt', 'common/include'), ('sdk', 'include/log/include'),
    ('sdk', 'include/esp_rom/include'), ('sdk', 'include/soc/esp32s3/include'),
    ('sdk', 'include/esp_common/include'), ('bt', 'host/bluedroid/api/include/api'),
    ('bt', 'host/bluedroid/stack/include'), ('bt', 'common/osi/include'),
    ('sdk', 'include/freertos/include'), ('sdk', 'include/freertos/include/esp_additions/freertos'),
    ('sdk', 'include/freertos/port/xtensa/include'), ('sdk', 'include/xtensa/include'),
    ('sdk', 'include/xtensa/esp32s3/include'), ('sdk', 'include/esp_hw_support/include'),
    ('sdk', 'include/hal/include'), ('sdk', 'include/hal/esp32s3/include'),
    ('sdk', 'include/soc/include'), ('sdk', 'include/esp_system/include'),
    ('sdk', 'include/esp_timer/include'), ('sdk', 'include/newlib/platform_include'),
    ('sdk', 'include/heap/include'), ('sdk', 'include/freertos/include/esp_additions'),
    ('bt', 'host/bluedroid/btc/include'), ('bt', 'common/btc/include'),
    ('bt', 'host/bluedroid/bta/include'), ('sdk', 'include/lwip/port/esp32/include'),
    ('sdk', 'include/lwip/lwip/src/include'), ('bt', 'host/bluedroid/btc/profile/std/include'),
    ('bt', 'host/bluedroid/stack/btm/include'), ('bt', 'host/bluedroid/bta/dm/include'),
    ('bt', 'host/bluedroid/device/include'), ('bt', 'host/bluedroid/hci/include'),
    ('bt', 'host/bluedroid/stack/l2cap/include'), ('bt', 'host/bluedroid/stack/gatt/include'),
]
FLAGS = ['-c', '-mlongcalls', '-ggdb', '-Os', '-std=gnu99', '-ffunction-sections',
         '-fdata-sections', '-freorder-blocks', '-fstack-protector',
         '-fstrict-volatile-bitfields', '-fno-jump-tables', '-fno-tree-switch-conversion',
         '-DESP_PLATFORM', '-D_GNU_SOURCE', '-include', 'sdkconfig.h']


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def check(path, expected):
    actual = digest(path)
    if actual != expected:
        raise ValueError(f'unrecognized input {path}: {actual}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--idf-bt', required=True, type=Path, help='Matching IDF components/bt source tree')
    p.add_argument('--sdk', required=True, type=Path, help='Installed framework tools/sdk/esp32s3')
    p.add_argument('--toolchain-bin', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path, help='New output archive path')
    a = p.parse_args()
    bt, sdk, tools, output = (x.resolve() for x in (a.idf_bt, a.sdk, a.toolchain_bin, a.output))
    if output.exists():
        raise ValueError(f'output exists: {output}')
    if any(x == output or x in output.parents for x in (bt, sdk, tools)):
        raise ValueError('output must be outside all input trees')
    gcc, ar = (tools / ('xtensa-esp32s3-elf-' + x + ('.exe' if os.name == 'nt' else '')) for x in ('gcc', 'ar'))
    lib = sdk/'lib/libbt.a'
    for file, expected in ((lib, LIB_SHA), (sdk/'dio_qspi/include/sdkconfig.h', CONFIG_SHA),
                           (gcc, GCC_SHA), (ar, AR_SHA)):
        check(file, expected)
    env = os.environ.copy()
    env['PATH'] = str(tools) + os.pathsep + env['PATH']
    spec = importlib.util.spec_from_file_location('objcompare', HERE/'compare_objects.py')
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    with tempfile.TemporaryDirectory(prefix='mesh-bt-s3-') as tmp:
        work = Path(tmp)
        stock, changed, extracted = (work/x for x in ('stock', 'changed', 'extracted'))
        for d in (stock, changed, extracted):
            d.mkdir()
        members = subprocess.check_output([str(ar), 't', str(lib)], text=True, env=env).splitlines()
        if len(set(members)) != len(members) or any('/' in x or '\\' in x for x in members):
            raise ValueError('archive has duplicate or unsafe member names')
        if any(name not in members for name in SRC):
            raise ValueError('expected source object missing from archive')
        subprocess.run([str(ar), 'x', str(lib)], cwd=stock, check=True, env=env)
        generated = work/'modified'/'esp-idf'/'components'/'bt'
        subprocess.run([sys.executable, str(ROOT/'tools/bluedroid_bond_patch/generate.py'),
                        '--source-root', str(bt), '--output-dir', str(generated)], check=True, env=env)
        unmodified_root = work/'original'/'esp-idf'/'components'/'bt'
        build_dirs = (work/'original'/'build', work/'modified'/'build')
        for d in build_dirs:
            d.mkdir(parents=True)
        for name, rel in SRC.items():
            # Keep the same ordered include search that reproduced each stock
            # object; adding unrelated paths can select a different header.
            limit = {'btc_dm.c.obj': 31, 'bta_dm_act.c.obj': 34,
                     'btm_sec.c.obj': 36}[name]
            include_args = ['-I' + str((sdk if kind == 'sdk' else bt)/path)
                            for kind, path in INCLUDES[:limit]]
            built = work/'unmodified'/name
            built.parent.mkdir(exist_ok=True)
            original_source = unmodified_root/rel
            original_source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bt/rel, original_source)
            # Some BT modules embed __FILE__ in allocated strings. Match the
            # stock IDF relative source path, independent of the workspace.
            source_arg = '../esp-idf/components/bt/' + rel
            stable_debug = '-fdebug-prefix-map=' + str(work) + '=/meshcore-bt-build'
            subprocess.run([str(gcc), *FLAGS, stable_debug, *include_args, source_arg, '-o', str(built)],
                           cwd=build_dirs[0], check=True, env=env)
            try:
                compare.compare(stock/name, built)
            except ValueError as exc:
                raise ValueError(f'{name}: unmodified object mismatch: {exc}') from exc
            print(f'{name}: stock object comparison passed', flush=True)
            subprocess.run([str(gcc), *FLAGS, stable_debug, *include_args, source_arg, '-o', str(changed/name)],
                           cwd=build_dirs[1], check=True, env=env)
        candidate = work/'libbt.a'
        shutil.copyfile(lib, candidate)
        subprocess.run([str(ar), 'rD', str(candidate), *[str(changed/n) for n in SRC]], check=True, env=env)
        got = subprocess.check_output([str(ar), 't', str(candidate)], text=True, env=env).splitlines()
        if got != members:
            raise ValueError('archive member ordering changed')
        subprocess.run([str(ar), 'x', str(candidate)], cwd=extracted, check=True, env=env)
        for name in members:
            expected = changed/name if name in SRC else stock/name
            if digest(extracted/name) != digest(expected):
                raise ValueError(f'archive member differs unexpectedly: {name}')
            if name in SRC and digest(extracted/name) == digest(stock/name):
                raise ValueError(f'expected patched object was unchanged: {name}')
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate, output)
        print(json.dumps({'stock_sha256': LIB_SHA, 'candidate_sha256': digest(output),
                          'archive_members_verified': len(members), 'changed': list(SRC)}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
