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
    def test_hil_stat_failure_is_consumed_before_source_discovery(self):
        store = STORE.read_text(encoding="utf-8")
        load_contacts = function_body(store, "void DataStore::loadContacts(")
        load_pages = function_body(
            store, "bool DataStore::loadContactPages(DataStoreHost* host,"
        )

        self.assertEqual(store.count('"/__hil.readfail"'), 1)
        self.assertIn("HIL_CONTACT_STAT_FAILURE_FLAG = 0x80", store)
        marker_at = load_contacts.index("hil_failure_marker_exists")
        remove_at = load_contacts.index(
            "contacts_fs->remove(", marker_at
        )
        verify_at = load_contacts.index(
            "contactPathPresence(", remove_at
        )
        presence_at = load_contacts.index("uint32_t contact_page_presence")
        source_at = load_contacts.index("chooseContactStoreSource(")
        self.assertLess(marker_at, remove_at)
        self.assertLess(remove_at, verify_at)
        self.assertLess(verify_at, presence_at)
        self.assertLess(presence_at, source_at)

        injected_at = load_contacts.index(
            "if (page == hil_stat_failure_page)"
        )
        incomplete_at = load_contacts.index(
            "_contact_load_incomplete = true", injected_at
        )
        page_probe_at = load_contacts.index(
            "contactPathPresence(contacts_fs, path, page_exists)", injected_at
        )
        self.assertLess(injected_at, incomplete_at)
        self.assertLess(incomplete_at, page_probe_at)
        self.assertLess(page_probe_at, source_at)

        # Low-bit marker values remain handled by the existing post-discovery
        # unread-page hook rather than being consumed by the stat hook.
        self.assertIn("fail_page == page", load_pages)
        self.assertIn("HIL_CONTACT_PAGE_FAILURE_MARKER", load_pages)

    def test_contact_source_presence_uses_raw_tristate_lfs_stat(self):
        store = STORE.read_text(encoding="utf-8")
        load_contacts = function_body(store, "void DataStore::loadContacts(")
        prepare = function_body(
            store, "bool DataStore::prepareLegacyContactMigration()"
        )
        truncate = function_body(
            store, "bool DataStore::truncateLegacyContacts("
        )
        service = function_body(
            store, "bool DataStore::serviceContactWrites(DataStoreHost* host,"
        )

        stat = function_body(
            store, "static mesh::storage::ContactPathState statContactPath("
        )
        self.assertIn("fs->_lockFS();", stat)
        self.assertIn("lfs_stat(fs->_getFS(), path, &info)", stat)
        self.assertIn("fs->_unlockFS();", stat)
        self.assertIn("LFS_ERR_NOENT", stat)

        source_at = load_contacts.index("chooseContactStoreSource(")
        page_stat_at = load_contacts.index(
            "contactPathPresence(contacts_fs, path, page_exists)"
        )
        legacy_stat_at = load_contacts.index(
            'contactPathPresence(contacts_fs, "/contacts3", legacy_exists)'
        )
        marker_stat_at = load_contacts.index(
            "contactPathPresence(contacts_fs, CONTACT_MIGRATION_MARKER,"
        )
        incomplete_at = load_contacts.index(
            "_contact_load_incomplete = true", page_stat_at
        )
        self.assertLess(page_stat_at, incomplete_at)
        self.assertLess(incomplete_at, source_at)
        self.assertLess(legacy_stat_at, source_at)
        self.assertLess(marker_stat_at, source_at)
        self.assertNotIn("->exists(", load_contacts)

        for body in (prepare, truncate, service):
            self.assertIn("contactPathPresence(", body)
            self.assertNotIn("->exists(", body)

        prepare_marker_at = prepare.index(
            "contactPathPresence(fs, CONTACT_MIGRATION_MARKER, marker_exists)"
        )
        prepare_page_at = prepare.index(
            "contactPathPresence(fs, path, path_exists)"
        )
        prepare_remove_at = prepare.index("fs->remove(path)")
        self.assertLess(prepare_marker_at, prepare_page_at)
        self.assertLess(prepare_page_at, prepare_remove_at)

        truncate_legacy_at = truncate.index(
            'contactPathPresence(fs, "/contacts3", legacy_exists)'
        )
        truncate_remove_at = truncate.index('fs->remove("/contacts3")')
        truncate_marker_at = truncate.index(
            "contactPathPresence(fs, CONTACT_MIGRATION_MARKER, marker_exists)"
        )
        truncate_complete_at = truncate.index(
            "_legacy_contacts_pending_cleanup = false"
        )
        self.assertLess(truncate_legacy_at, truncate_remove_at)
        self.assertLess(truncate_remove_at, truncate_marker_at)
        self.assertLess(truncate_marker_at, truncate_complete_at)

        service_legacy_at = service.index(
            'contactPathPresence(fs, "/contacts3", legacy_exists)'
        )
        service_absent_at = service.index("if (!legacy_exists)")
        service_marker_at = service.index(
            "contactPathPresence(fs, CONTACT_MIGRATION_MARKER,"
        )
        service_complete_at = service.index(
            "_legacy_contacts_pending_cleanup = false"
        )
        self.assertLess(service_legacy_at, service_absent_at)
        self.assertLess(service_absent_at, service_marker_at)
        self.assertLess(service_marker_at, service_complete_at)

    def test_unread_contact_pages_fail_closed_without_rewrite(self):
        store = STORE.read_text(encoding="utf-8")
        header = STORE_HEADER.read_text(encoding="utf-8")
        mesh = MESH.read_text(encoding="utf-8")

        self.assertIn(
            "mesh::storage::DirtyPageSet _unread_contact_pages;", header
        )
        self.assertIn("bool hasIncompleteContactLoad() const;", header)

        load = function_body(
            store, "bool DataStore::loadContactPages(DataStoreHost* host,"
        )
        quarantine = function_body(load, "auto quarantineUnreadPage =")
        self.assertIn("_contact_slots.reserve(", quarantine)
        self.assertIn("_unread_contact_pages.mark(page)", quarantine)
        self.assertIn("_contact_load_incomplete = true", quarantine)
        self.assertGreaterEqual(load.count("quarantineUnreadPage(page)"), 4)
        self.assertIn("_dirty_contact_pages.clearAll()", load)

        for signature in (
            "bool DataStore::markContactDirty(const ContactInfo& contact)",
            "bool DataStore::releaseContact(const ContactInfo& contact)",
            "bool DataStore::restoreContactSlot(const ContactInfo& contact,",
        ):
            body = function_body(store, signature)
            self.assertIn(
                "if (hasIncompleteContactLoad()) return false;", body
            )

        service = function_body(
            store, "bool DataStore::serviceContactWrites(DataStoreHost* host,"
        )
        unread_at = service.index("_unread_contact_pages.bits()")
        write_at = service.index("writeContactPage(host, page", unread_at)
        self.assertLess(unread_at, write_at)

        pending = function_body(
            store, "bool DataStore::hasPendingContactWrites() const"
        )
        self.assertIn("!hasIncompleteContactLoad()", pending)

        incomplete = function_body(
            store, "bool DataStore::hasIncompleteContactLoad() const"
        )
        self.assertIn("_contact_load_incomplete", incomplete)
        self.assertIn("_unread_contact_pages.empty()", incomplete)

        handler = function_body(mesh, "void MyMesh::handleCmdFrame(")
        get_at = handler.index("cmd_frame[0] == CMD_GET_CONTACTS")
        next_at = handler.index("cmd_frame[0] == CMD_SET_ADVERT_NAME", get_at)
        get_contacts = handler[get_at:next_at]
        incomplete_at = get_contacts.index("hasIncompleteContactLoad()")
        lock_at = get_contacts.index("_serial->lockReplyRoute()")
        self.assertLess(incomplete_at, lock_at)
        self.assertIn("ERR_CODE_FILE_IO_ERROR", get_contacts)

        base = (ROOT / "src" / "helpers" / "BaseChatMesh.cpp").read_text(
            encoding="utf-8"
        )
        advert = function_body(base, "void BaseChatMesh::onAdvertRecv(")
        peer_data = function_body(base, "void BaseChatMesh::onPeerDataRecv(")
        path = function_body(base, "bool BaseChatMesh::onContactPathRecv(")
        self.assertIn("if (!canMutateContacts())", advert)
        self.assertGreaterEqual(peer_data.count("canMutateContacts()"), 2)
        self.assertIn("if (canMutateContacts())", path)
        self.assertIn(
            "bool MyMesh::canMutateContacts() const", mesh
        )

        terminal_path = function_body(mesh, "void MyMesh::handleTerminalPath(")
        self.assertIn("const ContactInfo previous = recipient", terminal_path)
        self.assertIn("if (!scheduleContactWrite(recipient))", terminal_path)
        self.assertIn("recipient = previous", terminal_path)

        terminal = function_body(mesh, "void MyMesh::handleTerminalCommand(")
        reset_at = terminal.index('strcmp(command, "reset path")')
        next_at = terminal.index('strcmp(command, "card")', reset_at)
        reset_path = terminal[reset_at:next_at]
        self.assertIn("previous_out_path_len", reset_path)
        self.assertIn("if (scheduleContactWrite(*recipient))", reset_path)
        self.assertIn(
            "recipient->out_path_len = previous_out_path_len", reset_path
        )

    def test_legacy_and_first_pass_page_load_fail_closed(self):
        store = STORE.read_text(encoding="utf-8")
        load_contacts = function_body(store, "void DataStore::loadContacts(")
        load_pages = function_body(
            store, "bool DataStore::loadContactPages(DataStoreHost* host,"
        )

        self.assertIn("uint32_t contact_page_presence = 0", load_contacts)
        self.assertIn("contact_page_presence |= 1UL << page", load_contacts)
        self.assertIn(
            "loadContactPages(host, 0, contact_page_presence)", load_contacts
        )
        self.assertIn("expected_page_mask", load_pages)
        self.assertIn(
            "if ((expected_page_mask & (1UL << page)) == 0) continue;",
            load_pages,
        )
        self.assertNotIn("if (!fs->exists(path)) continue;", load_pages)
        open_failure = load_pages.index("if (!file)")
        quarantine = load_pages.index("quarantineUnreadPage(page)", open_failure)
        self.assertLess(open_failure, quarantine)

        legacy_open = load_contacts.index('openRead(_getContactsChannelsFS(), "/contacts3")')
        open_failure = load_contacts.index("if (!file)", legacy_open)
        open_incomplete = load_contacts.index(
            "_contact_load_incomplete = true", open_failure
        )
        size_check = load_contacts.index(
            "isValidLegacyContactFileSize(legacy_size)", open_incomplete
        )
        read_failure = load_contacts.index("legacy_read_failed = true", size_check)
        host_failure = load_contacts.index("legacy_host_refused = true", read_failure)
        incomplete_return = load_contacts.index(
            "_contact_load_incomplete = true", host_failure
        )
        migration_prepare = load_contacts.index(
            "prepareLegacyContactMigration()", incomplete_return
        )
        self.assertLess(open_failure, open_incomplete)
        self.assertLess(size_check, read_failure)
        self.assertLess(read_failure, host_failure)
        self.assertLess(incomplete_return, migration_prepare)
        self.assertIn("record_index != _legacy_contact_count", load_contacts)
        self.assertIn("contact_page_presence = 0", load_contacts)

        reset = function_body(store, "void DataStore::resetContactPageState(")
        self.assertIn("if (clear_incomplete)", reset)
        reset_at = load_contacts.index("resetContactPageState()")
        latched_at = load_contacts.index(
            "if (_contact_load_incomplete)", reset_at
        )
        presence_at = load_contacts.index(
            "uint32_t contact_page_presence", latched_at
        )
        self.assertLess(reset_at, latched_at)
        self.assertLess(latched_at, presence_at)

        service = function_body(
            store, "bool DataStore::serviceContactWrites(DataStoreHost* host,"
        )
        self.assertLess(
            service.index("if (hasIncompleteContactLoad())"),
            service.index("_legacy_contacts_pending_cleanup"),
        )

    def test_contact_import_is_rejected_while_load_is_incomplete(self):
        mesh = MESH.read_text(encoding="utf-8")
        handler = function_body(mesh, "void MyMesh::handleCmdFrame(")
        import_at = handler.index("cmd_frame[0] == CMD_IMPORT_CONTACT")
        next_at = handler.index("cmd_frame[0] == CMD_SYNC_NEXT_MESSAGE", import_at)
        binary_import = handler[import_at:next_at]
        gate = binary_import.index("hasIncompleteContactLoad()")
        error = binary_import.index("ERR_CODE_FILE_IO_ERROR", gate)
        enqueue = binary_import.index("importContact(", error)
        ok = binary_import.index("writeOKFrame()", enqueue)
        self.assertLess(gate, error)
        self.assertLess(error, enqueue)
        self.assertLess(enqueue, ok)

        terminal_import = function_body(
            mesh, "void MyMesh::importTerminalCard("
        )
        gate = terminal_import.index("hasIncompleteContactLoad()")
        retry = terminal_import.index("reboot and retry", gate)
        enqueue = terminal_import.index("importContact(", retry)
        self.assertLess(gate, retry)
        self.assertLess(retry, enqueue)

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
        self.assertIn(
            "store.disableSecondaryFS(secondary_authority_unknown);", setup
        )
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
        self.assertIn("disableSecondaryFS(true);", invalid.group(1))
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
        quarantine_at = repair.index("_store->hasIncompleteContactLoad()")
        migrate_at = repair.index("_store->repairInternalExtraFS()")
        reload_gate = repair.index("if (authoritative_reload_required)", migrate_at)
        reboot_at = repair.index("_scheduled_reboot_at = futureMillis(1000)", reload_gate)
        return_at = repair.index("return;", reboot_at)
        contacts_at = repair.index("_store->saveContacts(this, save_filter)")
        self.assertLess(quarantine_at, migrate_at)
        self.assertLess(migrate_at, return_at)
        self.assertLess(reload_gate, reboot_at)
        self.assertLess(reboot_at, return_at)
        self.assertLess(return_at, contacts_at)
        self.assertNotIn("already_ready", repair)

    def test_secondary_migration_presence_errors_fail_before_authority_changes(self):
        store = STORE.read_text(encoding="utf-8")
        migration = function_body(store, "bool DataStore::migrateToSecondaryFS()")

        copy_helper = function_body(migration, "auto copy =")
        self.assertIn("bool exact_snapshot", migration)
        source_stat_at = copy_helper.index(
            "contactPathPresence(source_fs, path, source_exists)"
        )
        destination_stat_at = copy_helper.index(
            "contactPathPresence(dest_fs, path, destination_exists)"
        )
        destination_open_at = copy_helper.index(
            "mesh::AtomicFileWriter destination(dest_fs, path)"
        )
        self.assertLess(source_stat_at, destination_stat_at)
        self.assertLess(destination_stat_at, destination_open_at)
        self.assertNotIn("->exists(", copy_helper[: copy_helper.index("#else")])

        source_absent_at = copy_helper.index("if (!source_exists)")
        orphan_remove_at = copy_helper.index("dest_fs->remove(path)", source_absent_at)
        verified_absent_at = copy_helper.index(
            "contactPathPresence(dest_fs, path, destination_remains)",
            orphan_remove_at,
        )
        self.assertLess(source_absent_at, orphan_remove_at)
        self.assertLess(orphan_remove_at, verified_absent_at)
        conflict_at = copy_helper.index("if (!exact_snapshot)", verified_absent_at)
        self.assertLess(conflict_at, destination_open_at)

        primary_helper = function_body(
            migration, "auto migratePrimarySources ="
        )
        self.assertIn(
            "contactPathPresence(_fsExtra, path, secondary_exists)",
            primary_helper,
        )
        self.assertIn(
            "contactPathPresence(_fs, path, primary_exists)", primary_helper
        )
        self.assertNotIn(
            "->exists(", primary_helper[: primary_helper.index("#else")]
        )

        read_journal = function_body(migration, "auto readJournal =")
        journal_stat_at = read_journal.index(
            "contactPathPresence(_fsExtra, SECONDARY_MIGRATION_JOURNAL,"
        )
        journal_open_at = read_journal.index(
            "openRead(_fsExtra, SECONDARY_MIGRATION_JOURNAL)"
        )
        self.assertLess(journal_stat_at, journal_open_at)
        self.assertIn("return MigrationJournalState::IoError;", read_journal)
        self.assertNotIn("->exists(", read_journal)

        source_scan = function_body(migration, "auto hasSecondarySources =")
        self.assertGreaterEqual(source_scan.count("contactPathPresence("), 3)
        self.assertIn("present = false;", source_scan)
        self.assertIn("return false;", source_scan)
        self.assertNotIn("->exists(", source_scan)

        copy_sources = function_body(migration, "auto copySecondarySources =")
        self.assertEqual(copy_sources.count(", true)"), 3)

        pending_at = migration.index(
            "writeJournal(SECONDARY_MIGRATION_PENDING)"
        )
        scan_at = migration.index(
            "!hasSecondarySources(secondary_sources_present)"
        )
        self.assertLess(scan_at, pending_at)
        scan_failure_end = migration.index(
            "if (journal_state != MigrationJournalState::Committed", scan_at
        )
        scan_failure_branch = migration[scan_at:scan_failure_end]
        self.assertIn("_contact_load_incomplete = true;", scan_failure_branch)
        self.assertIn("return false;", scan_failure_branch)
        self.assertNotIn("_fsExtra = nullptr;", scan_failure_branch)

        journal_io_at = migration.index(
            "journal_state == MigrationJournalState::IoError"
        )
        journal_io_end = migration.index(
            "if (journal_state == MigrationJournalState::Invalid)",
            journal_io_at,
        )
        journal_io_branch = migration[journal_io_at:journal_io_end]
        self.assertIn("_contact_load_incomplete = true;", journal_io_branch)
        self.assertIn("return false;", journal_io_branch)
        self.assertNotIn("_fsExtra = nullptr;", journal_io_branch)

        primary_failure_at = migration.index("if (!migratePrimarySources())")
        self.assertLess(
            migration.index("MigrationJournalState journal_state = readJournal()"),
            primary_failure_at,
        )
        self.assertLess(primary_failure_at, journal_io_at)
        primary_failure_branch = migration[primary_failure_at:journal_io_at]
        self.assertIn("_contact_load_incomplete = true;", primary_failure_branch)
        self.assertIn("return false;", primary_failure_branch)
        self.assertNotIn("_fsExtra = nullptr;", primary_failure_branch)

        invalid_at = migration.index(
            "journal_state == MigrationJournalState::Invalid"
        )
        invalid_end = migration.index(
            "bool secondary_sources_present", invalid_at
        )
        invalid_branch = migration[invalid_at:invalid_end]
        self.assertIn("_contact_load_incomplete = true;", invalid_branch)
        self.assertIn("return false;", invalid_branch)
        self.assertNotIn("_fsExtra = nullptr;", invalid_branch)

        retire_helper = function_body(
            migration, "auto retireSecondarySources ="
        )
        last_stat_at = retire_helper.rindex("contactPathPresence(")
        first_remove_at = retire_helper.index("_fs->remove(path)")
        self.assertLess(last_stat_at, first_remove_at)
        self.assertIn("uint32_t page_presence = 0;", retire_helper)
        self.assertIn("uint16_t bucket_presence = 0;", retire_helper)
        self.assertNotIn("->exists(", retire_helper)

        committed_at = migration.index(
            "writeJournal(SECONDARY_MIGRATION_COMMITTED)"
        )
        retire_at = migration.index("retireSecondarySources()", committed_at)
        journal_remove_at = migration.index(
            "_fsExtra->remove(SECONDARY_MIGRATION_JOURNAL)", retire_at
        )
        self.assertLess(committed_at, retire_at)
        self.assertLess(retire_at, journal_remove_at)
        self.assertNotIn(
            "_fsExtra->exists(SECONDARY_MIGRATION_JOURNAL)", migration
        )

        load_channels = function_body(
            store, "void DataStore::loadChannels(DataStoreHost* host)"
        )
        save_channels = function_body(
            store, "bool DataStore::saveChannels(DataStoreHost* host)"
        )
        self.assertLess(
            load_channels.index("if (_contact_load_incomplete)"),
            load_channels.index('openRead(_getContactsChannelsFS(), "/channels2")'),
        )
        self.assertLess(
            save_channels.index("if (_contact_load_incomplete) return false;"),
            save_channels.index("mesh::AtomicFileWriter file("),
        )

    def test_legacy_primary_files_move_before_contact_journal_can_fall_back(self):
        store = STORE.read_text(encoding="utf-8")
        migration = function_body(store, "bool DataStore::migrateToSecondaryFS()")

        # Older ExtraFS test builds could leave identity and preferences only
        # on the secondary filesystem. Read the journal without changing
        # authority, then recover these files before acting on that result.
        self.assertIn(
            'static const char* to_primary[] = {"/_main.id", "/new_prefs"};',
            migration,
        )
        primary_move_at = migration.index("if (!migratePrimarySources())")
        journal_flow_at = migration.index(
            "MigrationJournalState journal_state = readJournal()"
        )
        journal_result_at = migration.index(
            "journal_state == MigrationJournalState::IoError",
            journal_flow_at,
        )
        self.assertLess(journal_flow_at, primary_move_at)
        self.assertLess(primary_move_at, journal_result_at)

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
            "bool recovered_primary = copy(_fsExtra, _fs, path, false)"
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

    def test_identity_creation_requires_proven_absence(self):
        store = STORE.read_text(encoding="utf-8")
        header = STORE_HEADER.read_text(encoding="utf-8")
        mesh = MESH.read_text(encoding="utf-8")

        self.assertIn("bool _identity_creation_blocked = false;", header)
        self.assertIn("bool _primary_storage_unavailable = false;", header)
        self.assertIn("bool _secondary_authority_unknown = false;", header)
        self.assertIn("bool canCreateMainIdentity() const;", header)
        self.assertIn(
            "void disableSecondaryFS(bool authority_unknown = true);", header
        )
        self.assertIn("void markPrimaryFSUnavailable();", header)

        load = function_body(
            store,
            "bool DataStore::loadMainIdentity(mesh::LocalIdentity &identity)",
        )
        raw_stat = load.index(
            'contactPathPresence(_fs, "/_main.id", identity_exists)'
        )
        proven_missing = load.index("if (!identity_exists) return false;", raw_stat)
        decode = load.index('identity_store.load("_main", identity)', proven_missing)
        self.assertLess(raw_stat, proven_missing)
        self.assertLess(proven_missing, decode)
        self.assertIn(
            "_identity_creation_blocked = true;",
            load[raw_stat:proven_missing],
        )
        self.assertIn(
            "_identity_creation_blocked = true;",
            load[decode:],
        )
        self.assertIn("_identity_creation_blocked = false;", load[decode:])

        save = function_body(
            store,
            "bool DataStore::saveMainIdentity(const mesh::LocalIdentity &identity)",
        )
        self.assertLess(
            save.index("if (_identity_creation_blocked) return false;"),
            save.index('identity_store.save("_main", identity)'),
        )

        migration = function_body(
            store, "bool DataStore::migrateToSecondaryFS()"
        )
        primary_helper = function_body(
            migration, "auto migratePrimarySources ="
        )
        self.assertIn(
            'const bool is_identity = strcmp(path, "/_main.id") == 0;',
            primary_helper,
        )
        self.assertGreaterEqual(
            primary_helper.count(
                "if (is_identity) _identity_creation_blocked = true;"
            ),
            3,
        )
        recovered = primary_helper.index(
            "bool recovered_primary = copy(_fsExtra, _fs, path, false)"
        )
        verify_destination = primary_helper.index(
            "contactPathPresence(_fs, path, recovered_primary_exists)",
            recovered,
        )
        reject_missing = primary_helper.index(
            "if (!recovered_primary)", verify_destination
        )
        self.assertLess(recovered, verify_destination)
        self.assertLess(verify_destination, reject_missing)

        store_begin = function_body(store, "void DataStore::begin()")
        reset_at = store_begin.index("resetContactPageState(true)")
        unmounted_at = store_begin.index("if (_primary_storage_unavailable)")
        primary_validate = store_begin.index("validateLfsFilesystem(_fs)")
        primary_failure = store_begin.index("if (!primary_ready)", primary_validate)
        secondary_validate = store_begin.index(
            "validateLfsFilesystem(_fsExtra)", primary_failure
        )
        primary_failure_branch = store_begin[primary_failure:secondary_validate]
        self.assertLess(reset_at, primary_validate)
        self.assertLess(unmounted_at, primary_validate)
        unmounted_branch = store_begin[unmounted_at:primary_validate]
        self.assertIn("_identity_creation_blocked = true;", unmounted_branch)
        self.assertIn("_contact_load_incomplete = true;", unmounted_branch)
        self.assertIn("return;", unmounted_branch)
        self.assertIn("_primary_storage_unavailable = true;", primary_failure_branch)
        self.assertIn("_identity_creation_blocked = true;", primary_failure_branch)
        self.assertIn("_contact_load_incomplete = true;", primary_failure_branch)
        self.assertIn("return;", primary_failure_branch)
        self.assertNotIn("recoverPrimaryFilesystem", store_begin)
        self.assertNotIn("->format(", store_begin)
        secondary_failure = store_begin.index(
            "if (_fsExtra != nullptr && !validateLfsFilesystem(_fsExtra))"
        )
        self.assertIn(
            "disableSecondaryFS(true);",
            store_begin[secondary_failure:],
        )

        disable = function_body(
            store, "void DataStore::disableSecondaryFS(bool authority_unknown)"
        )
        self.assertIn("_secondary_authority_unknown = true;", disable)
        self.assertIn("_contact_load_incomplete = true;", disable)
        self.assertIn("_identity_creation_blocked = true;", disable)

        unavailable = function_body(
            store, "void DataStore::markPrimaryFSUnavailable()"
        )
        self.assertIn("_primary_storage_unavailable = true;", unavailable)
        self.assertIn("_contact_load_incomplete = true;", unavailable)
        self.assertIn("_identity_creation_blocked = true;", unavailable)

        setup = function_body(MAIN.read_text(encoding="utf-8"), "void setup()")
        main = MAIN.read_text(encoding="utf-8")
        self.assertIn(
            "static const uint32_t INTERNAL_PRIMARY_FS_START = 0x000ED000UL;",
            main,
        )
        self.assertIn(
            "static const uint32_t INTERNAL_PRIMARY_FS_START = 0x0006D000UL;",
            main,
        )
        self.assertIn("7UL * FLASH_NRF52_PAGE_SIZE", main)
        primary_policy = setup.index(
            "InternalSecondaryFsBootResult primary_fs_boot"
        )
        primary_mount = setup.index(
            "InternalFS.Adafruit_LittleFS::begin()", primary_policy
        )
        blank_scan = setup.index("isErasedFlashRange(", primary_mount)
        primary_format = setup.index("InternalFS.format()", blank_scan)
        quarantine = setup.index("store.markPrimaryFSUnavailable()", primary_format)
        store_begin = setup.index("store.begin()", quarantine)
        self.assertLess(primary_policy, primary_mount)
        self.assertLess(primary_mount, blank_scan)
        self.assertLess(blank_scan, primary_format)
        self.assertLess(primary_format, quarantine)
        self.assertLess(quarantine, store_begin)
        primary_policy_body = setup[primary_policy:quarantine]
        self.assertIn("INTERNAL_PRIMARY_FS_START", primary_policy_body)
        self.assertIn("INTERNAL_PRIMARY_FS_SIZE", primary_policy_body)
        self.assertIn(
            "InternalSecondaryFsBootResult::PreservedNonBlank",
            primary_policy_body,
        )
        self.assertIn(
            "InternalSecondaryFsBootResult::InitializationFailed",
            primary_policy_body,
        )
        # Never call the core override in the nRF path: it auto-erases a
        # nonblank filesystem after any mount failure. The sole occurrence is
        # the separately scoped STM32 fallback.
        self.assertEqual(setup.count("InternalFS.begin();"), 1)
        self.assertGreater(setup.index("#else", primary_policy), primary_format)
        self.assertIn("store.disableSecondaryFS(true);", setup)
        authority = setup.index(
            "const bool secondary_authority_unknown = !extra_fs_geometry_valid"
        )
        disable_call = setup.index(
            "store.disableSecondaryFS(secondary_authority_unknown)", authority
        )
        authority_expression = setup[authority:disable_call]
        self.assertIn(
            "InternalSecondaryFsBootResult::PreservedNonBlank",
            authority_expression,
        )
        self.assertNotIn(
            "InternalSecondaryFsBootResult::InitializationFailed",
            authority_expression,
        )

        begin = function_body(mesh, "void MyMesh::begin(")
        load_at = begin.index("const bool identity_loaded =")
        creation_gate = begin.index(
            "is_new_install && !_store->canCreateMainIdentity()", load_at
        )
        generate_at = begin.index("mesh::generateUsableLocalIdentity", creation_gate)
        save_at = begin.index(
            "identity_ready = _store->saveMainIdentity(self_id)", generate_at
        )
        self.assertLess(load_at, creation_gate)
        self.assertLess(creation_gate, generate_at)
        self.assertLess(generate_at, save_at)
        blocked_branch = begin[creation_gate:generate_at]
        self.assertIn("board.reboot();", blocked_branch)
        self.assertIn("return;", blocked_branch)

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

    def test_channel_update_rolls_back_when_atomic_save_fails(self):
        mesh = MESH.read_text(encoding="utf-8")
        handler = function_body(mesh, "void MyMesh::handleCmdFrame(")
        channel_at = handler.index(
            "cmd_frame[0] == CMD_SET_CHANNEL && len >= 2 + 32 + 16"
        )
        next_command_at = handler.index(
            "cmd_frame[0] == CMD_SIGN_START", channel_at
        )
        channel_update = handler[channel_at:next_command_at]

        snapshot_at = channel_update.index(
            "getChannel(channel_idx, previous)"
        )
        mutate_at = channel_update.index(
            "setChannel(channel_idx, channel)"
        )
        save_at = channel_update.index("saveChannels()")
        rollback_at = channel_update.index(
            "setChannel(channel_idx, previous)", save_at
        )
        error_at = channel_update.index(
            "writeErrFrame(ERR_CODE_FILE_IO_ERROR)", rollback_at
        )
        ok_at = channel_update.index("writeOKFrame()")

        self.assertLess(snapshot_at, mutate_at)
        self.assertLess(mutate_at, save_at)
        self.assertLess(save_at, ok_at)
        self.assertLess(save_at, rollback_at)
        self.assertLess(rollback_at, error_at)

    def test_absent_advert_delete_avoids_bucket_rewrite_without_legacy_cache(self):
        store = STORE.read_text(encoding="utf-8")
        body = function_body(store, "bool DataStore::deleteBlobByKey(")
        fast_path = body.index(
            "if (!legacy_exists && bucket_valid"
        )
        save = body.index("saveBlobBucket(")
        self.assertLess(fast_path, save)
        self.assertIn(
            "&& (!found || records[selected].len == 0)",
            body[fast_path:save],
        )
        self.assertIn("free(records);", body[fast_path:save])
        self.assertIn("return true;", body[fast_path:save])

    def test_nrf_advert_tombstones_never_fall_back_to_legacy_cache(self):
        store = STORE.read_text(encoding="utf-8")
        read = function_body(store, "uint8_t DataStore::getBlobByKey(")
        bucket_at = read.index("findBlobInBucket(")
        nrf_return_at = read.index("return 0;", bucket_at)
        legacy_at = read.index(
            'File file = openRead(_getContactsChannelsFS(), "/adv_blobs")'
        )
        self.assertLess(nrf_return_at, legacy_at)
        between = read[nrf_return_at:legacy_at]
        self.assertIn("#endif", between)
        self.assertIn("#if !defined(NRF52_PLATFORM)", between)

        put = function_body(store, "bool DataStore::putBlobByKey(")
        cleanup_at = put.index("clearLegacyBlobRecord(")
        self.assertNotIn(
            "return false;", put[cleanup_at:put.index("#else", cleanup_at)]
        )

        delete = function_body(store, "bool DataStore::deleteBlobByKey(")
        cleanup_at = delete.index("clearLegacyBlobRecord(")
        self.assertNotIn(
            "return false;",
            delete[cleanup_at:delete.index("#else", cleanup_at)],
        )

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
