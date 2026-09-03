#!/usr/bin/env python3
"""Static contracts for failure-atomic Companion contact mutations."""

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


def command_section(handler: str, command: str, next_command: str) -> str:
    start = handler.index(f"cmd_frame[0] == {command}")
    end = handler.index(f"cmd_frame[0] == {next_command}", start)
    return handler[start:end]


class CompanionContactPersistenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mesh = source("examples/companion_radio/MyMesh.cpp")
        cls.mesh_header = source("examples/companion_radio/MyMesh.h")
        cls.data_store = source("examples/companion_radio/DataStore.cpp")
        cls.data_store_header = source("examples/companion_radio/DataStore.h")
        cls.base = source("src/helpers/BaseChatMesh.cpp")
        cls.base_header = source("src/helpers/BaseChatMesh.h")
        cls.handler = function_body(cls.mesh, "void MyMesh::handleCmdFrame(")

    def test_scheduling_results_are_observable(self):
        self.assertIn(
            "bool scheduleContactWrite(const ContactInfo& contact);",
            self.mesh_header,
        )
        self.assertIn(
            "bool scheduleContactWriteAfterRelease(const ContactInfo& contact,",
            self.mesh_header,
        )
        self.assertIn(
            "bool restoreContactWriteAfterRelease(const ContactInfo& contact,",
            self.mesh_header,
        )
        self.assertIn(
            "bool restoreContactSlot(const ContactInfo& contact, uint16_t slot);",
            self.data_store_header,
        )

        dirty = function_body(
            self.mesh,
            "bool MyMesh::scheduleContactWrite(const ContactInfo& contact)",
        )
        transient_guard = dirty.index(
            "contact.type == ADV_TYPE_NONE || isTransientContact(contact)"
        )
        mark = dirty.index("if (!_store->markContactDirty(contact))")
        self.assertLess(transient_guard, mark)
        self.assertIn("if (!_store->markContactDirty(contact))", dirty)
        self.assertIn("return false;", dirty)
        self.assertIn("scheduleLazyPersistenceMutation(", dirty)
        self.assertTrue(dirty.rstrip().endswith("return true;"))

        release = function_body(
            self.mesh,
            "bool MyMesh::scheduleContactWriteAfterRelease("
            "const ContactInfo& contact,",
        )
        self.assertIn("released_slot = contact.storage_slot", release)
        self.assertIn("if (!_store->releaseContact(contact))", release)
        self.assertIn("return false;", release)
        self.assertIn("scheduleLazyPersistenceMutation(", release)
        self.assertTrue(release.rstrip().endswith("return true;"))

        restore = function_body(
            self.mesh,
            "bool MyMesh::restoreContactWriteAfterRelease("
            "const ContactInfo& contact,",
        )
        self.assertIn(
            "if (!_store->restoreContactSlot(contact, released_slot))",
            restore,
        )

    def test_release_rollback_reserves_the_exact_original_slot(self):
        restore = function_body(
            self.data_store,
            "bool DataStore::restoreContactSlot(const ContactInfo& contact,",
        )
        reserve = restore.index("_contact_slots.reserve(slot)")
        mark = restore.index("_dirty_contact_pages.mark(", reserve)
        assign = restore.index("contact.storage_slot = slot", mark)
        self.assertLess(reserve, mark)
        self.assertLess(mark, assign)
        self.assertNotIn("_contact_slots.allocate()", restore)

    def test_page_load_uses_one_verified_payload_snapshot(self):
        load = function_body(
            self.data_store,
            "bool DataStore::loadContactPages(DataStoreHost* host,",
        )
        self.assertIn(
            "malloc(mesh::storage::CONTACT_PAGE_PAYLOAD_SIZE)", load
        )
        self.assertIn(
            "file.read(payload, mesh::storage::CONTACT_PAGE_PAYLOAD_SIZE)",
            load,
        )
        self.assertIn(
            "mesh::storage::updateCRC32(\n"
            "        0xFFFFFFFFUL, payload,",
            load,
        )
        parse_loop = load.index(
            "for (uint8_t index = 0; "
            "index < mesh::storage::CONTACTS_PER_PAGE; index++)"
        )
        self.assertNotIn("file.seek", load[parse_loop:])
        self.assertNotIn("file.read", load[parse_loop:])
        quarantine = load.index("auto quarantineUnreadPage")
        first_read_failure = load.index("if (!file)", quarantine)
        first_quarantine = load.index(
            "quarantineUnreadPage(page)", first_read_failure
        )
        self.assertLess(first_read_failure, first_quarantine)
        self.assertGreaterEqual(load.count("quarantineUnreadPage(page)"), 4)

    def test_reset_path_rolls_back_before_reporting_persistence_error(self):
        section = command_section(
            self.handler, "CMD_RESET_PATH", "CMD_ADD_UPDATE_CONTACT"
        )
        self.assertIn("lookupPersistentContactByPubKey", section)
        snapshot = section.index("previous_out_path_len")
        mutation = section.index("recipient->out_path_len = OUT_PATH_UNKNOWN")
        schedule = section.index("if (scheduleContactWrite(*recipient))")
        rollback = section.index(
            "recipient->out_path_len = previous_out_path_len", schedule
        )
        error = section.index("writeErrFrame(ERR_CODE_FILE_IO_ERROR)", rollback)
        ok = section.index("writeOKFrame()", schedule)
        clear_transient = section.index("clearTransientContact(*transient)", schedule)
        self.assertLess(snapshot, mutation)
        self.assertLess(mutation, schedule)
        self.assertLess(schedule, clear_transient)
        self.assertLess(clear_transient, ok)
        self.assertLess(schedule, ok)
        self.assertLess(ok, rollback)
        self.assertLess(rollback, error)

    def test_add_update_frame_is_validated_before_contact_lookup(self):
        section = command_section(
            self.handler, "CMD_ADD_UPDATE_CONTACT", "CMD_REMOVE_CONTACT"
        )
        short = section.index("len < CONTACT_UPDATE_FRAME_MIN_LEN")
        illegal = section.index("writeErrFrame(ERR_CODE_ILLEGAL_ARG)", short)
        lookup = section.index("lookupPersistentContactByPubKey", illegal)
        self.assertLess(short, illegal)
        self.assertLess(illegal, lookup)
        self.assertNotIn("CMD_ADD_UPDATE_CONTACT\n             && len", section)

        parser = function_body(
            self.mesh, "bool MyMesh::updateContactFromFrame("
        )
        self.assertIn("len < CONTACT_UPDATE_FRAME_MIN_LEN", parser)
        self.assertIn("frame[0] != CMD_ADD_UPDATE_CONTACT", parser)
        self.assertIn("!isPersistentContactType(type)", parser)
        self.assertIn("out_path_len != OUT_PATH_UNKNOWN", parser)
        self.assertIn("!mesh::Packet::isValidPathLen(out_path_len)", parser)
        self.assertIn("contact.name[sizeof(contact.name) - 1] = 0", parser)

        valid_types = function_body(
            self.mesh, "static bool isPersistentContactType(uint8_t type)"
        )
        for contact_type in (
            "ADV_TYPE_CHAT",
            "ADV_TYPE_REPEATER",
            "ADV_TYPE_ROOM",
            "ADV_TYPE_SENSOR",
        ):
            self.assertIn(contact_type, valid_types)

    def test_partially_present_optional_contact_fields_are_rejected(self):
        parser = function_body(
            self.mesh, "bool MyMesh::updateContactFromFrame("
        )
        self.assertIn("CONTACT_UPDATE_FRAME_GPS_LEN", parser)
        self.assertIn("CONTACT_UPDATE_FRAME_LASTMOD_LEN", parser)
        self.assertIn(
            "len > CONTACT_UPDATE_FRAME_MIN_LEN\n"
            "          && len < CONTACT_UPDATE_FRAME_GPS_LEN",
            parser,
        )
        self.assertIn(
            "len > CONTACT_UPDATE_FRAME_GPS_LEN\n"
            "          && len < CONTACT_UPDATE_FRAME_LASTMOD_LEN",
            parser,
        )

    def test_update_and_add_ack_only_after_scheduling(self):
        section = command_section(
            self.handler, "CMD_ADD_UPDATE_CONTACT", "CMD_REMOVE_CONTACT"
        )

        update_snapshot = section.index("const ContactInfo previous = *recipient")
        update_schedule = section.index("if (scheduleContactWrite(*recipient))")
        update_ok = section.index("writeOKFrame()", update_schedule)
        update_rollback = section.index("*recipient = previous", update_ok)
        update_error = section.index(
            "writeErrFrame(ERR_CODE_FILE_IO_ERROR)", update_rollback
        )
        self.assertLess(update_snapshot, update_schedule)
        self.assertLess(update_schedule, update_ok)
        self.assertLess(update_ok, update_rollback)
        self.assertLess(update_rollback, update_error)

        promotion = section.index("Promotion must free the exact transient prefix slot")
        add_section = section[promotion:]
        clear = add_section.index("clearTransientContact(*transient)")
        add = add_section.index("addContact(candidate)")
        add_schedule = section.index(
            "added != NULL && scheduleContactWrite(*added)", update_error
        )
        add_ok = section.index("writeOKFrame()", add_schedule)
        add_rollback = section.index("removeContact(*added)", add_ok)
        add_error = section.index(
            "writeErrFrame(ERR_CODE_FILE_IO_ERROR)", add_rollback
        )
        self.assertLess(clear, add)
        self.assertLess(add_schedule, add_ok)
        self.assertLess(add_ok, add_rollback)
        self.assertLess(add_rollback, add_error)
        self.assertIn("if (transient != NULL) *transient = previous_transient", add_section)

    def test_transient_prefix_is_never_compacted(self):
        transient_lookup = function_body(
            self.base,
            "ContactInfo* BaseChatMesh::lookupTransientContactByPubKey(",
        )
        persistent_lookup = function_body(
            self.base,
            "ContactInfo* BaseChatMesh::lookupPersistentContactByPubKey(",
        )
        clear = function_body(
            self.base, "bool BaseChatMesh::clearTransientContact("
        )
        remove = function_body(self.base, "bool BaseChatMesh::removeContact(")

        self.assertIn("int i = 0; i < MAX_ANON_CONTACTS", transient_lookup)
        self.assertIn(
            "int i = MAX_ANON_CONTACTS; i < num_contacts", persistent_lookup
        )
        self.assertIn("if (!isTransientContact(contact)) return false", clear)
        self.assertNotIn("num_contacts", clear)
        self.assertIn("int idx = MAX_ANON_CONTACTS", remove)
        self.assertIn("&contacts[idx] != &contact", remove)
        self.assertNotIn("id.matches", remove)

    def test_remove_reserves_persistent_deletion_before_live_mutation(self):
        section = command_section(
            self.handler, "CMD_REMOVE_CONTACT", "CMD_SHARE_CONTACT"
        )
        self.assertIn("lookupPersistentContactByPubKey", section)
        schedule = section.index("!scheduleContactWriteAfterRelease(")
        error = section.index("writeErrFrame(ERR_CODE_FILE_IO_ERROR)", schedule)
        blob = section.index("!_store->deleteBlobByKey", error)
        rollback = section.index(
            "restoreContactWriteAfterRelease(*recipient, released_slot)", blob
        )
        remove = section.index("removeContact(*recipient)", rollback)
        clear = section.index("clearTransientContact(*transient)", remove)
        ok = section.index("writeOKFrame()", clear)
        self.assertLess(schedule, error)
        self.assertLess(error, blob)
        self.assertLess(blob, rollback)
        self.assertLess(rollback, remove)
        self.assertLess(remove, clear)
        self.assertLess(clear, ok)

    def test_incomplete_contact_load_is_rejected_before_mutation_lookup(self):
        for command, next_command in (
            ("CMD_RESET_PATH", "CMD_ADD_UPDATE_CONTACT"),
            ("CMD_ADD_UPDATE_CONTACT", "CMD_REMOVE_CONTACT"),
            ("CMD_REMOVE_CONTACT", "CMD_SHARE_CONTACT"),
        ):
            section = command_section(self.handler, command, next_command)
            gate = section.index("_store->hasIncompleteContactLoad()")
            error = section.index("writeErrFrame(ERR_CODE_FILE_IO_ERROR)", gate)
            lookup = section.index("lookupPersistentContactByPubKey", error)
            self.assertLess(gate, error)
            self.assertLess(error, lookup)

    def test_failed_persistent_release_vetoes_contact_overwrite(self):
        self.assertIn(
            "virtual bool onContactOverwrite(const ContactInfo&)",
            self.base_header,
        )
        allocator_start = self.base.index(
            "ContactInfo* BaseChatMesh::allocateContactSlot("
        )
        allocator_end = self.base.index(
            "void BaseChatMesh::populateContactFromAdvert(", allocator_start
        )
        allocator = self.base[allocator_start:allocator_end]
        self.assertIn(
            "if (!onContactOverwrite(contacts[oldest_idx]))",
            allocator,
        )

        overwrite = function_body(
            self.mesh, "bool MyMesh::onContactOverwrite(const ContactInfo& contact)"
        )
        release = overwrite.index("!scheduleContactWriteAfterRelease(")
        refused = overwrite.index("return false;", release)
        delete = overwrite.index("!_store->deleteBlobByKey", refused)
        rollback = overwrite.index(
            "restoreContactWriteAfterRelease(contact, released_slot)", delete
        )
        notify = overwrite.index("PUSH_CODE_CONTACT_DELETED", delete)
        self.assertLess(release, refused)
        self.assertLess(refused, delete)
        self.assertLess(delete, rollback)
        self.assertLess(rollback, notify)
        self.assertLess(delete, notify)


if __name__ == "__main__":
    unittest.main()
