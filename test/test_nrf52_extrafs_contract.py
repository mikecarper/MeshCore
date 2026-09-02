#!/usr/bin/env python3
"""Contracts for the nRF52 Companion 100 KiB internal ExtraFS path."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "variants" / "t1000-e" / "platformio.ini"
LINKER = ROOT / "boards" / "nrf52840_s140_v7_extrafs.ld"
MAIN = ROOT / "examples" / "companion_radio" / "main.cpp"
STORE = ROOT / "examples" / "companion_radio" / "DataStore.cpp"
STORE_HEADER = ROOT / "examples" / "companion_radio" / "DataStore.h"
MESH = ROOT / "examples" / "companion_radio" / "MyMesh.cpp"


def section(source: str, name: str) -> str:
    start = source.index(f"[env:{name}]")
    end = source.find("\n[", start + 1)
    return source[start:] if end < 0 else source[start:end]


def function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for position in range(opening, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : position]
    raise AssertionError(f"unterminated function: {signature}")


class Nrf52ExtraFsContractTest(unittest.TestCase):
    def test_t1000_usb_and_ble_reserve_exact_100k_region(self):
        profile = PROFILE.read_text(encoding="utf-8")
        for environment in (
            "t1000e_companion_radio_usb",
            "t1000e_companion_radio_ble",
        ):
            body = section(profile, environment)
            self.assertIn(
                "board_build.ldscript = boards/nrf52840_s140_v7_extrafs.ld",
                body,
            )
            self.assertIn("board_upload.maximum_size = 708608", body)

        linker = LINKER.read_text(encoding="utf-8")
        self.assertRegex(
            linker,
            r"FLASH\s*\(rx\)\s*:\s*ORIGIN\s*=\s*0x27000,\s*"
            r"LENGTH\s*=\s*0xD4000\s*-\s*0x27000",
        )

        main = MAIN.read_text(encoding="utf-8")
        geometry = re.search(
            r"CustomLFS\s+ExtraFS\s*\(\s*(0x[0-9A-Fa-f]+)\s*,\s*"
            r"(0x[0-9A-Fa-f]+)\s*,\s*128\s*\)",
            main,
        )
        self.assertIsNotNone(geometry)
        start = int(geometry.group(1), 16)
        size = int(geometry.group(2), 16)
        self.assertEqual(start, 0xD4000)
        self.assertEqual(size, 100 * 1024)
        self.assertEqual(start + size, 0xED000)

    def test_boot_mount_is_non_destructive_and_checks_runtime_geometry(self):
        main = MAIN.read_text(encoding="utf-8")
        setup = function_body(main, "void setup()")
        self.assertIn("isExpectedInternalExtraFsGeometry(", setup)
        self.assertIn("isInternalExtraFsReservedByApplication(", setup)
        self.assertIn("uint32_t __flash_arduino_end[]", main)
        self.assertIn("(uintptr_t)__flash_arduino_end", setup)
        self.assertIn("prepareInternalSecondaryFilesystem(", setup)
        self.assertIn(
            "ExtraFS.Adafruit_LittleFS::begin()", setup
        )
        self.assertNotIn("ExtraFS.begin()", setup)
        self.assertIn("PreservedNonBlank", setup)
        self.assertIn("store.disableSecondaryFS();", setup)
        self.assertLess(
            setup.index("prepareInternalSecondaryFilesystem("),
            setup.index("store.begin()"),
        )

    def test_corrupt_secondary_is_preserved_until_explicit_repair(self):
        store = STORE.read_text(encoding="utf-8")
        begin = function_body(store, "void DataStore::begin()")
        invalid = re.search(
            r"if\s*\(_fsExtra\s*!=\s*nullptr\s*&&\s*"
            r"!validateLfsFilesystem\(_fsExtra\)\)\s*\{(.*?)\}",
            begin,
            re.DOTALL,
        )
        self.assertIsNotNone(invalid)
        self.assertIn("_fsExtra = nullptr;", invalid.group(1))
        self.assertNotIn("format", invalid.group(1))
        self.assertNotIn("reinitializeInternalExtraFS", invalid.group(1))

        header = STORE_HEADER.read_text(encoding="utf-8")
        self.assertIn("FILESYSTEM* _configuredFsExtra;", header)
        extra_constructor = re.search(
            r"DataStore::DataStore\(FILESYSTEM& fs, FILESYSTEM& fsExtra,.*?\{",
            store,
            re.DOTALL,
        )
        self.assertIsNotNone(extra_constructor)
        self.assertIn("_configuredFsExtra(&fsExtra)", extra_constructor.group(0))

    def test_repair_is_internal_only_and_revalidates_before_activation(self):
        store = STORE.read_text(encoding="utf-8")
        repair = function_body(store, "bool DataStore::repairInternalExtraFS()")
        self.assertRegex(
            repair,
            r"#if defined\(NRF52_PLATFORM\) && defined\(EXTRAFS\) "
            r"&& !defined\(QSPIFLASH\)",
        )
        self.assertIn("reinitializeInternalExtraFS()", repair)
        self.assertIn("return migrateToSecondaryFS();", repair)

        active_retry = repair[
            repair.index("if (_fsExtra != nullptr)") :
            repair.index("if (!reinitializeInternalExtraFS())")
        ]
        self.assertIn("return migrateToSecondaryFS();", active_retry)
        self.assertNotIn("format()", active_retry)

        reinitialize = function_body(
            store, "bool DataStore::reinitializeInternalExtraFS()"
        )
        geometry_at = reinitialize.index("isExpectedInternalExtraFsGeometry(")
        self.assertIn("isInternalExtraFsReservedByApplication(", reinitialize)
        self.assertIn("uint32_t __flash_arduino_end[]", store)
        self.assertIn("(uintptr_t)__flash_arduino_end", reinitialize)
        unmount_at = reinitialize.index("extra->end()")
        format_at = reinitialize.index("extra->format()")
        mount_at = reinitialize.index(
            "extra->Adafruit_LittleFS::begin()"
        )
        validate_at = reinitialize.index(
            "validateLfsFilesystem(_configuredFsExtra)"
        )
        activate_at = reinitialize.index("_fsExtra = _configuredFsExtra;")
        self.assertLess(geometry_at, unmount_at)
        self.assertLess(unmount_at, format_at)
        self.assertLess(format_at, mount_at)
        self.assertLess(mount_at, validate_at)
        self.assertLess(validate_at, activate_at)

    def test_migration_failures_block_rebuild_and_can_be_retried(self):
        store = STORE.read_text(encoding="utf-8")
        header = STORE_HEADER.read_text(encoding="utf-8")
        migration = function_body(store, "bool DataStore::migrateToSecondaryFS()")
        self.assertIn("bool migrateToSecondaryFS();", header)
        self.assertIn('SECONDARY_MIGRATION_JOURNAL = "/.extrafs.mig"', store)
        pending_at = migration.index(
            "writeJournal(SECONDARY_MIGRATION_PENDING)"
        )
        copy_at = migration.index("copySecondarySources()", pending_at)
        committed_at = migration.index(
            "writeJournal(SECONDARY_MIGRATION_COMMITTED)", copy_at
        )
        retire_at = migration.index("retireSecondarySources()", committed_at)
        journal_remove_at = migration.index(
            "_fsExtra->remove(SECONDARY_MIGRATION_JOURNAL)", retire_at
        )
        self.assertLess(pending_at, copy_at)
        self.assertLess(copy_at, committed_at)
        self.assertLess(committed_at, retire_at)
        self.assertLess(retire_at, journal_remove_at)
        copy_failure_end = migration.index(
            "journal_state = MigrationJournalState::Committed", committed_at
        )
        self.assertIn(
            "_fsExtra = nullptr;", migration[copy_at:copy_failure_end]
        )
        copy_helper = function_body(migration, "auto copy =")
        self.assertNotIn("source_fs->remove(path)", copy_helper)
        self.assertIn("return success;", migration)
        self.assertIn("compared == expected_size", migration)

        mesh = MESH.read_text(encoding="utf-8")
        repair = function_body(mesh, "void MyMesh::repairInternalExtraFS(")
        migrate_at = repair.index("_store->repairInternalExtraFS()")
        return_at = repair.index("return;", migrate_at)
        contacts_at = repair.index("_store->saveContacts(this, save_filter)")
        self.assertLess(migrate_at, return_at)
        self.assertLess(return_at, contacts_at)
        self.assertNotIn("already_ready", repair)

    def test_legacy_primary_files_move_before_contact_journal_can_fall_back(self):
        store = STORE.read_text(encoding="utf-8")
        migration = function_body(store, "bool DataStore::migrateToSecondaryFS()")

        # Older ExtraFS test builds could leave identity and preferences only
        # on the secondary filesystem. Recover those first: every journal
        # failure below disables/falls back from that secondary filesystem.
        self.assertIn(
            'static const char* to_primary[] = {"/_main.id", "/new_prefs"};',
            migration,
        )
        primary_move_at = migration.index("if (!migratePrimarySources())")
        journal_flow_at = migration.index(
            "MigrationJournalState journal_state = readJournal()"
        )
        self.assertLess(primary_move_at, journal_flow_at)

        # A primary-present conflict is nonfatal: primary is canonical and the
        # differing secondary copy is preserved for recovery. Only failure to
        # create a missing primary copy may block the handoff.
        copy_helper = function_body(migration, "auto copy =")
        primary_helper = function_body(
            migration, "auto migratePrimarySources ="
        )
        self.assertNotIn("source_fs->remove(path)", copy_helper)
        self.assertLess(
            copy_helper.rindex("filesEqual(source_fs, dest_fs, path)"),
            copy_helper.rindex("return true;"),
        )
        conflict_at = primary_helper.index(
            "if (!filesEqual(_fsExtra, _fs, path))"
        )
        missing_copy_at = primary_helper.index(
            "else if (!copy(_fsExtra, _fs, path))"
        )
        remove_at = primary_helper.index("_fsExtra->remove(path)")
        self.assertLess(conflict_at, missing_copy_at)
        self.assertLess(missing_copy_at, remove_at)
        conflict_branch = primary_helper[
            conflict_at:missing_copy_at
        ]
        self.assertIn("continue;", conflict_branch)
        self.assertNotIn("success = false", conflict_branch)
        missing_copy_branch = primary_helper[
            missing_copy_at:remove_at
        ]
        self.assertIn("success = false;", missing_copy_branch)
        retire_branch = primary_helper[remove_at:]
        self.assertNotIn("success = false;", retire_branch)
        self.assertIn(
            "preserving conflicting legacy secondary", primary_helper
        )

    def test_local_rescue_repair_rebuilds_live_contacts_and_channels(self):
        mesh = MESH.read_text(encoding="utf-8")
        repair = function_body(mesh, "void MyMesh::repairInternalExtraFS(")
        self.assertIn("_store->repairInternalExtraFS()", repair)
        self.assertIn("_store->saveContacts(this, save_filter)", repair)
        self.assertIn("const bool channels_saved = saveChannels();", repair)
        self.assertIn("_store->getStorageTotalKb() == 100", repair)
        self.assertIn("getStorageTotalKb()", repair)

        rescue = function_body(mesh, "void MyMesh::checkCLIRescueCmd()")
        command_at = rescue.index('strcmp(cli_command, "repair extrafs")')
        rebuild_at = rescue.index('strcmp(cli_command, "rebuild")')
        repair_path = rescue[command_at:rebuild_at]
        self.assertIn("repairInternalExtraFS(output);", repair_path)

        terminal = function_body(mesh, "void MyMesh::handleTerminalCommand(")
        self.assertIn('strcmp(command, "repair extrafs")', terminal)
        self.assertIn("repairInternalExtraFS(terminalOutput());", terminal)
        self.assertIn(
            "repair extrafs (erases/rebuilds internal ExtraFS)", terminal
        )

    def test_rescue_rebuild_reports_success_only_after_every_save_succeeds(self):
        mesh = MESH.read_text(encoding="utf-8")
        rescue = function_body(mesh, "void MyMesh::checkCLIRescueCmd()")
        rebuild_at = rescue.index('strcmp(cli_command, "rebuild")')
        erase_at = rescue.index('strcmp(cli_command, "erase")', rebuild_at)
        rebuild = rescue[rebuild_at:erase_at]

        expected_saves = (
            ("identity_saved", "_store->saveMainIdentity(self_id)"),
            ("prefs_saved", "savePrefs()"),
            ("contacts_saved", "_store->saveContacts(this, save_filter)"),
            ("channels_saved", "saveChannels()"),
        )
        for result, call in expected_saves:
            self.assertRegex(
                rebuild,
                rf"(?:const\s+)?bool\s+{result}\s*=\s*"
                rf"{re.escape(call)}\s*;",
            )

        # A failed or still-pending contact rebuild must retain the lazy-write
        # retry path instead of claiming that the destructive rebuild is done.
        self.assertRegex(
            rebuild,
            r"(?:const\s+)?bool\s+contacts_complete\s*=\s*"
            r"contacts_saved\s*&&\s*"
            r"!_store->hasPendingContactWrites\(\)\s*;",
        )
        self.assertIsNotNone(
            re.search(
                r"if\s*\(\s*contacts_complete\s*\)\s*\{.*?\}\s*"
                r"else\s*\{\s*scheduleContactWriteRetry\(\)\s*;",
                rebuild,
                re.DOTALL,
            )
        )

        success_text = 'output.println("  > erase and rebuild done")'
        success_at = rebuild.index(success_text)
        success_prefix = rebuild[:success_at]
        guard_at = success_prefix.rfind("if (")
        brace_at = success_prefix.rfind("{")
        self.assertGreaterEqual(guard_at, 0)
        self.assertGreater(brace_at, guard_at)
        success_guard = success_prefix[guard_at:brace_at]
        for result in ("identity_saved", "prefs_saved", "channels_saved"):
            self.assertIn(result, success_guard)
        self.assertIn("contacts_complete", success_guard)
        self.assertNotIn("||", success_guard)

        # The positive message must follow all save attempts; the branch must
        # also retain an explicit error response for a partial rebuild.
        for _, call in expected_saves:
            self.assertLess(rebuild.index(call), success_at)
        self.assertIn("Error:", rebuild[success_at:])

    def test_quarantined_extrafs_rescue_file_commands_are_null_safe(self):
        mesh = MESH.read_text(encoding="utf-8")
        rescue = function_body(mesh, "void MyMesh::checkCLIRescueCmd()")
        unavailable = (
            'ExtraFS is unavailable; run repair extrafs first'
        )

        cat_start = rescue.index('memcmp(cli_command, "cat", 3)')
        rm_start = rescue.index('memcmp(cli_command, "rm ", 3)', cat_start)
        reboot_start = rescue.index(
            'strcmp(cli_command, "reboot")', rm_start
        )
        cat_path = rescue[cat_start:rm_start]
        rm_path = rescue[rm_start:reboot_start]

        for command_path in (cat_path, rm_path):
            secondary_at = command_path.index(
                "_store->getSecondaryFS()"
            )
            null_guard_at = command_path.index(
                "if (selected_fs == nullptr)", secondary_at
            )
            access_at = min(
                position
                for operation in (
                    "_store->openRead(selected_fs, path)",
                    "_store->removeFile(selected_fs, path)",
                )
                if (position := command_path.find(operation)) >= 0
            )
            self.assertLess(null_guard_at, access_at)
            self.assertIn(unavailable, command_path)

        self.assertIn("uint8_t buffer[64];", cat_path)
        self.assertNotRegex(cat_path, r"uint8_t\s+buffer\s*\[\s*file_size\s*\]")


if __name__ == "__main__":
    unittest.main()
