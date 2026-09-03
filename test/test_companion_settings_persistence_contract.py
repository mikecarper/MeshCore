#!/usr/bin/env python3
"""Static contracts for fail-closed Companion preferences and channels."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def function_body(text: str, signature: str) -> str:
    start = text.index(signature)
    opening = text.index("{", start)
    depth = 0
    for position in range(opening, len(text)):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : position]
    raise AssertionError(f"unterminated function: {signature}")


class CompanionSettingsPersistenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = source("examples/companion_radio/DataStore.cpp")
        cls.store_header = source("examples/companion_radio/DataStore.h")
        cls.mesh = source("examples/companion_radio/MyMesh.cpp")

    def test_preferences_load_has_an_observable_fail_closed_result(self):
        self.assertIn("bool loadPrefs(CompanionNodePrefs& prefs", self.store_header)
        self.assertIn("bool loadPrefsInt(const char *filename", self.store_header)
        self.assertIn("bool _prefs_load_incomplete = false;", self.store_header)

        save = function_body(
            self.store,
            "bool DataStore::savePrefs(const CompanionNodePrefs& _prefs",
        )
        self.assertRegex(
            save,
            r"if\s*\([^)]*_prefs_load_incomplete[^)]*\)\s*return false;",
        )

    def test_preferences_source_discovery_does_not_treat_stat_error_as_absence(self):
        load = function_body(
            self.store,
            "bool DataStore::loadPrefs(CompanionNodePrefs& prefs",
        )
        self.assertIn(
            'contactPathPresence(_fs, "/new_prefs",',
            load,
        )
        self.assertIn(
            'contactPathPresence(_fs, "/node_prefs",',
            load,
        )
        nrf_branch = load[: load.index("#else")]
        self.assertNotIn('_fs->exists("/new_prefs")', nrf_branch)
        self.assertNotIn('_fs->exists("/node_prefs")', nrf_branch)
        self.assertIn("_prefs_load_incomplete = true;", load)
        self.assertIn("return false;", load)
        self.assertIn("return true;", load)

    def test_preferences_load_is_staged_and_checks_mandatory_reads(self):
        load = function_body(
            self.store,
            "bool DataStore::loadPrefsInt(const char *filename",
        )

        # A failed read must not leave a partly decoded structure in live use.
        self.assertRegex(
            load,
            r"CompanionNodePrefs\s+\w+\s*=\s*_prefs\s*;",
        )
        self.assertRegex(load, r"double\s+\w+\s*=\s*node_lat\s*;")
        self.assertRegex(load, r"double\s+\w+\s*=\s*node_lon\s*;")

        # All fields in the original 84-byte base image are mandatory. The loader
        # must propagate a short underlying read instead of interpreting it as
        # defaults, and an unrecognized partial optional tail must also fail.
        self.assertRegex(
            load,
            r"file\.read\([^;]+\)\s*!=\s*size",
        )
        self.assertGreaterEqual(load.count("readField("), 22)
        self.assertGreaterEqual(load.count("readOptionalField("), 26)
        self.assertIn("success = success && file.available() == 0;", load)
        failure_at = load.index("if (!success) return false;")
        commit_at = load.index("_prefs = loaded_prefs;", failure_at)
        self.assertLess(failure_at, commit_at)
        self.assertIn("return false;", load)
        self.assertIn("return true;", load)

    def test_startup_never_rewrites_preferences_after_failed_load(self):
        begin = function_body(self.mesh, "void MyMesh::begin(bool has_display")
        load_at = begin.index("prefs_ready")
        self.assertIn("_store->loadPrefs(", begin[load_at:])
        migration_at = begin.index("migrateCompanionPowerSavingDefault", load_at)
        save_at = begin.index("_store->savePrefs(", migration_at)
        guarded_region = begin[migration_at:save_at]

        self.assertRegex(
            guarded_region,
            r"if\s*\(prefs_ready\s*&&\s*\(",
        )

    def test_channel_source_discovery_and_open_failures_latch_quarantine(self):
        load = function_body(self.store, "void DataStore::loadChannels(")
        presence_at = load.index(
            'contactPathPresence(contacts_fs, "/channels2",'
        )
        open_at = load.index('openRead(_getContactsChannelsFS(), "/channels2")')
        self.assertLess(presence_at, open_at)

        # Stat and open errors are distinct from a cleanly absent file, and
        # both must enter the same write-veto state used by saveChannels().
        self.assertGreaterEqual(load.count("_contact_load_incomplete = true;"), 2)
        self.assertIn("if (!channels_exist) return;", load)

    def test_channel_file_requires_complete_fixed_size_records(self):
        load = function_body(self.store, "void DataStore::loadChannels(")
        self.assertRegex(
            load,
            r"CHANNEL_RECORD_SIZE\s*=\s*4\s*\+\s*32\s*\+\s*32",
        )
        self.assertRegex(
            load,
            r"channels_size\s*%\s*CHANNEL_RECORD_SIZE\)\s*!=\s*0",
        )

        # Once the exact record count is known, every selected record must be
        # read fully; short reads and host-capacity refusal are errors, not EOF.
        self.assertRegex(load, r"file\.read\([^;]+\)\s*==")
        self.assertIn("if (!success)", load)
        self.assertRegex(load, r"if\s*\(!host->onChannelLoaded\(")
        self.assertGreaterEqual(load.count("_contact_load_incomplete = true;"), 4)

        save = function_body(self.store, "bool DataStore::saveChannels(")
        self.assertIn("if (_contact_load_incomplete) return false;", save)

    def test_failed_legacy_preferences_recovery_latches_preferences(self):
        migrate = function_body(self.store, "bool DataStore::migrateToSecondaryFS()")
        primary = function_body(
            migrate,
            "auto migratePrimarySources = [this, &copy, &filesEqual]()",
        )
        self.assertIn(
            'const bool is_preferences = strcmp(path, "/new_prefs") == 0;',
            primary,
        )
        self.assertEqual(
            primary.count("if (is_preferences) _prefs_load_incomplete = true;"),
            3,
        )

    def test_channel_snapshot_is_failure_atomic_without_large_stack_buffer(self):
        load = function_body(self.store, "void DataStore::loadChannels(")
        read_at = load.index("for (uint8_t channel_idx = 0;")
        apply_at = load.index("host->onChannelLoaded(", read_at)
        self.assertLess(read_at, apply_at)
        self.assertIn("malloc(sizeof(ChannelDetails) * channel_count)", load)
        self.assertNotRegex(load, r"ChannelDetails\s+\w+\s*\[MAX_GROUP_CHANNELS\]")
        self.assertIn("channel_count != 0 && loaded == nullptr", load)
        self.assertGreaterEqual(load.count("free(loaded);"), 3)


if __name__ == "__main__":
    unittest.main()
