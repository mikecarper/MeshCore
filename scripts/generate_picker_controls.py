#!/usr/bin/env python3
"""Generate release-bound picker controls from qualified manifests and resolved PIO config.

Resolve PlatformIO configuration separately, with no other PIO process running:
  pio project config --json-output > /tmp/pio-config.json
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def capacity_note(reductions, defines):
    notes = []
    for reduction in reductions:
        contacts = re.fullmatch(r'companion.capacity limited to (\d+) contacts for runtime RAM; (\d+) queued frames and all Full transports retained', reduction)
        compact = re.fullmatch(r'companion.capacity limited to (\d+) contacts, (\d+) channels, and (\d+) queued frames by measured internal DRAM', reduction)
        queue = re.fullmatch(r'nRF52 Full: (\d+) offline frames normally; (\d+) while mOTA borrows queue storage', reduction)
        paper = re.fullmatch(r'Wireless Paper Full: (\d+) contacts; (\d+) offline frames normally, (\d+) while mOTA borrows queue storage', reduction)
        neighbors = re.match(r'mesh.neighbors limited to (\d+)\b', reduction)
        rules = re.match(r'mesh.flood_rules limited to (\d+)\b', reduction)
        if contacts:
            count, frames = contacts.groups()
            notes.append(f'Full Companion capacity: {count} contacts and {frames} queued messages; all Full transports are retained. '
                         f'Export contacts before updating if you have more than {count}; entries beyond this limit may be unavailable or omitted by a later save.')
        elif compact:
            count, channels, frames = compact.groups()
            notes.append(f'Full Companion capacity: {count} contacts, {channels} channels and {frames} queued messages. '
                         'Export contacts and channels before updating if they exceed these limits; extra entries may be unavailable or omitted by a later save.')
        elif queue or paper:
            if paper:
                count, normal, borrowed = paper.groups()
                channels = defines.get('MAX_GROUP_CHANNELS', '')
                capacity = f'{count} contacts'
                if re.fullmatch(r'\d+', channels):
                    capacity += f' and {channels} channels'
                notes.append(f'Wireless Paper Full capacity: {capacity}.')
            else:
                normal, borrowed = queue.groups()
            notes.append(f'Full Companion queue: {normal} offline messages normally; {borrowed} while mOTA borrows queue storage. '
                         f'Sync unread messages with a Companion app before starting mOTA if more than {borrowed} are pending. '
                         f'Stopping or disconnecting the source restores all {normal} slots.')
        elif neighbors:
            notes.append(f'Neighbor table: {neighbors[1]} entries.')
        elif rules:
            notes.append(f'Flood rules: {rules[1]} entries; the complete rule engine is retained.')
        elif reduction.startswith(('companion.capacity', 'nRF52 Full:', 'Wireless Paper Full:', 'mesh.neighbors', 'mesh.flood_rules')):
            raise ValueError('Unrecognized capacity reduction: ' + reduction)
    return ' '.join(notes)


def runtime_metadata(manifest, mqtt):
    """Keep picker choices tied to capabilities proven in this exact image."""
    capabilities = set(manifest['capabilities'])
    verified = {check['capability'] for check in manifest.get('verification', [])
                if check.get('present') is True}
    packaged = {check['capability'] for check in manifest.get('verification', [])
                if check.get('present') is True and check.get('source') == 'packaged application'}
    if 'ota.update.lora' in capabilities and 'lora' in manifest.get('ota_update_methods', []):
        ota_role = 'lora-receiver'
    elif 'companion.mota_sender' in verified:
        ota_role = 'lora-source'
    else:
        ota_role = 'none'
    result = {'otaRole': ota_role}
    if {'logging.usb.packets', 'logging.usb.control'} <= packaged:
        result['loggingModes'] = ['none', 'usb', 'wifi', 'both'] if mqtt else ['none', 'usb']
        result['loggingControl'] = 'logging.output' if mqtt else 'usb.logging'
        result['loggingSource'] = manifest['source_commit']
    if 'companion.dedicated_usb_logging' in verified:
        result['dedicatedUsbLogging'] = True
    return result


def generate(stage, config):
    plan = json.loads((stage / 'release-plan.json').read_text())
    envs = {name: dict(options) for name, options in config}
    profiles = {}
    for group in plan['groups']:
        for manifest in json.loads((stage / group['key'] / 'TARGET-MANIFEST.json').read_text()):
            if not manifest.get('verified'):
                raise ValueError('Unqualified target: ' + manifest['target'])
            env = envs['env:' + manifest['platformio_env']]
            flags = env.get('build_flags', [])
            defines = {}
            for flag in flags:
                undefine = re.match(r'-U\s*(\w+)$', flag.strip())
                if undefine:
                    defines.pop(undefine[1], None)
                match = re.match(r'-D\s*([\w]+)(?:=(.*))?$', flag.strip())
                if match:
                    defines[match[1]] = match[2] or '1'
            def enabled(name):
                return name in defines and defines[name] != '0'
            board_source = ''
            for flag in flags:
                match = re.fullmatch(r'-I\s+(variants/[\w.-]+)', flag.strip())
                if match:
                    for file in sorted((ROOT / match[1]).glob('*')):
                        if file.suffix in {'.h', '.cpp'}:
                            board_source += file.read_text(errors='replace')
            caps = set(manifest['capabilities'])
            controls = {
                'platform': manifest['platform'],
                'gps': enabled('ENV_INCLUDE_GPS'),
                'display': defines.get('DISPLAY_CLASS', 'NullDisplayDriver') != 'NullDisplayDriver',
                'rxgain': enabled('SX126X_RX_BOOSTED_GAIN') or enabled('LR1110_RX_BOOSTED_GAIN'),
                'rxps': 'SX126' in defines.get('RADIO_CLASS', ''),
                'femRx': bool(re.search(r'bool\s+\w*(?:::)?setLoRaFemLnaEnabled\s*\(', board_source)),
                'femTx': bool(re.search(r'bool\s+\w*(?:::)?setLoRaFemPaGainEnabled\s*\(', board_source)),
                'webconfig': 'web.webconfig' in caps,
                'mqtt': enabled('WITH_MQTT_BRIDGE'),
                'rs232': enabled('WITH_RS232_BRIDGE'),
                # Expanded ESP32 Full observer builds add ESP-NOW at build
                # time, so it is recorded in the qualified manifest instead
                # of the base PlatformIO environment's static flags.
                'espnowBridge': enabled('WITH_ESPNOW_BRIDGE') or 'bridge.espnow' in caps,
                'primaryEspnow': enabled('MESH_PRIMARY_ESPNOW'),
                'snmp': enabled('WITH_SNMP'),
                'updateMethods': manifest.get('ota_update_methods', []),
            }
            controls.update(runtime_metadata(manifest, controls['mqtt']))
            if manifest.get('ota_update_requirements'):
                controls['updateRequirements'] = manifest['ota_update_requirements']
            note = capacity_note(manifest.get('reductions', []), defines)
            if note:
                source = manifest.get('source_commit', plan['source'])
                if not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{40}', source):
                    raise ValueError('Capacity notes require an exact source commit: ' + manifest['target'])
                controls['memoryNote'] = note
                controls['memorySource'] = source
            for name in manifest['files']:
                if not name.endswith(('.bin', '.uf2', '.zip', '.hex')):
                    continue
                # Match parseFirmwareAsset's target extraction, including the
                # separate -ota- marker and firmware's unchanged source tag.
                target = re.split(r'-(?:ota-)?v\d+\.', name, maxsplit=1)[0]
                if target in profiles and profiles[target] != controls:
                    raise ValueError('Conflicting target metadata: ' + target)
                profiles[target] = controls
    family = next(g['tag'] for g in plan['groups'] if g['key'] == 'companion')
    return {'familyTag': family, 'source': plan['source'], 'profiles': profiles}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--pio-config', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/_data/firmware_controls.json')
    args = parser.parse_args()
    result = generate(args.stage, json.loads(args.pio_config.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(f"Wrote controls for {len(result['profiles'])} qualified profiles")
