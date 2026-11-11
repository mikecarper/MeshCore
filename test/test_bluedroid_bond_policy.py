"""Check the generated SDK storage policy with in-memory callback counters.

No Bluetooth controller, real keys, or radio connection is used.
"""
from pathlib import Path
import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'tools/bluedroid_bond_patch'
spec = importlib.util.spec_from_file_location('bond_patch', PATCH / 'generate.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
sources = {}
for path, expected in patch.FILES.items():
    raw = (PATCH / 'upstream' / path).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected, path
    sources[path] = raw.decode('utf-8')
source = patch.patch_sources(sources)['host/bluedroid/btc/core/btc_dm.c']
start = source.index('static void btc_dm_ble_auth_cmpl_evt (')
brace = source.index('{', start)
depth = 0
for i in range(brace, len(source)):
    depth += (source[i] == '{') - (source[i] == '}')
    if depth == 0:
        actual = source[start:i + 1]
        break
else:
    raise RuntimeError('unclosed function')

harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
typedef struct { unsigned char address[6]; } bt_bdaddr_t;
typedef struct { int success, auth_mode, addr_type, fail_reason; unsigned char bd_addr[6]; } tBTA_DM_AUTH_CMPL;
typedef struct { int is_pid_key_rcvd, pid_key, another_staged_key; } staged_t;
static struct { struct { unsigned char bd_addr[6]; staged_t ble; } pairing_cb; } btc_dm_cb;
typedef int bt_status_t;
#define BT_STATUS_FAIL 1
#define BT_STATUS_SUCCESS 0
#define BT_STATUS_AUTH_FAILURE 2
#define BT_STATUS_AUTH_REJECTED 3
#define BTA_DM_AUTH_SMP_PAIR_AUTH_FAIL 4
#define BTA_DM_AUTH_SMP_CONFIRM_VALUE_FAIL 5
#define BTA_DM_AUTH_SMP_PAIR_NOT_SUPPORT 6
#define SMP_AUTH_GEN_BOND 1
#define FALSE 0
#define BTC_TRACE_DEBUG(...) ((void)0)
#define CONFIG_BT_STACK_NO_LOG 1
#define bdcpy(dst,src) memcpy((dst),(src),6)
static int pending, saves, removes, metadata, unused;
static unsigned char mesh_bl_staged_addr[6];
static int mesh_bl_staged;
static int mesh_bl_narrow_pending(void) { return pending; }
static void mesh_bl_narrow_failure(void) { pending = 1; }
void btc_dm_remove_ble_bonding_keys(void) { ++removes; }
static void btc_dm_save_ble_bonding_keys(void) { ++saves; }
static void btc_storage_remove_unused_sections(const unsigned char *a,const int *b) {(void)a;(void)b;++unused;}
static int btc_storage_get_remote_addr_type(const bt_bdaddr_t *a,int *b) {(void)a;(void)b;return 1;}
static void btc_storage_set_remote_addr_type(const bt_bdaddr_t *a,int b,int c) {(void)a;(void)b;(void)c;++metadata;}
static void btc_storage_set_ble_dev_auth_mode(const bt_bdaddr_t *a,int b,int c) {(void)a;(void)b;(void)c;++metadata;}
''' + actual + r'''
int main(void) {
    for (int reason = 0; reason != 8; ++reason) {
        tBTA_DM_AUTH_CMPL event = {.success = 0, .auth_mode = 1, .fail_reason = reason};
        btc_dm_cb.pairing_cb.ble.is_pid_key_rcvd = 1;
        btc_dm_cb.pairing_cb.ble.another_staged_key = 9;
        pending = saves = removes = metadata = unused = mesh_bl_staged = 0;
        btc_dm_ble_auth_cmpl_evt(&event);
        assert(pending && !removes && !saves && !metadata && !unused);
        assert(!btc_dm_cb.pairing_cb.ble.is_pid_key_rcvd && !btc_dm_cb.pairing_cb.ble.another_staged_key);
        tBTA_DM_AUTH_CMPL blocked = {.success = 1, .auth_mode = 1};
        btc_dm_cb.pairing_cb.ble.another_staged_key = 2;
        btc_dm_ble_auth_cmpl_evt(&blocked);
        assert(!blocked.success && !saves && !metadata);
        assert(!btc_dm_cb.pairing_cb.ble.another_staged_key);
        pending = 0;
        tBTA_DM_AUTH_CMPL wrong = {.success = 1, .auth_mode = 1};
        mesh_bl_staged = 1; mesh_bl_staged_addr[0] = 1;
        btc_dm_ble_auth_cmpl_evt(&wrong);
        assert(!wrong.success && pending && !saves && !metadata && !mesh_bl_staged);
        pending = 0; mesh_bl_staged_addr[0] = 0;
        tBTA_DM_AUTH_CMPL success = {.success = 1, .auth_mode = 1};
        btc_dm_cb.pairing_cb.ble.is_pid_key_rcvd = 1; mesh_bl_staged = 1;
        btc_dm_ble_auth_cmpl_evt(&success);
        assert(success.success && saves == 1 && metadata == 2 && unused == 1 && !removes);
    }
    puts("PASS: actual patched IDF authentication failure and successful replacement path");
}
'''
with tempfile.TemporaryDirectory(prefix='mesh-bluedroid-narrow-') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(harness)
    binary = work / ('test.exe' if os.name == 'nt' else 'test')
    sanitizers = [] if os.name == 'nt' else ['-fsanitize=address,undefined', '-fno-pie', '-no-pie']
    compiler = os.environ.get('CC') or shutil.which('cc') or 'gcc'
    subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', *sanitizers, str(work/'test.c'), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
