"""Exercise real build helpers with a fake PlatformIO, without running PIO."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PlatformIORetryTests(unittest.TestCase):
    def run_scenario(self, scenario, wrapper=False, command='run'):
        with tempfile.TemporaryDirectory() as temp:
            script = r'''
source "$1/build_legacy.sh"
attempt_file="$2/attempts"
scenario=$3
pio() {
  local count=0
  [ ! -f "$attempt_file" ] || read -r count < "$attempt_file"
  count=$((count + 1))
  printf '%s\n' "$count" > "$attempt_file"
  printf 'ARGS:%s\n' "$*"
  case "$scenario:$count" in
    recovery:1|exhaust:*) echo "PackageException: Got the unrecognized status code '504' when downloaded https://example.invalid/archive.zip"; return 1 ;;
    http*:1) echo "PackageException: Got the unrecognized status code '${scenario#http}' when downloaded https://example.invalid/archive.zip"; return 1 ;;
    compile:*) echo 'error: compilation failed (HTTP 504 in source)'; return 7 ;;
    checksum:*) echo 'PackageException: checksum mismatch'; return 5 ;;
    size:*) echo 'region FLASH overflowed by 123 bytes'; return 1 ;;
  esac
  return 0
}
sleep() { printf 'SLEEP:%s\n' "$1"; }
set -e -o pipefail
if [ "$4" = wrapper ]; then
  export attempt_file scenario
  export -f pio sleep
  bash "$1/tools/ci/pio_retry.sh" "$5" -e test-board
else
  run_pio_with_size_detection "$5" -e test-board
fi
status=$?
printf 'STATUS:%s\n' "$status"
exit "$status"
'''
            result = subprocess.run(['bash', '-c', script, 'retry-test',
                                     str(ROOT), temp, scenario,
                                     'wrapper' if wrapper else 'direct', command], cwd=ROOT,
                                    capture_output=True, text=True, timeout=20)
            attempts = Path(temp, 'attempts').read_text().strip()
            return result, int(attempts)

    def test_transient_download_is_retried_and_success_is_returned(self):
        result, attempts = self.run_scenario('recovery')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(attempts, 2)
        self.assertEqual(result.stdout.count('ARGS:run -e test-board'), 2)
        self.assertIn('SLEEP:5', result.stdout)
        wrapper_result, wrapper_attempts = self.run_scenario('recovery', wrapper=True)
        self.assertEqual(wrapper_result.returncode, 0, wrapper_result.stdout + wrapper_result.stderr)
        self.assertEqual(wrapper_attempts, 2)

    def test_native_test_command_retries_download_only(self):
        result, attempts = self.run_scenario('recovery', wrapper=True, command='test')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(attempts, 2)
        self.assertEqual(result.stdout.count('ARGS:test -e test-board'), 2)
        result, attempts = self.run_scenario('compile', wrapper=True, command='test')
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(attempts, 1)

    def test_all_transient_http_codes_and_permanent_404(self):
        for status in (408, 429, 500, 502, 503, 504, 404):
            with self.subTest(status=status):
                result, attempts = self.run_scenario(f'http{status}')
                self.assertEqual(attempts, 1 if status == 404 else 2)
                self.assertEqual(result.returncode, 1 if status == 404 else 0)

    def test_retries_are_bounded_and_other_failures_are_not_retried(self):
        for scenario, status, attempts, sleeps in [
            ('exhaust', 1, 4, 3), ('compile', 7, 1, 0),
            ('checksum', 5, 1, 0), ('size', 42, 1, 0),
        ]:
            with self.subTest(scenario=scenario):
                result, actual_attempts = self.run_scenario(scenario)
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertEqual(actual_attempts, attempts)
                self.assertEqual(result.stdout.count('SLEEP:'), sleeps)

    def test_framework_setup_stops_when_core_install_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            script = r'''
source "$1/build_legacy.sh"
export PLATFORMIO_CORE_DIR="$2"
requires_esp32_arduino3_framework() { return 0; }
pio() { printf 'UNINSTALL:%s\n' "$*"; return 0; }
run_pio_with_size_detection() { printf 'INSTALL:%s\n' "$*"; return 7; }
prepare_esp32_arduino3_framework test-board test-board
'''
            result = subprocess.run(['bash', '-c', script, 'framework-test',
                                     str(ROOT), temp], cwd=ROOT,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
            self.assertEqual(sum(line.startswith('INSTALL:')
                                 for line in result.stdout.splitlines()), 1)
            self.assertNotIn('esp32-core-3.3.11-libs', result.stdout)


if __name__ == '__main__':
    unittest.main()
