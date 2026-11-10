#!/usr/bin/env bash
# Run all historical profile assertions, or one independent CI family.
set -euo pipefail

BUILD_PROFILE_TEST_SUITE=all
BUILD_PROFILE_TEST_LIST_ONLY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --suite)
      [ "$#" -ge 2 ] || { echo "Missing --suite value" >&2; exit 2; }
      BUILD_PROFILE_TEST_SUITE=$2
      shift 2
      ;;
    --list-sections) BUILD_PROFILE_TEST_LIST_ONLY=1; shift ;;
    --help|-h)
      echo "Usage: bash test/test_build_profiles.sh [--suite esp32-release|companion-transport|nrf52-storage] [--list-sections]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$BUILD_PROFILE_TEST_SUITE" in
  all|esp32-release|companion-transport|nrf52-storage) ;;
  *) echo "Unknown profile suite: $BUILD_PROFILE_TEST_SUITE" >&2; exit 2 ;;
esac

build_profile_test_directory=$(cd -- "$(dirname -- "$0")" && pwd -P)
source "$build_profile_test_directory/build_profile_common.sh"
if [ "$BUILD_PROFILE_TEST_LIST_ONLY" -eq 1 ]; then
  printf 'shared\tbootstrap\n'
  for build_profile_test_entry in "${BUILD_PROFILE_TEST_SECTIONS[@]}"; do
    IFS='|' read -r build_profile_test_owner build_profile_test_section <<<"$build_profile_test_entry"
    if [ "$BUILD_PROFILE_TEST_SUITE" = all ] || [ "$build_profile_test_owner" = shared ] ||
        [ "$build_profile_test_owner" = "$BUILD_PROFILE_TEST_SUITE" ]; then
      printf '%s\t%s\n' "$build_profile_test_owner" "$build_profile_test_section"
    fi
  done
  exit 0
fi

# Keep build.sh sourced at global scope: its arrays must remain visible to
# every section. Loading section definitions performs no assertions or PIO I/O.
source "$build_profile_test_directory/build_profile_bootstrap.sh"
source "$build_profile_test_directory/build_profile_esp32_release.sh"
source "$build_profile_test_directory/build_profile_companion_transport.sh"
source "$build_profile_test_directory/build_profile_nrf52_storage.sh"
prepare_build_profile_suite

for build_profile_test_entry in "${BUILD_PROFILE_TEST_SECTIONS[@]}"; do
  IFS='|' read -r build_profile_test_owner build_profile_test_section <<<"$build_profile_test_entry"
  if [ "$BUILD_PROFILE_TEST_SUITE" = all ] || [ "$build_profile_test_owner" = shared ] ||
      [ "$build_profile_test_owner" = "$BUILD_PROFILE_TEST_SUITE" ]; then
    printf 'test_build_profiles[%s]: %s\n' "$BUILD_PROFILE_TEST_SUITE" "$build_profile_test_section"
    # Invoke directly, never as a condition: Bash must preserve errexit inside
    # the section, just as in the original unsplit top-level script.
    "build_profile_$build_profile_test_section"
  fi
done
