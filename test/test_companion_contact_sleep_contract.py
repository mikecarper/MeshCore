#!/usr/bin/env python3
"""Integration guards for Companion contact writes across event-only sleep."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    opening_brace = source.index("{", start)
    depth = 0
    for position in range(opening_brace, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[opening_brace + 1 : position]
    raise AssertionError(f"unterminated function: {signature}")


class CompanionContactSleepContractTest(unittest.TestCase):
    def test_initial_write_polls_but_failed_backoff_can_sleep(self):
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        body = function_body(mesh, "bool MyMesh::hasPendingWork() const")

        nrf_start = body.index("#if defined(NRF52_PLATFORM)")
        other_start = body.index("#else", nrf_start)
        conditional_end = body.index("#endif", other_start)
        nrf = body[nrf_start:other_start]
        other = body[other_start:conditional_end]

        self.assertRegex(
            nrf,
            r"contact_write_needs_polling\s*=\s*"
            r"dirty_contacts_expiry\s*!=\s*0\s*"
            r"&&\s*\(dirty_contacts_failures\s*==\s*0\s*"
            r"\|\|\s*isContactWriteDue\s*\(\s*\)\)",
        )
        self.assertRegex(
            other,
            r"contact_write_needs_polling\s*=\s*isContactWriteDue\s*\(\s*\)",
        )
        self.assertRegex(body, r"\|\|\s*contact_write_needs_polling")

    def test_contact_write_is_serviced_only_after_its_deadline(self):
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        body = function_body(mesh, "void MyMesh::loop()")

        self.assertIn("if (isContactWriteDue())", body)
        self.assertIn("_store->serviceContactWrites(this, save_filter)", body)

        due = function_body(mesh, "bool MyMesh::isContactWriteDue() const")
        self.assertEqual(due.count("_ms->getMillis()"), 1)
        self.assertIn("(int32_t)(now - dirty_contacts_expiry) >= 0", due)

    def test_repeated_mutations_do_not_postpone_first_write(self):
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()

        for signature in (
            "bool MyMesh::scheduleContactWrite(const ContactInfo& contact)",
            "bool MyMesh::scheduleContactWriteAfterRelease(",
        ):
            body = function_body(mesh, signature)
            self.assertIn("mesh::scheduleLazyPersistenceMutation(", body)
            self.assertIn("dirty_contacts_failures", body)
            self.assertIn("futureMillis(LAZY_CONTACTS_WRITE_DELAY)", body)
            self.assertNotRegex(
                body,
                r"dirty_contacts_expiry\s*=\s*"
                r"futureMillis\s*\(\s*LAZY_CONTACTS_WRITE_DELAY\s*\)",
            )

    def test_every_direct_contact_rearm_preserves_zero_sentinel(self):
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        begin = function_body(mesh, "void MyMesh::begin(")
        save = function_body(mesh, "void MyMesh::saveContacts()")
        loop = function_body(mesh, "void MyMesh::loop()")

        for body in (begin, save, loop):
            self.assertIn("mesh::nonzeroLazyPersistenceDeadline(", body)

        self.assertIn("futureMillis(CONTACT_PAGE_WRITE_GAP)", loop)

    def test_failed_writes_back_off_until_a_success_resets_state(self):
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        save = function_body(mesh, "void MyMesh::saveContacts()")
        loop = function_body(mesh, "void MyMesh::loop()")
        retry = function_body(mesh, "void MyMesh::scheduleContactWriteRetry()")

        self.assertIn("uint8_t dirty_contacts_failures;", header)
        self.assertIn("mesh::recordLazyPersistenceSaveFailure(", retry)
        self.assertIn(
            "mesh::LAZY_PERSISTENCE_MAX_RETRY_DELAY_MILLIS", retry
        )
        self.assertIn("LAZY_CONTACTS_WRITE_DELAY", retry)
        self.assertIn("mesh::completeLazyPersistenceSave(", retry)
        self.assertIn("retry in %lu ms", retry)
        for body in (save, loop):
            self.assertIn("scheduleContactWriteRetry();", body)
            self.assertIn("mesh::resetLazyPersistenceAfterSuccess(", body)

    def test_main_loop_gates_sleep_on_pending_work(self):
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()
        sleep_start = main.index("bool can_sleep =")
        sleep_end = main.index("if (can_sleep)", sleep_start)
        sleep_gate = main[sleep_start:sleep_end]

        self.assertIn("!the_mesh.hasPendingWork()", sleep_gate)

        nrf52 = (ROOT / "src/helpers/NRF52Board.cpp").read_text()
        body = function_body(nrf52, "void NRF52Board::sleep(uint32_t secs)")
        self.assertIn("event-driven sleep instead of timed sleep", body)
        self.assertIn("sd_app_evt_wait();", body)

    def test_all_runtime_reboots_flush_contacts_first(self):
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        self.assertIn("bool flushContactsBeforeReboot();", header)

        flush = function_body(mesh, "bool MyMesh::flushContactsBeforeReboot()")
        self.assertIn("_store->flushContactWrites(this, save_filter)", flush)
        self.assertIn("scheduleContactWriteRetry();", flush)
        self.assertIn("mesh::resetLazyPersistenceAfterSuccess(", flush)

        framed = function_body(mesh, "void MyMesh::handleCmdFrame(")
        reboot_at = framed.index("cmd_frame[0] == CMD_REBOOT")
        framed_reboot = framed[reboot_at : framed.index(
            "cmd_frame[0] == CMD_GET_BATT_AND_STORAGE", reboot_at
        )]
        self.assertIn("flushContactsBeforeReboot()", framed_reboot)

        rescue = function_body(mesh, "void MyMesh::checkCLIRescueCmd()")
        rescue_reboot = rescue[rescue.index('strcmp(cli_command, "reboot")') :]
        self.assertIn("flushContactsBeforeReboot()", rescue_reboot)

        callback = function_body(mesh, "void MyMesh::rebootNow()")
        self.assertIn("flushContactsBeforeReboot()", callback)

        loop = function_body(mesh, "void MyMesh::loop()")
        scheduled = loop[: loop.index("#if COMPANION_FEATURE_TEMP_RADIO")]
        self.assertIn("flushContactsBeforeReboot()", scheduled)

        pending = function_body(mesh, "bool MyMesh::hasPendingWork() const")
        self.assertIn("|| _scheduled_reboot_at != 0", pending)

    def test_ota_apply_reboot_uses_companion_persistence_hook(self):
        mesh_base_header = (ROOT / "src/Mesh.h").read_text()
        mesh_base = (ROOT / "src/Mesh.cpp").read_text()
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()

        self.assertIn(
            "virtual bool prepareForOtaReboot() { return true; }",
            mesh_base_header,
        )
        maintenance = function_body(
            mesh_base, "void __attribute__((noinline)) Mesh::serviceLoopMaintenance()"
        )
        guard_at = maintenance.index("prepareForOtaReboot()")
        apply_at = maintenance.index("ota::ota_reboot_to_apply()")
        self.assertLess(guard_at, apply_at)
        self.assertIn("oc.apply_at = futureMillis(1000)", maintenance)

        self.assertIn("bool prepareForOtaReboot() override;", header)
        guard = function_body(mesh, "bool MyMesh::prepareForOtaReboot()")
        self.assertIn("dirty_contacts_failures != 0", guard)
        self.assertIn("!isContactWriteDue()", guard)
        self.assertIn("flushContactsBeforeReboot()", guard)

    def test_deferred_ota_apply_prevents_event_only_sleep(self):
        mesh_base_header = (ROOT / "src/Mesh.h").read_text()
        mesh_base = (ROOT / "src/Mesh.cpp").read_text()
        self.assertIn("bool hasPendingOtaApply() const;", mesh_base_header)

        pending = function_body(mesh_base, "bool Mesh::hasPendingOtaApply() const")
        self.assertIn("defined(ENABLE_OTA)", pending)
        self.assertIn("!defined(OTA_SEEDER_ONLY)", pending)
        self.assertIn("ota::ota_ctx().apply_pending", pending)

        role_sources = (
            ROOT / "examples/companion_radio/MyMesh.cpp",
            ROOT / "examples/simple_sensor/SensorMesh.cpp",
            ROOT / "examples/simple_room_server/MyMesh.cpp",
            ROOT / "examples/simple_repeater/MyMesh.cpp",
        )
        for role_source in role_sources:
            role = role_source.read_text()
            signature = (
                "bool SensorMesh::hasPendingWork() const"
                if role_source.name == "SensorMesh.cpp"
                else "bool MyMesh::hasPendingWork() const"
            )
            has_pending_work = function_body(role, signature)
            self.assertIn("hasPendingOtaApply()", has_pending_work, role_source)

    def test_every_companion_ui_shutdown_uses_persistence_guard(self):
        abstract = (
            ROOT / "examples/companion_radio/AbstractUITask.h"
        ).read_text()
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        self.assertIn("public UIShutdownGuard", header)
        self.assertIn("virtual bool prepareForUiShutdown() = 0;", abstract)
        self.assertIn("_ui->setShutdownGuard(this)", mesh)
        callback = function_body(mesh, "bool MyMesh::prepareForUiShutdown()")
        self.assertIn("flushContactsBeforeReboot()", callback)

        for ui_name in ("ui-orig", "ui-new", "ui-tiny"):
            ui = (
                ROOT / "examples/companion_radio" / ui_name / "UITask.cpp"
            ).read_text()
            shutdown = function_body(ui, "void UITask::shutdown(bool restart)")
            guard_at = shutdown.index("if (!prepareForShutdown()) return;")
            reboot_at = shutdown.index("_board->reboot()")
            poweroff_at = shutdown.index("_board->powerOff()")
            self.assertLess(guard_at, reboot_at, ui_name)
            self.assertLess(guard_at, poweroff_at, ui_name)

    def test_identity_import_rearms_page_repairs(self):
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        framed = function_body(mesh, "void MyMesh::handleCmdFrame(")
        import_at = framed.index("cmd_frame[0] == CMD_IMPORT_PRIVATE_KEY")
        import_end = framed.index("cmd_frame[0] == CMD_SEND_RAW_DATA", import_at)
        identity_import = framed[import_at:import_end]
        flush_at = identity_import.index("flushContactsBeforeReboot()")
        save_identity_at = identity_import.index("_store->saveMainIdentity(")
        load_at = identity_import.index("_store->loadContacts(this)")
        reset_at = identity_import.index(
            "mesh::resetLazyPersistenceAfterSuccess(", save_identity_at
        )
        rearm_at = identity_import.index(
            "mesh::scheduleLazyPersistenceMutation(", load_at
        )
        self.assertLess(flush_at, save_identity_at)
        self.assertLess(reset_at, load_at)
        self.assertLess(load_at, rearm_at)
        self.assertIn("_store->hasPendingContactWrites()", identity_import)

    def test_webconfig_reboot_request_is_one_shot_if_flush_fails(self):
        webconfig = (
            ROOT / "src/helpers/esp32/WebConfigServer.cpp"
        ).read_text()
        tick = function_body(webconfig, "void WebConfigServer::tick(")
        due_at = tick.index("WebConfigBatch::rebootDue(")
        callback_at = tick.index("_cb->rebootNow()", due_at)
        clear_at = tick.index("_reboot_at = 0", due_at)
        self.assertLess(clear_at, callback_at)


if __name__ == "__main__":
    unittest.main()
