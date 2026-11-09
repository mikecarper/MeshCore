"""Verify the pinned framework fallback without downloading release assets."""
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'esp32_core_fallback', ROOT / 'scripts/fetch_pinned_esp32_core.py')
FETCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FETCH)


class VerifiedDownloadTests(unittest.TestCase):
    def run_download(self, payload, *, size=None, digest=None):
        content = b'test release asset'
        observed = []

        def opener(request, timeout):
            observed.append((request.full_url, request.get_header('Accept'), timeout))
            if isinstance(payload, Exception):
                raise payload
            return BytesIO(payload)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'asset.tar.xz'
            options = dict(opener=opener, expected_size=len(content) if size is None else size,
                           expected_sha256=hashlib.sha256(content).hexdigest() if digest is None else digest)
            if isinstance(payload, Exception) or payload != content or size is not None or digest is not None:
                with self.assertRaises(Exception):
                    FETCH.download_verified(path, **options)
                self.assertFalse(path.exists())
            else:
                FETCH.download_verified(path, **options)
                self.assertEqual(path.read_bytes(), content)
            self.assertEqual(observed, [(FETCH.ASSET_URL, 'application/octet-stream', 60)])

    def test_matching_archive_is_retained(self):
        self.run_download(b'test release asset')

    def test_wrong_hash_is_removed(self):
        self.run_download(b'test release asset', digest='0' * 64)

    def test_short_and_oversized_archive_are_removed(self):
        self.run_download(b'too short')
        self.run_download(b'test release asset with extra bytes')

    def test_download_failure_leaves_no_partial_file(self):
        self.run_download(OSError('signed URL must not be logged'))

    def test_existing_file_is_not_removed_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.tar.xz'
            path.write_bytes(b'existing')
            with self.assertRaises(FileExistsError):
                FETCH.download_verified(path, opener=lambda *_args, **_kw: BytesIO(b'other'))
            self.assertEqual(path.read_bytes(), b'existing')


