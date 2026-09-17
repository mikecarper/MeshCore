"""Audit CLI get/set coverage; execute migrated radio settings through real branches.

The inventory checks parser entry points, not documentation/whitelist mentions.
Read-only queries and settings written through another command have explicit
exceptions. Hardware callbacks are mocked in the native round-trip test.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / 'src/helpers/CommonCLI.cpp'
OBSERVER = ROOT / 'src/helpers/CommonCLI_Observer.cpp'
RADIO = ROOT / 'src/helpers/CommonRadioPrefs.cpp'
TOKEN = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]')


def literal_keys(method, variable='config', top_level=True):
    """Extract literal key comparisons, excluding nested comparisons of values."""
    matches = list(re.finditer(
        r'(?:memcmp|strncmp|strcmp|configKeyEquals)\(' + variable +
        r',\s*"([a-z][\w.]*)(?: [^"\n]*)?"', method))
    depth, cursor, result = 0, 0, set()
    tokens = list(TOKEN.finditer(method))
    for match in matches:
        while cursor < len(tokens) and tokens[cursor].start() < match.start():
            depth += (tokens[cursor].group() == '{') - (tokens[cursor].group() == '}')
            cursor += 1
        if not top_level or depth == 1:
            result.add(match[1])
    return result


def methods(text, prefix):
    return tuple(extract_braced(text, f'{prefix}{verb}Cmd(') for verb in ('Set', 'Get'))


def full_command_keys(text):
    comparisons = re.findall(
        r'(?:strcmp|strncmp|memcmp)\((?:command|cmd|text),\s*"(get|set) ([a-z][\w.]*)[ "]', text)
    return tuple({key for kind, key in comparisons if kind == verb} for verb in ('set', 'get'))


COMMON_QUERY_EXCEPTIONS = {
    'bootloader.ver': 'Installed bootloader identity',
    'bridge.running': 'Live bridge state; set bridge.enabled controls intent',
    'espnow.running': 'Live ESP-NOW bridge state; set espnow.enabled controls intent',
    'bridge.type': 'Build-selected bridge transport',
    'password': 'Written by the password command, not set password',
    'public.key': 'Derived from identity; written through set prv.key',
    'pwrmgt.bootmv': 'Boot-time voltage measurement',
    'pwrmgt.bootreason': 'Boot/reset cause',
    'pwrmgt.source': 'Measured power source',
    'pwrmgt.support': 'Hardware power-management capabilities',
    'radio.rxps.config': 'Detailed view of set radio.rxps',
    'role': 'Build-selected firmware role',
    'rxps.wd': 'Runtime RX power-saving recovery counters',
    'wifi.pwd': 'Setter is in the observer or standalone-WiFi delegate',
}
OBSERVER_QUERY_EXCEPTIONS = {
    'mqtt.config.valid': 'Configuration validation result',
    'mqtt.ntp.diag': 'NTP diagnostics',
    'mqtt.presets': 'List of available presets',
    'mqtt.running': 'Live state; set mqtt.enabled controls intent',
    'mqtt.stats': 'Runtime MQTT statistics',
    'wifi.status': 'Live connection status',
}


class CLISettingsContractTest(unittest.TestCase):
    def test_feature_guarded_query_coverage(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler, 'Host C++ compiler required')
        profiles = {
            'minimal': [],
            'nrf52_gps_sd': ['NRF52_PLATFORM', 'ENV_INCLUDE_GPS', 'OTA_SD_STORE', 'NRF52_POWER_MANAGEMENT'],
            'esp32_web': ['ESP_PLATFORM', 'ESP32_PLATFORM', 'ADMIN_PASSWORD', 'MESH_USB_LOGGING_AVAILABLE'],
            'esp32_mqtt': ['ESP_PLATFORM', 'ESP32_PLATFORM', 'WITH_MQTT_BRIDGE', 'WITH_BRIDGE',
                           'WITH_MQTT_NEIGHBORS', 'MESH_USB_LOGGING_AVAILABLE'],
            'esp32_mqtt_espnow': ['ESP_PLATFORM', 'ESP32_PLATFORM', 'WITH_MQTT_BRIDGE',
                                   'WITH_ESPNOW_BRIDGE', 'WITH_BRIDGE'],
            'rs232_gps': ['WITH_BRIDGE', 'WITH_RS232_BRIDGE', 'ENV_INCLUDE_GPS'],
            'espnow': ['ESP_PLATFORM', 'ESP32_PLATFORM', 'WITH_BRIDGE', 'WITH_ESPNOW_BRIDGE', 'MESH_PRIMARY_ESPNOW'],
            'lr2021': ['USE_LR2021'],
        }
        for path, prefix, exceptions in (
            (COMMON, 'void CommonCLI::handle', COMMON_QUERY_EXCEPTIONS),
            (OBSERVER, 'bool CommonCLI::handleObserver', OBSERVER_QUERY_EXCEPTIONS),
        ):
            original = '\n'.join(methods(path.read_text(encoding='utf-8'), prefix))
            for profile, macros in profiles.items():
                with self.subTest(parser=path.name, profile=profile):
                    # Preprocess the production bodies so a setter behind a
                    # different feature guard cannot satisfy a visible getter.
                    result = subprocess.run(
                        [compiler, '-E', '-P', '-x', 'c++', *[f'-D{key}=1' for key in macros], '-'],
                        input=original, text=True, capture_output=True, check=True)
                    setter, getter = methods(result.stdout, prefix)
                    sets, gets = literal_keys(setter), literal_keys(getter)
                    gets = {'flood.retry.bucket' if k == 'flood.retry.bucket.' else k for k in gets}
                    self.assertFalse(gets - sets - set(exceptions))
                    if path == COMMON:
                        self.assertIn('path.hash.mode', sets)

    def test_common_and_observer_query_coverage(self):
        for path, prefix, exceptions in (
            (COMMON, 'void CommonCLI::handle', COMMON_QUERY_EXCEPTIONS),
            (OBSERVER, 'bool CommonCLI::handleObserver', OBSERVER_QUERY_EXCEPTIONS),
        ):
            with self.subTest(parser=path.name):
                setter, getter = methods(path.read_text(encoding='utf-8'), prefix)
                sets, gets = literal_keys(setter), literal_keys(getter)
                # The query embeds the bucket index in the key; the setter
                # accepts it as a separate argument.
                gets = {'flood.retry.bucket' if k == 'flood.retry.bucket.' else k for k in gets}
                self.assertEqual(gets - sets, set(exceptions),
                                 f'{path.name}: missing setter or undocumented read-only query')

    def test_mqtt_slot_query_coverage(self):
        setter, getter = methods(OBSERVER.read_text(encoding='utf-8'), 'bool CommonCLI::handleObserver')
        sets = literal_keys(setter, 'subcmd', False)
        gets = literal_keys(getter, 'subcmd', False)
        self.assertGreater(len(sets), 5, 'MQTT slot parser moved; update the audit')
        self.assertEqual(gets - sets, {'diag'})

    def test_every_migrated_radio_setter_is_reachable_in_infrastructure(self):
        common_set, common_get = methods(COMMON.read_text(encoding='utf-8'), 'void CommonCLI::handle')
        shared = extract_braced(RADIO.read_text(encoding='utf-8'), 'bool CommonRadioPrefs::handleCommand(')
        migrated = set(re.findall(r'startsWith\(command, "set ([\w.]+) ', shared))
        self.assertEqual(len(migrated), 13, 'Review changes to the shared radio command surface')
        self.assertFalse(migrated - literal_keys(common_set))
        self.assertFalse(migrated - literal_keys(common_get))

    def test_split_parser_delegates_are_wired(self):
        common = COMMON.read_text(encoding='utf-8')
        setter, getter = methods(common, 'void CommonCLI::handle')
        self.assertIn('handleObserverSetCmd(sender_timestamp, config, reply)', setter)
        self.assertIn('handleObserverGetCmd(sender_timestamp, config, reply)', getter)
        for role, filename in (('simple_repeater', 'MyMesh.cpp'),
                               ('simple_room_server', 'MyMesh.cpp'),
                               ('simple_sensor', 'SensorMesh.cpp')):
            text = (ROOT/'examples'/role/filename).read_text(encoding='utf-8')
            self.assertIn('_cli.handleCommand(sender_timestamp, command, reply)', text)
        for verb in ('get', 'set'):
            block = extract_braced(common, f'if (memcmp(command, "{verb} ", 4) == 0)')
            self.assertIn(f'handle{verb.title()}Cmd(sender_timestamp, command, reply)', block)

    def test_companion_allowlist_is_backed_by_shared_parser(self):
        text = (ROOT/'examples/companion_radio/MyMesh.cpp').read_text(encoding='utf-8')
        allowlist = extract_braced(text, 'static bool isCompanionRadioPrefsCommand(')
        shared = extract_braced(RADIO.read_text(encoding='utf-8'), 'bool CommonRadioPrefs::handleCommand(')
        for verb, key in re.findall(r'"(get|set) ([\w.]+) ?"', allowlist):
            self.assertIn(f'"{verb} {key}' + (' ' if verb == 'set' else '') + '"', shared)
        handler = extract_braced(text, 'bool MyMesh::handleCommand(')
        self.assertIn('_prefs.getRadioPrefs()->handleCommand(', handler)
        self.assertIn('_prefs.getRadioPrefs()->isDirty()', handler)
        self.assertIn('savePrefs()', handler)

    def test_role_query_coverage(self):
        common_set, _ = methods(COMMON.read_text(encoding='utf-8'), 'void CommonCLI::handle')
        common_keys = literal_keys(common_set)
        for role, filename, exceptions in (
            ('simple_repeater', 'MyMesh.cpp', {
                'acl',                    # ACL list, changed with ACL commands
                'battery.alert.region',   # set battery.alert on <region>
                'clock.sync',             # clock-sync delegate
                'host',                   # Runtime host configuration view
                'recent.repeaters',       # Discovery/cache query
            }),
            ('simple_room_server', 'MyMesh.cpp', {'acl'}),
            ('simple_sensor', 'SensorMesh.cpp', {'acl'}),
        ):
            text = (ROOT/'examples'/role/filename).read_text(encoding='utf-8')
            sets, gets = full_command_keys(text)
            with self.subTest(role=role):
                self.assertEqual(gets - sets - common_keys, exceptions)

        companion = (ROOT/'examples/companion_radio/MyMesh.cpp').read_text(encoding='utf-8')
        sets, gets = full_command_keys(companion)
        terminal = extract_braced(companion, 'void MyMesh::handleTerminalCommand(')
        terminal_set = extract_braced(terminal, 'if (strncmp(command, "set ", 4) == 0)')
        sets |= literal_keys(terminal_set)
        shared = extract_braced(companion, 'static bool isCompanionRadioPrefsCommand(')
        sets |= {k for verb, k in re.findall(r'"(get|set) ([\w.]+) ?"', shared) if verb == 'set'}
        self.assertEqual(gets - sets, {
            'contact.cache', 'contact.cache.timing',  # Contact-storage diagnostics
            'display.wifi',                          # Connection/display status
            'mqtt', 'mqtt.running',                  # MQTT connection status
            'password', 'prv.key',                   # Local secret/identity reads
            'pwrmgt.bootreason', 'role',             # Boot/build facts
            'radio.rxps.config',                     # Detail view of radio.rxps
            'wifi.status', 'wifi.ip',                # Live WiFi link state
        })

    def test_literal_key_comparison_lengths(self):
        for path in (COMMON, OBSERVER):
            text = path.read_text(encoding='utf-8')
            for match in re.finditer(r'(?:memcmp|strncmp)\((?:config|subcmd),\s*"([^"\n]*)",\s*(\d+)\)', text):
                key, length = match[1], int(match[2])
                with self.subTest(file=path.name, key=key):
                    # Some exact getters compare the terminating NUL too.
                    self.assertIn(length, (len(key), len(key) + 1))
                    if key.endswith(' '):
                        self.assertEqual(length, len(key), 'Setter must not compare the value against NUL')

    def test_native_radio_setting_round_trips(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler, 'Host C++ compiler required')
        source = COMMON.read_text(encoding='utf-8')
        setter, getter = methods(source, 'void CommonCLI::handle')
        keys = ['radio', 'freq', 'af', 'dutycycle', 'int.thresh', 'cad', 'radio.rxgain',
                'tx', 'rxdelay', 'agc.reset.interval', 'multi.acks', 'txdelay', 'direct.txdelay',
                'flood.max', 'flood.max.advert', 'flood.max.unscoped']
        # Preserve the actual conditions and bodies. The only excluded branches
        # are unrelated settings needing board-specific dependencies.
        set_blocks = [extract_braced(setter, 'if (strncmp(config, "path.hash.mode", 14)')]
        get_blocks = [extract_braced(getter, 'if (configKeyEquals(config, "path.hash.mode"))')]
        set_blocks.append(extract_braced(setter, 'if (strcmp(config, "extra.sf") == 0 ||'))
        get_blocks.append(extract_braced(getter, 'if (strcmp(config, "extra.sf") == 0)'))
        for key in keys:
            block = extract_braced(setter, f'if (memcmp(config, "{key} ", {len(key)+1}) == 0)')
            # A feature guard immediately before this closing brace controls
            # the following else-if arm, which is not part of this fixture.
            block = re.sub(r'\n#if[^\n]*\n\s*}$', '\n}', block)
            set_blocks.append(block)
            get_blocks.append(extract_braced(getter, f'if (configKeyEquals(config, "{key}"))'))
        dispatch = ' else '.join(extract_braced(source, f'if (memcmp(command, "{verb} ", 4) == 0)')
                                 for verb in ('get', 'set'))
        fixture = (ROOT/'test/fixtures/cli_settings/main.cpp').read_text(encoding='utf-8')
        fixture = fixture.replace('@SET@', ' else '.join(set_blocks))
        fixture = fixture.replace('@GET@', ' else '.join(get_blocks))
        fixture = fixture.replace('@DISPATCH@', dispatch)
        fixture = fixture.replace('@KEY_EQUALS@', extract_braced(source, 'static bool configKeyEquals('))
        with tempfile.TemporaryDirectory() as directory:
            cpp, exe = Path(directory)/'test.cpp', Path(directory)/'test.exe'
            cpp.write_text(fixture, encoding='utf-8')
            for macros in ([], ['-DUSE_LR2021=1']):
                with self.subTest(macros=macros):
                    subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', *macros,
                                    '-I', str(ROOT/'src'), str(cpp), '-o', str(exe)], check=True)
                    subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
