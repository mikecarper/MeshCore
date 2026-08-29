#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source build.sh

fail() {
  echo "test_build_background: $*" >&2
  exit 1
}

test_dir=$(mktemp -d)
trap 'rm -rf -- "$test_dir"' EXIT

# The option may appear alongside the ordinary build options and must not leak
# into the positional command that the detached worker receives.
parse_cli_options \
  build-firmware RAK_4631_repeater \
  --firmware-version v9.8.7-test \
  --radio-preset target \
  --background \
  --resume
[ "$BUILD_BACKGROUND_REQUESTED" = "1" ] || fail "--background was ignored"
[ "$BUILD_BACKGROUND_EXPLICIT" = "1" ] || fail "explicit background mode was not recorded"
[ "${PARSED_COMMAND_ARGS[*]}" = "build-firmware RAK_4631_repeater" ] \
  || fail "background option leaked into positional arguments"

BUILD_BACKGROUND_REQUESTED=0
prompt_for_background_build_mode <<< "2" >/dev/null
[ "$BUILD_BACKGROUND_REQUESTED" = "1" ] \
  || fail "interactive background selection was ignored"

# Capture systemd-run as a shell function: this validates argv/environment
# serialization without starting a service or invoking PlatformIO.
capture_file="${test_dir}/systemd-run.args"
mock_systemd_exit=0
mock_systemd_state=running
background_systemd_available() { return 0; }
systemd-run() {
  local argument job_id="" job_dir=""
  printf '%s\n' "$@" > "$capture_file"
  if [ "$mock_systemd_exit" -ne 0 ]; then
    return "$mock_systemd_exit"
  fi
  for argument in "$@"; do
    case "$argument" in
      BUILD_BACKGROUND_JOB_ID=*) job_id=${argument#*=} ;;
      BUILD_BACKGROUND_DIR=*) job_dir=${argument#*=} ;;
    esac
  done
  [ -n "$job_id" ] || return 1
  [ -n "$job_dir" ] || return 1
  mkdir -p -- "$job_dir"
  printf 'job=%s\nstate=%s\n' \
    "$job_id" "$mock_systemd_state" > "${job_dir}/${job_id}.status"
}

BUILD_BACKGROUND_ACTIVE=0
BUILD_BACKGROUND_DIR="${test_dir}/job records"
OUTPUT_DIR="${test_dir}/firmware output"
unset DISABLE_DEBUG
PLATFORMIO_BUILD_FLAGS="-D BACKGROUND_OVERRIDE_TEST=1"
PLATFORMIO_BUILD_UNFLAGS="-D FOREGROUND_ONLY_FLAG"
PLATFORMIO_BUILD_SRC_FILTER="+<custom source with spaces/**>"
PLATFORMIO_EXTRA_SCRIPTS="pre:custom script.py"
FIRMWARE_VERSION="v9.8.7 custom"
BUILD_PROFILE_OVERRIDE="standard"
BUILD_PROFILE_EXPLICIT=1
FIRMWARE_PROFILE_OVERRIDE=""
MESHDEBUG_OVERRIDE="on"
PACKET_LOGGING_OVERRIDE="off"
MQTT_BRIDGE_OVERRIDE="on"
MQTT_DEBUG_OVERRIDE="off"
RADIO_SETTING_TITLE="Custom field test"
RADIO_FREQ_OVERRIDE="910.125"
RADIO_BW_OVERRIDE="125"
RADIO_SF_OVERRIDE="8"
RADIO_CR_OVERRIDE="6"
RESUME_BUILD_OUTPUT=1
KISS_MODE_OVERRIDE="skip"

launch_output=$(launch_background_build \
  build-firmware RAK_4631_repeater)

grep -Fxq -- 'BUILD_BACKGROUND_ACTIVE=1' "$capture_file" \
  || fail "worker recursion guard was not forwarded"
grep -Fxq -- 'BUILD_BACKGROUND_CONFIG_RESOLVED=1' "$capture_file" \
  || fail "resolved-config marker was not forwarded"
grep -Fxq -- 'RADIO_SETTING_TITLE=Custom field test' "$capture_file" \
  || fail "custom radio title was not preserved"
grep -Fxq -- 'RADIO_FREQ_OVERRIDE=910.125' "$capture_file" \
  || fail "custom frequency was not preserved"
grep -Fxq -- 'FIRMWARE_PROFILE_OVERRIDE=' "$capture_file" \
  || fail "empty target-default profile was not preserved"
grep -Fxq -- 'MESHDEBUG_OVERRIDE=on' "$capture_file" \
  || fail "debug choice was not preserved"
grep -Fxq -- 'MQTT_BRIDGE_OVERRIDE=on' "$capture_file" \
  || fail "MQTT choice was not preserved"
grep -Fxq -- 'FIRMWARE_VERSION=v9.8.7 custom' "$capture_file" \
  || fail "firmware version was not preserved"
grep -Fxq -- 'RESUME_BUILD_OUTPUT=1' "$capture_file" \
  || fail "output policy was not preserved"
grep -Fxq -- "--working-directory=$PWD" "$capture_file" \
  || fail "working directory was not preserved"
