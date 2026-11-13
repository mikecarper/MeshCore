import copy
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'mesh-america/reconcile-guarded-combo-release.py'
spec = importlib.util.spec_from_file_location('combo_reconcile', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    tag = 'lora-ota-' + module.FAMILY
    stem = 'Heltec_t1_repeater_lora_ota_no_external_sensors-with-bootloader-2.4.12-full-ota'
    old = stem + module.OLD_SUFFIX
    new = stem + module.NEW_SUFFIX
    base = module.REPOSITORY + '/releases/download/' + tag + '/'
    record = {'notes': 'CAPABILITIES - ' + base + old + '.capabilities.json\nMEMORY - ' + base + old + '.memory.json\nSOURCE - ' + module.REPOSITORY + '/tree/' + module.OLD_SOURCE,
              'files': [{'name': old + ext, 'url': base + old + ext} for ext in ('.zip', '.uf2')]}
    provider = {'device': [{'type': 'nrf52', 'firmware': [{'version': {module.FAMILY: record}}]}]}
    assets = [{'name': new + ext, 'browser_download_url': base + new + ext, 'state': 'uploaded', 'size': 17, 'digest': 'sha256:' + 'a' * 64}
              for ext in ('.zip', '.uf2', '.capabilities.json', '.memory.json')]
    return provider, {tag: {'tag_name': tag, 'draft': False, 'assets': assets}}


class ReconcileTest(unittest.TestCase):
    def test_exact_verified_assets_update_without_changing_release_family(self):
        provider, releases = fixture(); original = copy.deepcopy(provider)
        updated, count = module.reconcile(provider, releases)
        self.assertEqual(provider, original)
        self.assertEqual(count, 2)
        record = updated['device'][0]['firmware'][0]['version'][module.FAMILY]
        self.assertTrue(all(module.NEW_SUFFIX in item['name'] and item['sha256'] == 'a' * 64 for item in record['files']))
        self.assertIn(module.NEW_SOURCE, record['notes'])
        self.assertNotIn(module.OLD_SOURCE, record['notes'])
        self.assertIn(module.NEW_SUFFIX + '.capabilities.json', record['notes'])
        self.assertIn(module.NEW_SUFFIX + '.memory.json', record['notes'])

    def test_missing_ambiguous_unverified_and_wrong_release_fail_closed(self):
        for mutation in ('missing', 'duplicate', 'digest', 'url', 'wrong-tag', 'report', 'source'):
            with self.subTest(mutation=mutation):
                provider, releases = fixture(); tag = next(iter(releases)); assets = releases[tag]['assets']
                if mutation == 'missing': assets.pop(0)
                if mutation == 'duplicate': assets.append(copy.deepcopy(assets[0]))
                if mutation == 'digest': assets[0]['digest'] = 'sha256:bad'
                if mutation == 'url': assets[0]['browser_download_url'] = 'https://example.com/firmware.zip'
                if mutation == 'wrong-tag': releases[tag]['tag_name'] = 'different'
                if mutation == 'report': assets.pop()
                if mutation == 'source': provider['device'][0]['firmware'][0]['version'][module.FAMILY]['notes'] = 'wrong source'
                original = copy.deepcopy(provider)
                with self.assertRaises(ValueError): module.reconcile(provider, releases)
                self.assertEqual(provider, original)


if __name__ == '__main__': unittest.main()
