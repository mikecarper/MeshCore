#!/usr/bin/env python3
"""Static contracts for fail-closed Companion preferences and channels."""

from pathlib import Path
import re
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


def has_leading_quarantine_veto(body: str) -> bool:
    """Recognize the initial fail-closed guard, including nested calls.

    The quarantine flag must itself be the first top-level OR term, so setting
    it reaches the immediate false return before any other expression runs.
    A later guard or a flag under AND/negation does not establish that contract.
    """
    opening = re.match(r"\s*if\s*\(", body)
    if opening is None:
        return False
    start = opening.end()
    depth = 1
    terms = []
    term_start = start
    position = start
    while position < len(body):
        char = body[position]
        # This narrow source contract does not treat comments or literal text
        # as executable terms; refuse them instead of parsing all of C++.
        if char in "\"'" or body.startswith(("//", "/*"), position):
            return False
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                terms.append(body[term_start:position].strip())
                return (
                    terms[0] == "_prefs_load_incomplete"
                    and re.match(r"\s*return\s+false\s*;", body[position + 1 :])
                    is not None
                )
        elif depth == 1:
            # Lower-precedence operators can override a true OR term, and an
            # if-init statement is not the condition which controls the return.
            if char in "?,;":
                return False
            if body.startswith("||", position):
                terms.append(body[term_start:position].strip())
                position += 2
                term_start = position
                continue
        position += 1
    return False


class CompanionSettingsPersistenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = source("examples/companion_radio/DataStore.cpp")
        cls.store_header = source("examples/companion_radio/DataStore.h")
        cls.mesh = source("examples/companion_radio/MyMesh.cpp")
        cls.prefs = source("examples/companion_radio/NodePrefs.h")

    def test_preferences_load_has_an_observable_fail_closed_result(self):
        self.assertIn("bool loadPrefs(CompanionNodePrefs& prefs", self.store_header)
        self.assertIn("bool loadPrefsInt(const char *filename", self.store_header)
        self.assertIn("bool _prefs_load_incomplete = false;", self.store_header)

        save = function_body(
            self.store,
            "bool DataStore::savePrefs(const CompanionNodePrefs& _prefs",
        )
        self.assertTrue(has_leading_quarantine_veto(save))

    def test_quarantine_veto_matcher_accepts_nested_validity_calls(self):
        for guard in (
            "if (_prefs_load_incomplete) return false;",
            "if (_prefs_load_incomplete || !timezone::valid(_prefs.tz_offset))\n"
            "  return false;",
            "if (_prefs_load_incomplete || !valid(check(_prefs.tz_offset))) "
            "return false;",
        ):
            with self.subTest(guard=guard):
                self.assertTrue(has_leading_quarantine_veto(guard))

    def test_quarantine_veto_matcher_rejects_weakened_or_late_guards(self):
        for guard in (
            "if (!_prefs_load_incomplete) return false;",
            "if (_prefs_load_incomplete && !valid(_prefs.tz_offset)) return false;",
            "if ((_prefs_load_incomplete || !valid(_prefs.tz_offset)) && ready) "
            "return false;",
            "if (_prefs_load_incomplete || !valid(_prefs.tz_offset)) return true;",
            "if (_prefs_load_incomplete || valid(value) ? false : true) return false;",
            "if (_prefs_load_incomplete || valid(value), false) return false;",
            "if (_prefs_load_incomplete || false; false) return false;",
            "if (openWrite() || _prefs_load_incomplete) return false;",
            "if (false /* || _prefs_load_incomplete || */) return false;",
            'if (" || _prefs_load_incomplete || " == "unused") return false;',
            "openWrite(); if (_prefs_load_incomplete) return false;",
            "if (_prefs_load_incomplete || !valid(value) return false;",
        ):
            with self.subTest(guard=guard):
                self.assertFalse(has_leading_quarantine_veto(guard))

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
            r"CompanionNodePrefs\s+loaded_prefs\s*;",
        )
        snapshot = function_body(
            load,
            "if (!loaded_prefs.copyPersistedValuesFrom(_prefs))",
        )
        self.assertIn("file.close();", snapshot)
        self.assertIn("return false;", snapshot)
        snapshot_at = load.index("loaded_prefs.copyPersistedValuesFrom(_prefs)")
        self.assertLess(snapshot_at, load.index("loaded_prefs.bluetooth_enabled ="))
        self.assertLess(snapshot_at, load.index("readField("))
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
        commit_at = load.index("_prefs.copyPersistedValuesFrom(loaded_prefs)", failure_at)
        self.assertIn(
            "if (!_prefs.copyPersistedValuesFrom(loaded_prefs)) return false;",
            load,
        )
        self.assertLess(failure_at, commit_at)
        self.assertLess(commit_at, load.index("node_lat = loaded_lat;"))
        self.assertLess(commit_at, load.index("node_lon = loaded_lon;"))
        # Whole-object assignment also aliases the snapshot's runtime adapter
        # owners. Only a validated value snapshot may reach live preferences.
        self.assertNotRegex(load, r"\b_prefs\s*=")
        self.assertIn("return false;", load)
        self.assertIn("return true;", load)

    def test_preferences_snapshot_excludes_runtime_adapters_before_copy(self):
        copy = function_body(
            self.prefs,
            "bool copyPersistedValuesFrom(const CompanionNodePrefs& source)",
        )
        self.assertIn("reinterpret_cast<uintptr_t>(&airtime_factor)", copy)
        # Both common traffic preferences follow the optional RAK policy.
        # The final value may never include either runtime adapter.
        self.assertIn("const auto& last_value = tx_ssh;", copy)
        self.assertIn("reinterpret_cast<uintptr_t>(&last_value)", copy)
        self.assertIn("&values.tx_ssh", copy)
        self.assertIn("+ sizeof(tx_ssh)", copy)
        self.assertIn("end - begin == size - sizeof(values.tx_ssh)", copy)
        self.assertIn("reinterpret_cast<uintptr_t>(&values.radio)", copy)
        self.assertIn("reinterpret_cast<uintptr_t>(&values.custom)", copy)
        self.assertIn("size <= radio_begin - begin", copy)
        self.assertIn("size <= custom_begin - begin", copy)
        self.assertIn("size <= sizeof(values) -", copy)
        guarded_at = copy.index("if (!bounded(*this) || !bounded(source)) return false;")
        write_at = copy.index("memcpy(&airtime_factor, &source.airtime_factor, size);")
        self.assertLess(guarded_at, write_at)
        self.assertNotRegex(copy, r"\b(?:radio|custom)\s*=")

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

    def test_bluetooth_mac_config_is_an_append_only_preference_tail(self):
        cad_at = self.prefs.index("uint16_t cad_max_duration_ms")
        mode_at = self.prefs.index("uint8_t bluetooth_mac_mode", cad_at)
        address_at = self.prefs.index("uint8_t bluetooth_mac[", mode_at)
        peer_type_at = self.prefs.index(
            "uint8_t bluetooth_stealth_peer_type", address_at
        )
        peer_at = self.prefs.index(
            "uint8_t bluetooth_stealth_peer[", peer_type_at
        )
        stealth_at = self.prefs.index("uint8_t bluetooth_stealth_mode", peer_at)
        self.assertLess(cad_at, mode_at)
        self.assertLess(mode_at, address_at)
        self.assertLess(address_at, peer_type_at)
        self.assertLess(peer_type_at, peer_at)
        self.assertLess(peer_at, stealth_at)

        load = function_body(
            self.store,
            "bool DataStore::loadPrefsInt(const char *filename",
        )
        self.assertIn("sizeof(loaded_prefs.bluetooth_mac_mode)", load)
        self.assertIn("sizeof(loaded_prefs.bluetooth_mac)", load)
        read_mode_at = load.index(
            "readOptionalField(&loaded_prefs.bluetooth_mac_mode"
        )
        read_address_at = load.index(
            "readOptionalField(loaded_prefs.bluetooth_mac", read_mode_at
        )
        read_peer_type_at = load.index(
            "readOptionalField(&loaded_prefs.bluetooth_stealth_peer_type",
            read_address_at,
        )
        read_peer_at = load.index(
            "readOptionalField(loaded_prefs.bluetooth_stealth_peer",
            read_peer_type_at,
        )
        read_stealth_at = load.index(
            "readOptionalField(&loaded_prefs.bluetooth_stealth_mode", read_peer_at
        )
        self.assertIn("sizeof(loaded_prefs.bluetooth_stealth_mode)", load)
        self.assertLess(read_mode_at, read_address_at)
        self.assertLess(read_address_at, read_peer_type_at)
        self.assertLess(read_peer_type_at, read_peer_at)
        self.assertLess(read_peer_at, read_stealth_at)

        save = function_body(
            self.store,
            "bool DataStore::savePrefs(const CompanionNodePrefs& _prefs",
        )
        write_mode_at = save.index("&_prefs.bluetooth_mac_mode")
        write_address_at = save.index(
            "(uint8_t *)_prefs.bluetooth_mac,", write_mode_at
        )
        write_peer_type_at = save.index(
            "&_prefs.bluetooth_stealth_peer_type", write_address_at
        )
        write_peer_at = save.index(
            "(uint8_t *)_prefs.bluetooth_stealth_peer,", write_peer_type_at
        )
        write_stealth_at = save.index("&_prefs.bluetooth_stealth_mode", write_peer_at)
        self.assertLess(write_mode_at, write_address_at)
        self.assertLess(write_address_at, write_peer_type_at)
        self.assertLess(write_peer_type_at, write_peer_at)
        self.assertLess(write_peer_at, write_stealth_at)

    def test_channel_source_discovery_and_open_failures_latch_quarantine(self):
        load = function_body(self.store, "void DataStore::loadChannels(")
        presence_at = load.index(
            'contactPathPresence(contacts_fs, "/channels2",'
        )
        self.assertIn('const char* channel_path = "/channels2";', load)
        open_at = load.index("openRead(contacts_fs, channel_path)")
        self.assertLess(presence_at, open_at)

        # Stat and open errors are distinct from a cleanly absent file, and
        # both must enter the same write-veto state used by saveChannels().
        self.assertGreaterEqual(load.count("incomplete = true;"), 2)
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
        self.assertGreaterEqual(load.count("incomplete = true;"), 4)

        save = function_body(self.store, "bool DataStore::saveChannels(")
        self.assertIn("if (_channel_load_incomplete) return false;", save)
        self.assertIn("if (_contact_load_incomplete) return false;", save)

    def test_channel_quarantine_does_not_poison_contact_authority(self):
        contact_health = function_body(self.store, "bool DataStore::hasIncompleteContactLoad(")
        self.assertNotIn("_channel_load_incomplete", contact_health)
        self.assertIn("contactRecoveryPending()", contact_health)
        self.assertIn("_contact_load_incomplete", contact_health)
        load = function_body(self.store, "void DataStore::loadChannels(")
        self.assertIn("bool& incomplete = _channel_load_incomplete;", load)
        self.assertNotIn("_contact_load_incomplete = true", load)
        clear = function_body(self.store, "void DataStore::clearContactLoadFailure(")
        self.assertNotIn("_channel_load_incomplete", clear)

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
