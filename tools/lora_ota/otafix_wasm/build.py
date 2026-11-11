#!/usr/bin/env python3
"""Build isolated OTAFIX RAK internal-path WASM prototypes.

Set WASI_SDK_PATH to the extracted WASI SDK 34.0 x86_64 Linux release.
These modules are research artifacts; no updater currently authorizes them.
"""
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SDK = Path(os.environ.get('WASI_SDK_PATH', ''))
OUT = HERE / 'build'


def main():
    cc = SDK / 'bin/clang'
    if not cc.is_file():
        raise SystemExit('Set WASI_SDK_PATH to the extracted WASI SDK 34.0')
    OUT.mkdir(exist_ok=True)
    exports = ['sim_base', 'sim_package', 'sim_result', 'sim_status', 'sim_commit_size', 'sim_apply']
    link = ['-Wl,--no-entry', '-Wl,--export-memory', '-Wl,--gc-sections']
    link += ['-Wl,--export=' + symbol for symbol in exports]
    for version in ('2.4.3', '2.4.6', '2.4.9', '2.4.11', '2.4.12'):
        src = HERE / ('otafix-' + version) / 'src'
        common = [str(cc), '-std=c11', '-Oz', '-nostartfiles', '-Wno-unknown-attributes',
                  '-ffile-prefix-map=' + str(HERE) + '=.', '-DOTA_DELTA_HOST_TEST',
                  '-DDETOOLS_CONFIG_IN_PLACE_RESUME=0', '-DUSB_DESC_VID=0x239A',
                  '-DUSB_DESC_UF2_PID=0x0029', '-DMOTA_INTERNAL_BOOTLOADER_UPDATE=1',
                  '-I' + str(src), '-I' + str(src / 'detools')]
        sources = [HERE / 'adapter.c', src / 'ota_delta.c', src / 'detools/detools.c',
                   src / 'sha256.c', src / 'usb/uf2/bootloader_image.c']
        for board in ('3401', '4631'):
            flags = ['-DDEVICE_NAME="' + board + '_DFU"']
            if version in ('2.4.6', '2.4.9'):
                flags += ['-DMOTA_RAM_ARENA_SIZE=65536']
            if version in ('2.4.11', '2.4.12'):
                flags += ['-DMOTA_QSPI_FLASH=1', '-DMOTA_RAK_AUTO_STORE=1', '-DMOTA_RAM_ARENA_SIZE=65536']
                if board == '4631': flags += ['-DMOTA_RAK_AUTO_RAK4631=1']
            if version == '2.4.12' and board == '4631':
                flags += ['-DMOTA_RAK_SD_STORE=1', '-DMOTA_SD_CARD=1']
            subprocess.run(common + flags + [str(p) for p in sources] + link +
                           ['-o', str(OUT / ('otafix-' + version + '-rak' + board + '-internal.wasm'))], check=True)


if __name__ == '__main__':
    sys.exit(main())
