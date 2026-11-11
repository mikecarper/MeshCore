#!/usr/bin/env python3
"""Prepare matched Bluedroid sources without modifying the SDK.

Input is the components/bt directory of the exact supported ESP-IDF source.
Output is a separate source tree; this utility does not compile or install it.
"""
import argparse
import hashlib
from pathlib import Path

FILES = {
    "host/bluedroid/btc/core/btc_dm.c": "ea1c058a6ad54cbff63c94e29b9fdd32017625342e81f0dfb3f26a018aa6481d",
    "host/bluedroid/bta/dm/bta_dm_act.c": "a80842e8c2e28046db789d5f50ab4778ed35d51dcaa8dceddd6d356653837e15",
    "host/bluedroid/stack/btm/btm_sec.c": "d3207f4b4a369cf534f128c124fca8192979769a01986ed46bcb15ace4c8b394",
}


def replace_one(text, old, new):
    count = text.count(old)
    if count != 1:
        raise ValueError(f"patch anchor count={count}: {old[:80]!r}")
    return text.replace(old, new, 1)


def patch_sources(source):
    btc_path, bta_path, btm_path = FILES
    btc = source[btc_path]
    start = btc.index("static void btc_dm_ble_auth_cmpl_evt (")
    end = btc.index("\nstatic void btc_dm_link_up_evt(", start)
    head, segment, tail = btc[:start], btc[start:end], btc[end:]
    segment = replace_one(segment, '    if (p_auth_cmpl->success) {\n        status = BT_STATUS_SUCCESS;',
        '    if (p_auth_cmpl->success) {\n        if (mesh_bl_narrow_pending() || (mesh_bl_staged && memcmp(mesh_bl_staged_addr, p_auth_cmpl->bd_addr, 6) != 0)) {\n            memset(&btc_dm_cb.pairing_cb.ble, 0, sizeof(btc_dm_cb.pairing_cb.ble));\n            mesh_bl_staged = 0;\n            mesh_bl_narrow_failure();\n            p_auth_cmpl->success = FALSE;\n            return;\n        }\n        status = BT_STATUS_SUCCESS;')
    segment = replace_one(segment, '    } else {\n        /*Map the HCI fail reason  to  bt status  */',
        '    } else {\n        memset(&btc_dm_cb.pairing_cb.ble, 0, sizeof(btc_dm_cb.pairing_cb.ble));\n        mesh_bl_staged = 0;\n        mesh_bl_narrow_failure();\n        /*Map the HCI fail reason  to  bt status  */')
    segment = replace_one(segment, '        if((p_auth_cmpl->auth_mode & SMP_AUTH_GEN_BOND) == 0) {\n            return;\n        }',
        '        if((p_auth_cmpl->auth_mode & SMP_AUTH_GEN_BOND) == 0) {\n            memset(&btc_dm_cb.pairing_cb.ble, 0, sizeof(btc_dm_cb.pairing_cb.ble));\n            mesh_bl_staged = 0;\n            return;\n        }')
    segment = replace_one(segment, '        btc_dm_save_ble_bonding_keys();',
        '        btc_dm_save_ble_bonding_keys();\n        mesh_bl_staged = 0;')
    old = '            btc_dm_remove_ble_bonding_keys();'
    if segment.count(old) != 2:
        raise ValueError("unexpected persisted-bond deletion count")
    segment = segment.replace(old, '            /* Preserve the saved bond on failed authentication. */')
    btc = ('extern void mesh_bl_narrow_failure(void);\nextern int mesh_bl_narrow_pending(void);\n'
           'static unsigned char mesh_bl_staged_addr[6];\nstatic int mesh_bl_staged;\n'
           'void mesh_bl_narrow_reset_staging(void) { mesh_bl_staged = 0; }\n'
           'void mesh_bl_narrow_sdk_guard(void) {}\n') + head + segment + tail
    btc = replace_one(btc, '        btc_dm_ble_auth_cmpl_evt(&p_data->auth_cmpl);\n        break;',
        '        btc_dm_ble_auth_cmpl_evt(&p_data->auth_cmpl);\n        param.ble_security.auth_cmpl.success = p_data->auth_cmpl.success ? true : false;\n        break;')
    btc = replace_one(btc, '    case BTA_DM_BLE_KEY_EVT: {\n        rsp_app = true;',
        '    case BTA_DM_BLE_KEY_EVT: {\n        if (mesh_bl_narrow_pending() || (mesh_bl_staged && memcmp(mesh_bl_staged_addr, p_data->ble_key.bd_addr, 6) != 0)) {\n            memset(&btc_dm_cb.pairing_cb.ble, 0, sizeof(btc_dm_cb.pairing_cb.ble));\n            mesh_bl_staged = 0;\n            mesh_bl_narrow_failure();\n            rsp_app = false;\n            break;\n        }\n        if (!mesh_bl_staged) { memcpy(mesh_bl_staged_addr, p_data->ble_key.bd_addr, 6); mesh_bl_staged = 1; }\n        rsp_app = true;')
    bta = 'extern void mesh_bl_narrow_failure(void);\n' + source[bta_path]
    bta = replace_one(bta, '            /* delete this device entry from Sec Dev DB */\n            bta_dm_remove_sec_dev_entry (bda);',
        '            /* Keep the saved peer and reload the live record after failure. */\n            mesh_bl_narrow_failure();')
    btm = 'extern void mesh_bl_narrow_failure(void);\n' + source[btm_path]
    btm = replace_one(btm, '            p_dev_rec->sec_flags &= ~ (BTM_SEC_LE_LINK_KEY_KNOWN);\n            p_dev_rec->ble.key_type = BTM_LE_KEY_NONE;',
        '            /* Keep the saved bond; reload live security records. */\n            mesh_bl_narrow_failure();')
    return {btc_path: btc, bta_path: bta, btm_path: btm}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source_root = args.source_root.resolve(strict=True)
    output_dir = args.output_dir.resolve()
    if output_dir == source_root or source_root in output_dir.parents:
        parser.error('output must not be inside the input source tree')
    sources = {}
    for path, expected in FILES.items():
        raw = (source_root / path).read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise SystemExit(f'{path}: unrecognized SHA-256 {actual}')
        sources[path] = raw.decode('utf-8').replace('\r\n', '\n')
    patched = patch_sources(sources)
    for path, content in patched.items():
        dest = output_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding='utf-8', newline='\n')
        print(path, hashlib.sha256(dest.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