class InstalledPackageBindingTests(unittest.TestCase):
    def fixture(self, root):
        archive = root / 'asset with spaces.tar.xz'
        archive.write_bytes(b'verified archive')
        package = root / 'packages' / 'framework'
        package.mkdir(parents=True)
        metadata = {'type': 'tool', 'name': FETCH.PACKAGE_NAME, 'version': FETCH.PACKAGE_VERSION,
                    'spec': {'name': 'asset', 'uri': 'file://' + str(archive.resolve()),
                             'owner': None, 'id': None, 'requirements': None}}
        (package / '.piopm').write_text(json.dumps(metadata))
        (package / 'package.json').write_text(json.dumps({'name': FETCH.PACKAGE_NAME,
                                                          'version': FETCH.PACKAGE_VERSION}))
        options = dict(packages_dir=root / 'packages', expected_size=archive.stat().st_size,
                       expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        return archive, package, metadata, options

    def test_verified_install_binds_only_canonical_external_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, package, before, options = self.fixture(Path(directory))
            FETCH.bind_installed_package(archive, **options)
            actual = json.loads((package / '.piopm').read_text())
            expected = dict(before, spec=dict(before['spec'], name='arduino-esp32', uri=FETCH.CANONICAL_URL))
            self.assertEqual(actual, expected)
            self.assertFalse(list(package.glob('.piopm-*')))

    def test_unverified_archive_cannot_rebind(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, package, before, options = self.fixture(Path(directory))
            options['expected_sha256'] = '0' * 64
            with self.assertRaises(ValueError):
                FETCH.bind_installed_package(archive, **options)
            self.assertEqual(json.loads((package / '.piopm').read_text()), before)

    def test_wrong_package_or_ambiguous_install_cannot_rebind(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, package, before, options = self.fixture(Path(directory))
            (package / 'package.json').write_text(json.dumps({'name': FETCH.PACKAGE_NAME,
                                                              'version': 'wrong'}))
            with self.assertRaises(ValueError):
                FETCH.bind_installed_package(archive, **options)
            self.assertEqual(json.loads((package / '.piopm').read_text()), before)
            (package / 'package.json').write_text(json.dumps({'name': FETCH.PACKAGE_NAME,
                                                              'version': FETCH.PACKAGE_VERSION}))
            duplicate = package.parent / 'duplicate'
            duplicate.mkdir()
            (duplicate / '.piopm').write_text(json.dumps(before))
            (duplicate / 'package.json').write_text((package / 'package.json').read_text())
            with self.assertRaises(ValueError):
                FETCH.bind_installed_package(archive, **options)
            self.assertEqual(json.loads((package / '.piopm').read_text()), before)

    def test_different_file_uri_cannot_rebind(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, package, before, options = self.fixture(Path(directory))
            before['spec']['uri'] = 'file:///other/archive.tar.xz'
            (package / '.piopm').write_text(json.dumps(before))
            with self.assertRaises(ValueError):
                FETCH.bind_installed_package(archive, **options)
            self.assertEqual(json.loads((package / '.piopm').read_text()), before)


class FrameworkPreflightTests(unittest.TestCase):
    def test_real_retry_helper_reports_only_exhausted_transient_downloads(self):
        script = r'''
source "$1/build_legacy.sh"
sleep() { :; }
pio() {
  case "$2" in
    transient) echo "PackageException: Got the unrecognized status code '504' when downloaded https://example.invalid"; return 1 ;;
    exact) echo "PackageException: Got the unrecognized status code '504' when downloaded https://github.com/espressif/arduino-esp32/releases/download/3.3.11/esp32-core-3.3.11-libs.tar.xz"; return 1 ;;
    permanent) echo 'PackageException: checksum mismatch'; return 7 ;;
    success) return 0 ;;
  esac
}
for mode in transient exact permanent success; do
  if run_pio_with_size_detection test "$mode" >/dev/null 2>&1; then status=0; else status=$?; fi
  printf '%s:%s:%s:%s\n' "$mode" "$status" "$MESHCORE_PIO_TRANSIENT_EXHAUSTED" "$MESHCORE_PIO_ESP32_3311_LIBS_EXHAUSTED"
done
'''
        result = subprocess.run(['bash', '-c', script, 'retry-state', str(ROOT)], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['transient:1:1:0', 'exact:1:1:1', 'permanent:7:0:0', 'success:0:0:0'])

    def run_preflight(self, scenario):
        with tempfile.TemporaryDirectory() as directory:
            script = r'''
source "$1/build_legacy.sh"
scenario=$3
export TMPDIR="$2/tmp" PLATFORMIO_CORE_DIR="$2/pio"
mkdir -p "$TMPDIR" "$PLATFORMIO_CORE_DIR/packages/framework-arduinoespressif32"
printf '{"version":"3.3.11"}' > "$PLATFORMIO_CORE_DIR/packages/framework-arduinoespressif32/package.json"
requires_esp32_arduino3_framework() { [ "$scenario" != unrelated ]; }
run_pio_with_size_detection() {
  MESHCORE_PIO_TRANSIENT_EXHAUSTED=0
  MESHCORE_PIO_ESP32_3311_LIBS_EXHAUSTED=0
  printf 'PIO:%s\n' "$*"
  case "$*" in
    *https://github.com/*-libs.tar.xz*)
      if [ "$scenario" = permanent ]; then return 7; fi
      if [ "$scenario" = foreign_transient ]; then MESHCORE_PIO_TRANSIENT_EXHAUSTED=1; return 1; fi
      if [ "$scenario" = transient ] || [ "$scenario" = fetch_fail ] || [ "$scenario" = install_fail ] || [ "$scenario" = bind_fail ]; then
        MESHCORE_PIO_TRANSIENT_EXHAUSTED=1; MESHCORE_PIO_ESP32_3311_LIBS_EXHAUSTED=1; return 1
      fi ;;
    *file://*) [ "$scenario" != install_fail ] || return 9 ;;
  esac
  return 0
}
python3() {
  printf 'PY:%s\n' "$2"
  if [ "$2" = fetch ]; then
    [ "$scenario" != fetch_fail ] || return 8
    printf 'archive' > "$3"
  elif [ "$2" = bind ]; then
    [ "$scenario" != bind_fail ] || return 10
  fi
  return 0
}
prepare_esp32_arduino3_framework target env
status=$?
if find "$TMPDIR" -mindepth 1 -print -quit | grep -q .; then echo TEMP_LEAK; fi
exit "$status"
'''
            return subprocess.run(['bash', '-c', script, 'preflight', str(ROOT), directory, scenario],
                                  cwd=ROOT, capture_output=True, text=True, timeout=20)

    def test_only_transient_exhaustion_uses_verified_file_and_cleans_up(self):
        result = self.run_preflight('transient')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PY:fetch', result.stdout)
        self.assertIn('PY:bind', result.stdout)
        self.assertIn('PIO:pkg install --global --tool file://', result.stdout)
        self.assertIn('PIO:pkg install -e env', result.stdout)
        self.assertNotIn('TEMP_LEAK', result.stdout)

    def test_failures_stop_without_leaving_temp(self):
        for scenario, code in (('permanent', 7), ('foreign_transient', 1), ('fetch_fail', 8),
                               ('install_fail', 9), ('bind_fail', 10)):
            with self.subTest(scenario=scenario):
                result = self.run_preflight(scenario)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertNotIn('PIO:pkg install -e env', result.stdout)
                self.assertNotIn('TEMP_LEAK', result.stdout)
                if scenario in ('permanent', 'foreign_transient'):
                    self.assertNotIn('PY:fetch', result.stdout)

    def test_other_frameworks_never_use_fallback(self):
        result = self.run_preflight('unrelated')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('PY:', result.stdout)
        self.assertNotIn('PIO:', result.stdout)


class C6RetryTests(unittest.TestCase):
    def run_install(self, kind, scenario):
        with tempfile.TemporaryDirectory() as directory:
            script = r'''
source "$1/build_legacy.sh"
kind=$3
scenario=$4
export TMPDIR="$2/tmp with spaces"
mkdir -p "$TMPDIR"
python3() {
  printf 'PY:%s:%s\n' "$2" "$3"
  if [ "$2" = fetch ]; then
    [ "$scenario" != fetch_fail ] || return 8
    printf 'archive' > "$4"
  elif [ "$2" = bind ]; then
    [ "$scenario" != bind_fail ] || return 10
  fi
}
pio() {
  printf 'PIO:%s\n' "$*"
  [ "$scenario" != install_fail ] || return 9
}
install_esp32_c6_pinned_fallback "$kind"
status=$?
if find "$TMPDIR" -mindepth 1 -print -quit | grep -q .; then echo TEMP_LEAK; fi
exit "$status"
'''
            return subprocess.run(['bash', '-c', script, 'c6-install', str(ROOT), directory, kind, scenario],
                                  cwd=ROOT, capture_output=True, text=True, timeout=20)

    def test_installer_passes_exact_package_type_and_cleans_up(self):
        for kind, package_type in (('platform', '--platform'), ('libs', '--tool')):
            with self.subTest(kind=kind):
                result = self.run_install(kind, 'success')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f'PY:fetch:{kind}', result.stdout)
                self.assertIn(f'PY:bind:{kind}', result.stdout)
                self.assertIn(f'PIO:pkg install --global --skip-dependencies {package_type} file://', result.stdout)
                self.assertNotIn('TEMP_LEAK', result.stdout)

    def test_installer_stops_on_every_failure_and_cleans_up(self):
        for scenario, code in (('fetch_fail', 8), ('install_fail', 9), ('bind_fail', 10)):
            with self.subTest(scenario=scenario):
                result = self.run_install('libs', scenario)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertNotIn('TEMP_LEAK', result.stdout)
                if scenario == 'fetch_fail':
                    self.assertNotIn('PIO:', result.stdout)
                if scenario in ('fetch_fail', 'install_fail'):
                    self.assertNotIn('PY:bind', result.stdout)

    def run_scenario(self, scenario):
        with tempfile.TemporaryDirectory() as directory:
            script = r'''
source "$1/build_legacy.sh"
scenario=$3
state_dir=$2
platform_url='https://github.com/pioarduino/platform-espressif32/releases/download/53.03.13-1/platform-espressif32.zip'
libs_url='https://github.com/espressif/esp32-arduino-lib-builder/releases/download/idf-release_v5.3/esp32-arduino-libs-idf-release_v5.3-489d7a2b-v1.zip'
sleep() { :; }
pio() {
  echo call >> "$state_dir/calls"
  if [ "$scenario" = permanent ]; then echo 'compiler error'; return 7; fi
  if [ "$scenario" = foreign ]; then url='https://example.invalid/other.zip'
  elif [ "$scenario" = suffix ]; then url="${platform_url}.untrusted"
  elif [ "$scenario" = repeat ] || [ "$scenario" = fallback_error ] || [ ! -f "$state_dir/platform" ]; then url=$platform_url
  elif [ ! -f "$state_dir/libs" ]; then url=$libs_url
  else return 0
  fi
  echo "PackageException: Got the unrecognized status code '504' when downloaded $url"
  return 1
}
install_esp32_c6_pinned_fallback() {
  printf 'FALLBACK:%s\n' "$1"
  if [ "$scenario" = fallback_error ]; then return 9; fi
  touch "$state_dir/$1"
}
if run_pio_with_size_detection run -e c6; then status=0; else status=$?; fi
printf 'CALLS:%s\n' "$(wc -l < "$state_dir/calls")"
exit "$status"
'''
            return subprocess.run(['bash', '-c', script, 'c6-retry', str(ROOT), directory, scenario],
                                  cwd=ROOT, capture_output=True, text=True, timeout=20)

    def test_platform_then_libs_recover_once_each(self):
        result = self.run_scenario('both')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count('FALLBACK:platform'), 1)
        self.assertEqual(result.stdout.count('FALLBACK:libs'), 1)
        self.assertIn('CALLS:9', result.stdout)

    def test_repeated_asset_and_unrelated_errors_remain_bounded(self):
        for scenario, code, calls, fallbacks in (('repeat', 1, 8, 1), ('foreign', 1, 4, 0),
                                                ('suffix', 1, 4, 0), ('permanent', 7, 1, 0),
                                                ('fallback_error', 9, 4, 1)):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertIn(f'CALLS:{calls}', result.stdout)
                self.assertEqual(result.stdout.count('FALLBACK:'), fallbacks)

if __name__ == '__main__':
    unittest.main()
