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
                'espnowBridge': enabled('WITH_ESPNOW_BRIDGE'),
                'primaryEspnow': enabled('MESH_PRIMARY_ESPNOW'),
                'snmp': enabled('WITH_SNMP'),
                'updateMethods': manifest.get('ota_update_methods', []),
            }
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