grep -Fxq -- '--collect' "$capture_file" \
  || fail "completed transient unit would not be collected"
grep -Fxq -- '-i' "$capture_file" \
  || fail "worker environment was not cleared before explicit forwarding"
if grep -Eq '^(--setenv=)?DISABLE_DEBUG=' "$capture_file"; then
  fail "unset DISABLE_DEBUG leaked into detached worker"
fi
grep -Fxq -- 'PLATFORMIO_BUILD_FLAGS=-D BACKGROUND_OVERRIDE_TEST=1' "$capture_file" \
  || fail "caller PlatformIO build flags were not preserved"
grep -Fxq -- 'PLATFORMIO_BUILD_UNFLAGS=-D FOREGROUND_ONLY_FLAG' "$capture_file" \
  || fail "caller PlatformIO build unflags were not preserved"
grep -Fxq -- 'PLATFORMIO_BUILD_SRC_FILTER=+<custom source with spaces/**>' "$capture_file" \
  || fail "caller PlatformIO source filter was not preserved"
grep -Fxq -- 'PLATFORMIO_EXTRA_SCRIPTS=pre:custom script.py' "$capture_file" \
  || fail "caller PlatformIO extra scripts were not preserved"
[ "$(tail -n 2 "$capture_file" | head -n 1)" = "build-firmware" ] \
  || fail "detached command was not forwarded"
[ "$(tail -n 1 "$capture_file")" = "RAK_4631_repeater" ] \
  || fail "detached target was not forwarded"
[[ "$launch_output" == *"systemctl --user status meshcore-build-"* ]] \
  || fail "launch output omitted the persistent status command"
[[ "$launch_output" == *"Build log:"* ]] \
  || fail "launch output omitted the durable log path"
[ -s "${BUILD_BACKGROUND_DIR}/latest-job" ] \
  || fail "latest background job was not recorded"
successful_job=$(<"${BUILD_BACKGROUND_DIR}/latest-job")

# Failed service creation must not replace the last accepted job, while an
# accepted worker that immediately fails must be reported synchronously.
mock_systemd_exit=1
if launch_background_build build-repeater-firmwares >/dev/null 2>&1; then
  fail "failed systemd launch was reported as successful"
fi
[ "$(<"${BUILD_BACKGROUND_DIR}/latest-job")" = "$successful_job" ] \
  || fail "failed systemd launch left a stale latest-job pointer"
mock_systemd_exit=0
mock_systemd_state=failed
if launch_background_build build-sensor-firmwares >/dev/null 2>&1; then
  fail "immediate worker failure was reported as successful"
fi
failed_job=$(<"${BUILD_BACKGROUND_DIR}/latest-job")
[ "$failed_job" != "$successful_job" ] \
  || fail "accepted failed worker was not recorded as the latest attempt"
grep -Fxq 'state=failed' "${BUILD_BACKGROUND_DIR}/${failed_job}.status" \
  || fail "accepted failed worker status was not retained"
mock_systemd_state=running

BUILD_BACKGROUND_ACTIVE=1
if launch_background_build build-firmware RAK_4631_repeater >/dev/null 2>&1; then
  fail "a background worker was allowed to detach recursively"
fi
BUILD_BACKGROUND_ACTIVE=0

# Canonicalization must catch a configured path that is not visibly root until
# its symlink is resolved, before either caller constructs a log/status path.
root_link="${test_dir}/root-link"
ln -s / "$root_link"
if canonicalize_background_build_dir "$root_link" >/dev/null 2>&1; then
  fail "background directory canonicalizer accepted a symlink to root"
fi
BUILD_BACKGROUND_DIR="${test_dir}/job records"

# The durable status file survives independently of firmware output cleanup.
worker_dir="${test_dir}/worker status"
env \
  BUILD_BACKGROUND_ACTIVE=1 \
  BUILD_BACKGROUND_JOB_ID=meshcore-build-test-worker \
  BUILD_BACKGROUND_DIR="$worker_dir" \
  BUILD_BACKGROUND_BACKEND=test \
  bash build.sh help >/dev/null
grep -Fxq 'state=completed' \
  "$worker_dir/meshcore-build-test-worker.status" \
  || fail "worker did not record successful completion"
grep -Fxq 'exit_code=0' \
  "$worker_dir/meshcore-build-test-worker.status" \
  || fail "worker did not record its exit code"
[ -s "$worker_dir/meshcore-build-test-worker.log" ] \
  || fail "worker did not write a durable log"

# A second foreground or detached build cannot acquire the checkout lock.
lock_root="${test_dir}/lock root"
mkdir -p "$lock_root/.pio"
get_build_project_dir() { printf '%s\n' "$lock_root"; }
BUILD_SCRIPT_LOCK_FD=""
acquire_build_script_lock || fail "first build lock acquisition failed"
first_lock_fd=$BUILD_SCRIPT_LOCK_FD
BUILD_SCRIPT_LOCK_FD=""
if acquire_build_script_lock >/dev/null 2>&1; then
  fail "second build lock acquisition unexpectedly succeeded"
fi
BUILD_SCRIPT_LOCK_FD=$first_lock_fd
release_build_script_lock

echo "test_build_background: all checks passed"
