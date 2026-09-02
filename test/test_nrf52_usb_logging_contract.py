#!/usr/bin/env python3
"""Static integration guards for nRF52 nonblocking USB diagnostics."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Nrf52UsbLoggingContractTest(unittest.TestCase):
    def test_dedicated_cdc_name_is_installed_while_descriptor_is_built(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()

        override = source.index("class DedicatedUsbLoggingCdc")
        descriptor = source.index("getInterfaceDescriptor(", override)
        custom_name = source.index(
            "setStringDescriptor(dedicated_usb_logging_descriptor)", descriptor
        )
        core_builder = source.index(
            "Adafruit_USBD_CDC::getInterfaceDescriptor(", custom_name
        )
        begin = source.index("dedicated_usb_logging_port.begin(115200)")

        self.assertLess(descriptor, custom_name)
        self.assertLess(custom_name, core_builder)
        self.assertLess(core_builder, begin)
        self.assertIn('"MeshCore Logging"', source)
        # A post-begin setter is too late: the installed Adafruit core has
        # already copied the CDC interface descriptor by then.
        self.assertNotIn(
            'dedicated_usb_logging_port.setStringDescriptor("MeshCore Logging")',
            source,
        )

    def test_connection_marker_retries_one_whole_record_without_waiting(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()

        service = source.index("void serviceDedicatedUsbLoggingFromUsbTask()")
        connection = source.index("tud_cdc_n_connected(1)", service)
        capacity = source.index(
            "single_attempt_dedicated_usb_logging_port.availableForWrite()",
            connection,
        )
        write = source.index(
            "single_attempt_dedicated_usb_logging_port.write(", capacity
        )
        progress = source.index(
            "detail::nextUsbLoggingIdentityOffset(", write
        )
        queued_drain = source.index(
            "usb_task_dedicated_usb_logging_port.drainOne();", progress
        )

        self.assertLess(connection, capacity)
        self.assertLess(capacity, write)
        self.assertLess(write, progress)
        self.assertLess(progress, queued_drain)
        self.assertIn("if (should_service)", source)
        self.assertIn('"MeshCore USB logging port\\r\\n"\n', source)
        self.assertIn(
            '"USB CDC 1; interface 02; Linux stable suffix: -if02\\r\\n"',
            source,
        )
        self.assertRegex(
            source,
            r"dedicated_usb_logging_identity_size\s*"
            r"<= CFG_TUD_CDC_TX_BUFSIZE",
        )
        self.assertNotIn('port.println("MeshCore USB logging port")', source)

    def test_partial_marker_write_restarts_from_actual_usb_task_result(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        header = (ROOT / "src/helpers/UsbLogging.h").read_text()

        service = source.index("void serviceDedicatedUsbLoggingFromUsbTask()")
        write = source.index(
            "single_attempt_dedicated_usb_logging_port.write(", service
        )
        progress = source.index(
            "detail::nextUsbLoggingIdentityOffset(", write
        )

        self.assertLess(write, progress)
        self.assertIn(
            "return written == requested ? current_offset + written : 0;",
            header,
        )
        self.assertNotIn(
            "dedicated_usb_logging_identity_offset += accepted;", source
        )
        self.assertNotIn(
            "nonblocking_dedicated_usb_logging_port.write(\n"
            "      reinterpret_cast<const uint8_t*>(dedicated_usb_logging_identity",
            source,
        )

    def test_all_nrf52_logging_ports_return_the_nonblocking_facade(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()

        self.assertIn(
            "nonblocking_dedicated_usb_logging_port(\n"
            "        usb_task_dedicated_usb_logging_port)",
            source,
        )
        self.assertIn(
            "nonblocking_primary_usb_logging_port(\n"
            "    nonblocking_primary_usb_companion_port)",
            source,
        )
        self.assertIn("return nonblocking_dedicated_usb_logging_port;", source)
        self.assertIn("return nonblocking_primary_usb_logging_port;", source)
        # ESP32 and other platforms retain their original Serial path.
        self.assertIn("#else\n    return Serial;\n  #endif", source)

    def test_facade_uses_a_zero_wait_gate_around_capacity_and_write(self):
        source = (ROOT / "src/helpers/NonBlockingWriteStream.h").read_text()

        facade = source.index("class WholeRecordNonBlockingStream")
        enter = source.index("_writer_busy.test_and_set", facade)
        capacity = source.index("_delegate.availableForWrite()", enter)
        write = source.index("_delegate.write(data, size)", capacity)
        release = source.index("_writer_busy.clear", write)
        self.assertLess(enter, capacity)
        self.assertLess(capacity, write)
        self.assertLess(write, release)
        self.assertIn("size > MAX_WRITE_SIZE", source)

    def test_primary_cdc_uses_one_direct_tinyusb_write(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()

        helper_start = source.index("static size_t writeTinyUsbCdcOnce(")
        helper_end = source.index(
            "static SingleAttemptNonBlockingStream", helper_start
        )
        helper = source[helper_start:helper_end]
        self.assertEqual(helper.count("tud_cdc_n_write(instance, data, attempt)"), 1)
        self.assertNotIn("Adafruit_USBD_CDC::write", helper)
        self.assertIn("tud_cdc_n_connected(instance)", helper)
        self.assertIn("tud_cdc_n_write_available(instance)", helper)
        self.assertIn("nonblocking_primary_usb_companion_port(", source)
        self.assertIn("single_attempt_dedicated_usb_logging_port(", source)
        self.assertIn(
            "usb_serial_interface.begin(mesh::usbCompanionPort(),", main
        )

    def test_dedicated_logging_is_drained_only_from_tinyusb_task(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        stream = (ROOT / "src/helpers/NonBlockingWriteStream.h").read_text()

        self.assertIn(
            "TaskOwnedWriteStream<> usb_task_dedicated_usb_logging_port(",
            source,
        )
        override = (
            ROOT / "src/helpers/UsbLoggingLineStateOverride.cpp"
        ).read_text()
        self.assertNotIn('extern "C" void tud_sof_cb(', source)
        self.assertIn('extern "C" void tud_sof_cb(', override)
        self.assertIn("meshTinyUsbStartOfFrame(frame_count);", override)
        self.assertIn(
            'extern "C" void meshTinyUsbStartOfFrame(', source
        )
        self.assertIn(
            'extern "C" void TinyUSB_Device_FlushCDC(void)', override
        )
        self.assertIn(
            'extern "C" uint32_t tud_cdc_n_write_flush(uint8_t itf);',
            override,
        )
        flush_override = override[override.index(
            'extern "C" void TinyUSB_Device_FlushCDC(void)'
        ):override.index('extern "C" void tud_mount_cb(void)')]
        self.assertIn("tud_cdc_n_write_flush(0)", flush_override)
        self.assertNotIn("tud_cdc_n_write_flush(1)", flush_override)
        self.assertGreaterEqual(source.count("tud_cdc_n_write_flush(1)"), 3)
        self.assertIn("serviceDedicatedUsbLoggingFromUsbTask();", source)
        self.assertIn(
            "usb_task_dedicated_usb_logging_port.drainOne();",
            source,
        )
        self.assertIn(
            "single_attempt_dedicated_usb_logging_port.write(",
            source,
        )
        self.assertIn("tud_cdc_n_connected(1)", source)
        self.assertIn("dedicated_usb_logging_reset_generation", source)
        self.assertIn("tud_sof_cb_enable(true);", source)
        self.assertIn("tud_sof_cb_enable(false);", source)
        self.assertIn(
            "tud_sof_cb_enable(dtr && isUsbLoggingEnabled());", source
        )
        self.assertIn(
            "usb_task_dedicated_usb_logging_port.discardPending();",
            source,
        )
        main_loop_service = source[
            source.index("void serviceUsbLoggingPort()") :
            source.index("Stream& usbLoggingPort()")
        ]
        self.assertNotIn("discardPending()", main_loop_service)
        producer = stream[
            stream.index("class TaskOwnedWriteStream") :
            stream.index("class WholeRecordNonBlockingStream")
        ]
        write = producer[
            producer.index("size_t write(const uint8_t* data") :
            producer.index("size_t drainOne()")
        ]
        self.assertNotIn("_delegate.write", write)
        self.assertIn("_delegate.write(record.data, record.size)", producer)

    def test_exact_cdc_line_state_hook_preserves_cdc0_touch(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        override = (
            ROOT / "src/helpers/UsbLoggingLineStateOverride.cpp"
        ).read_text()

        # Including TinyUSB's declaration here would make our definition weak
        # like the framework default instead of overriding it.
        self.assertNotIn("#include <Arduino.h>", override)
        self.assertNotIn("#include <Adafruit_TinyUSB.h>", override)
        self.assertNotIn("#include <tusb.h>", override)
        self.assertNotIn("#include <class/cdc/cdc_device.h>", override)
        self.assertIn("#include <class/cdc/cdc.h>", override)
        guard = "#if defined(ARDUINO) && defined(NRF52_PLATFORM)"
        self.assertLess(override.index(guard), override.index(
            "#include <class/cdc/cdc.h>"
        ))
        self.assertIn(
            'extern "C" void tud_cdc_line_state_cb(', override
        )
        self.assertIn(
            "meshTinyUsbCdcLineStateChanged(instance, dtr, rts);", override
        )
        self.assertIn(
            'extern "C" void tud_cdc_line_coding_cb(', override
        )
        self.assertIn(
            "cdc_line_coding_t const* line_coding", override
        )
        self.assertIn(
            "meshTinyUsbCdcLineCodingChanged(instance);", override
        )
        self.assertIn('extern "C" void tud_mount_cb(void)', override)
        self.assertIn('extern "C" void tud_umount_cb(void)', override)
        self.assertEqual(
            override.count("meshTinyUsbDeviceSessionBoundary();"), 3
        )

        bridge = source[source.index(
            'extern "C" void meshTinyUsbCdcLineStateChanged('
        ):]
        self.assertIn("if (instance == 1)", bridge)
        self.assertIn("handleDedicatedUsbLoggingLineState(dtr);", bridge)
        self.assertIn("tud_cdc_n_write_clear(1);", source)
        self.assertIn("endPrimaryUsbHostSession(true);", bridge)
        self.assertIn("if (instance == 0 && !dtr)", bridge)
        self.assertIn("tud_cdc_get_line_coding(&coding);", bridge)
        self.assertIn("if (coding.bit_rate == 1200)", bridge)
        self.assertIn("TinyUSB_Port_EnterDFU();", bridge)
        self.assertIn("const bool was_open =", bridge)
        self.assertIn("primary_usb_line_state_dtr.load(", bridge)
        cdc0_close = bridge[
            bridge.index("if (instance == 0 && !dtr)") :
            bridge.index(
                'extern "C" void meshTinyUsbCdcLineCodingChanged('
            )
        ]
        self.assertLess(
            cdc0_close.index("endPrimaryUsbHostSession(true)"),
            cdc0_close.index("tud_cdc_get_line_coding(&coding)"),
        )
        cdc0_open = cdc0_close[cdc0_close.index(
            "} else if (instance == 0)"
        ):]
        self.assertIn("const bool was_open =", cdc0_open)
        self.assertIn("if (!was_open)", cdc0_open)
        self.assertIn("tud_cdc_n_read_flush(0);", cdc0_open)
        self.assertIn("primary_usb_line_state_dtr.store(", cdc0_open)
        self.assertLess(
            cdc0_open.index("tud_cdc_n_read_flush(0);"),
            cdc0_open.index("primary_usb_line_state_dtr.store("),
        )
        boundary = source[source.index(
            'extern "C" void meshTinyUsbDeviceSessionBoundary()'
        ):source.index(
            'extern "C" void meshTinyUsbCdcLineCodingChanged('
        )]
        self.assertIn("endPrimaryUsbHostSession(false);", boundary)
        self.assertIn("handleDedicatedUsbLoggingLineState(false);", boundary)

        session_end = source[source.index(
            "static void endPrimaryUsbHostSession("
        ):source.index("#endif", source.index(
            "static void endPrimaryUsbHostSession("
        ))]
        self.assertIn("primary_usb_line_state_dtr.exchange(", session_end)
        self.assertIn("if (!previous) return;", session_end)
        self.assertLess(
            session_end.index("primary_usb_reset_generation.fetch_add("),
            session_end.index("tud_cdc_n_read_flush(0)"),
        )
        self.assertLess(
            session_end.index("tud_cdc_n_read_flush(0)"),
            session_end.index("tud_cdc_n_write_clear(0)"),
        )
        self.assertIn(
            "dedicated_usb_logging_line_state_dtr.exchange(", source
        )
        coding_bridge = source[source.index(
            'extern "C" void meshTinyUsbCdcLineCodingChanged('
        ):]
        self.assertIn("if (instance == 0)", coding_bridge)
        self.assertIn(
            "primary_usb_line_state_dtr.load(", coding_bridge
        )
        self.assertIn("endPrimaryUsbHostSession(true);", coding_bridge)
        self.assertIn("tud_cdc_n_connected(0)", coding_bridge)
        self.assertIn("if (instance == 1)", coding_bridge)
        self.assertIn("handleDedicatedUsbLoggingLineCoding();", coding_bridge)
        self.assertIn("if (tud_cdc_n_connected(1))", source)
        self.assertIn("restartDedicatedUsbLoggingHostSession();", source)
        self.assertIn("dedicated_usb_logging_host_settle_sofs = 50", source)
        self.assertIn("dedicated_usb_logging_quiet_sofs > 0", source)
        self.assertIn("--dedicated_usb_logging_quiet_sofs;", source)

        restart = source[
            source.index("static void restartDedicatedUsbLoggingHostSession()"):
            source.index("static void handleDedicatedUsbLoggingLineState")
        ]
        restart_gate = restart.index(
            "dedicated_usb_logging_port_connected.store(false"
        )
        restart_fifo = restart.index("tud_cdc_n_write_clear(1)")
        restart_state = restart.index("resetDedicatedUsbLoggingUsbTaskState()")
        restart_quiet = restart.index("dedicated_usb_logging_quiet_sofs =")
        restart_generation = restart.index(
            "dedicated_usb_logging_reset_generation.fetch_add("
        )
        self.assertLess(restart_gate, restart_fifo)
        self.assertLess(restart_fifo, restart_state)
        self.assertLess(restart_state, restart_quiet)
        self.assertLess(restart_quiet, restart_generation)

        owner_service = source[
            source.index("void serviceDedicatedUsbLoggingFromUsbTask()"):
            source.index("#elif defined(NRF52_PLATFORM)")
        ]
        quiet_start = owner_service.index(
            "if (dedicated_usb_logging_quiet_sofs > 0)"
        )
        positive_gate = owner_service.index(
            "dedicated_usb_logging_port_connected.store(\n"
            "      true",
            quiet_start,
        )
        marker_write = owner_service.index(
            "single_attempt_dedicated_usb_logging_port.write(",
            positive_gate,
        )
        queued_drain = owner_service.index(
            "usb_task_dedicated_usb_logging_port.drainOne();",
            marker_write,
        )
        self.assertLess(quiet_start, positive_gate)
        self.assertLess(positive_gate, marker_write)
        self.assertLess(marker_write, queued_drain)

        quiet_block = owner_service[quiet_start:positive_gate]
        quiet_fifo = quiet_block.index("tud_cdc_n_write_clear(1)")
        quiet_state = quiet_block.index(
            "resetDedicatedUsbLoggingUsbTaskState()"
        )
        quiet_decrement = quiet_block.index(
            "--dedicated_usb_logging_quiet_sofs;"
        )
        quiet_return = quiet_block.index("return;", quiet_decrement)
        self.assertLess(quiet_fifo, quiet_state)
        self.assertLess(quiet_state, quiet_decrement)
        self.assertLess(quiet_decrement, quiet_return)

        positive_publish_sites = list(re.finditer(
            r"dedicated_usb_logging_port_connected\.store\(\s*true",
            source,
        ))
        self.assertEqual(1, len(positive_publish_sites))
        owner_start = source.index(
            "void serviceDedicatedUsbLoggingFromUsbTask()"
        )
        owner_end = source.index("#elif defined(NRF52_PLATFORM)")
        self.assertGreater(positive_publish_sites[0].start(), owner_start)
        self.assertLess(positive_publish_sites[0].start(), owner_end)

        main_loop_service = source[
            source.index("void serviceUsbLoggingPort()"):
            source.index("Stream& usbLoggingPort()")
        ]
        positive_publish = (
            "dedicated_usb_logging_port_connected.store(\n"
            "      true"
        )
        self.assertNotIn(positive_publish, main_loop_service)

        self.assertIn(
            '#error "MESH_DUAL_CDC_LOGGING requires ENABLE_USB_INTERFACE"',
            source,
        )

    def test_nrf52_ascii_terminal_uses_the_buffered_nonblocking_primary_port(self):
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        usb = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        stream = (ROOT / "src/helpers/NonBlockingWriteStream.h").read_text()

        self.assertIn("static Stream& usbTerminalOutput()", main)
        self.assertIn("return mesh::usbTerminalPort();", main)
        self.assertNotRegex(main, r"Serial\.(?:print|println|printf|write)\(")
        self.assertNotRegex(mesh, r"Serial\.(?:print|println|printf|write)\(")
        self.assertIn(
            "_terminal_output = &mesh::usbTerminalPort();",
            mesh,
        )
        self.assertIn(
            "if (_terminal_output == &mesh::usbTerminalPort())",
            mesh,
        )
        self.assertIn("BufferedNonBlockingWriteStream<4096>", usb)
        self.assertIn("class BufferedNonBlockingWriteStream", stream)
        self.assertIn("mesh::serviceUsbTerminalPort();", main)
        self.assertIn("drainUsbTerminalOutputBeforeProtocolSwitch();", main)
        self.assertIn("mesh::discardUsbTerminalOutput();", main)
        self.assertIn("size > CAPACITY - _count", stream)
        self.assertIn("data[PRINTF_SCRATCH_SIZE - 1] == '\\0'", stream)

    def test_nrf52_serial_mota_uses_one_attempt_and_never_flushes(self):
        context = (ROOT / "src/helpers/ota/OtaContext.h").read_text()
        stream = (ROOT / "src/helpers/NonBlockingWriteStream.h").read_text()
        usb = (ROOT / "src/helpers/UsbLogging.cpp").read_text()

        self.assertIn(
            "#define OTA_FOLDER_SERIAL_STREAM ::mesh::usbMotaPort()",
            context,
        )
        self.assertIn(
            "#define OTA_FOLDER_SERIAL_WRITE_POLICY MotaStreamWritePolicy::NoFlush",
            context,
        )
        self.assertIn(
            "static SerialMotaSource src(OTA_FOLDER_SERIAL_STREAM,\n"
            "                                OTA_FOLDER_SERIAL_WRITE_POLICY, 600);",
            context,
        )
        facade = stream[
            stream.index("class SingleAttemptNonBlockingStream") :
            stream.index("class BufferedNonBlockingWriteStream")
        ]
        self.assertIn("void flush() override {}", facade)
        self.assertNotIn("_delegate.flush()", facade)
        self.assertIn(
            "AtomicWholeRecordNonBlockingStream<11>\n"
            "    nonblocking_primary_usb_mota_port(",
            usb,
        )
        self.assertIn("Stream& usbMotaPort()", usb)
        self.assertIn("return nonblocking_primary_usb_mota_port;", usb)
        atomic = stream[
            stream.index("class AtomicWholeRecordNonBlockingStream") :
            stream.index("class BufferedNonBlockingWriteStream")
        ]
        self.assertIn("_delegate.writeWholeRecord(data, size)", atomic)
        inner = stream[
            stream.index("size_t writeWholeRecord(") :
            stream.index("bool tryRunExclusive(")
        ]
        self.assertLess(
            inner.index("_writer_busy.test_and_set"),
            inner.index("_delegate.availableForWrite()"),
        )
        self.assertLess(
            inner.index("_delegate.availableForWrite()"),
            inner.index("_try_write(_context, data, size)"),
        )

    def test_cdc0_close_resets_the_complete_application_session_before_dispatch(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        header = (ROOT / "src/helpers/UsbLogging.h").read_text()
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()

        self.assertIn("bool takeUsbTerminalSessionReset();", header)
        self.assertIn("bool tryCompleteUsbTerminalSessionReset();", header)
        self.assertIn("bool takeUsbTerminalSessionReset()", source)
        self.assertIn("bool tryCompleteUsbTerminalSessionReset()", source)
        reset = main[
            main.index("static void serviceUsbTerminalHostSessionReset()") :
            main.index("static void serviceUsbTerminal()")
        ]
        self.assertIn("mesh::takeUsbTerminalSessionReset()", reset)
        self.assertIn("mesh::tryCompleteUsbTerminalSessionReset()", reset)
        self.assertIn("leaveUsbMotaMode(false);", reset)
        self.assertIn("leaveUsbTerminalMode(false);", reset)
        self.assertIn("usb_serial_interface.setPassthroughMode(false);", reset)
        self.assertIn("clearUsbTerminalLine();", reset)
        self.assertIn("usb_binary_startup_probe.cancel();", reset)
        self.assertIn("usb_terminal_host_reset_completion_pending", reset)
        self.assertIn("cancelUsbSerialOperations();", reset)
        usb_cancel = main[
            main.index("static void cancelUsbSerialOperations()"):
            main.index("static void enterUsbTerminalMode()")
        ]
        route_check = usb_cancel.index(
            "interface_manager.isReplyRouteFor(&usb_serial_interface)"
        )
        route_cancel = usb_cancel.index(
            "the_mesh.cancelSerialResponseStream()"
        )
        delayed_cancel = usb_cancel.index(
            "the_mesh.cancelSerialOperationsForRoute(&usb_serial_interface)"
        )
        route_forget = usb_cancel.index(
            "interface_manager.forgetReplyRouteForDisconnected("
        )
        self.assertLess(route_check, route_cancel)
        self.assertLess(route_cancel, delayed_cancel)
        self.assertLess(delayed_cancel, route_forget)
        self.assertIn("the_mesh.resetUsbHostSessionInput();", reset)
        self.assertNotRegex(main, r"Serial\.(?:available|read|peek)\(")
        mesh_source = (
            ROOT / "examples/companion_radio/MyMesh.cpp"
        ).read_text()
        self.assertNotRegex(
            mesh_source, r"Serial\.(?:available|read|peek)\("
        )
        session_operations = mesh_source[
            mesh_source.index("void MyMesh::cancelSerialOperationsForRoute("):
            mesh_source.index("void MyMesh::handleCmdFrame(")
        ]
        self.assertIn(
            "pending_serial_reply_route == route", session_operations
        )
        self.assertIn(
            "command_radio_reply_route == route", session_operations
        )
        self.assertIn("cancelPendingRadioParamApply();", session_operations)
        self.assertIn("if (_cli_rescue)", session_operations)
        self.assertIn(
            "memset(cli_command, 0, sizeof(cli_command));",
            session_operations,
        )

        # Every delayed Binary mesh response uses a captured requester. A
        # missing/disconnected route fails closed instead of broadcasting.
        self.assertEqual(
            mesh_source.count(
                "pending_serial_reply_route = _serial->captureReplyRoute();"
            ),
            6,
        )
        self.assertGreaterEqual(
            mesh_source.count("writePendingSerialFrame(out_frame, i);"), 5
        )
        pending_writer = mesh_source[
            mesh_source.index("size_t MyMesh::writePendingSerialFrame("):
            mesh_source.index("void MyMesh::writeDisabledFrame(")
        ]
        self.assertIn("pending_serial_reply_route == NULL", pending_writer)
        self.assertIn("writeFrameToRoute(", pending_writer)
        self.assertNotIn("_serial->writeFrame(out_frame, i);", pending_writer)
        self.assertIn(
            "command_radio_reply_route = _serial->captureReplyRoute();",
            mesh_source,
        )
        self.assertIn(
            "isReplyRouteAvailable(command_radio_reply_route)", mesh_source
        )
        self.assertIn(
            "isReplyRouteWriteBusy(command_radio_reply_route)", mesh_source
        )

        completion = source[
            source.index("static void completePrimaryUsbSessionReset(") :
            source.index("bool tryCompleteUsbTerminalSessionReset()")
        ]
        # The owner-task close/line-coding callback purges old RX immediately.
        # A second purge after the settle gate would erase a new host's
        # immediately sent APP_START (meshcli does not delay after open).
        self.assertNotIn("tud_cdc_n_read_flush(0);", completion)
        self.assertIn("tud_cdc_n_write_clear(0);", completion)
        self.assertIn("primary_usb_allowed_generation.store(", completion)
        self.assertIn(".tryRunExclusive(", source)
        self.assertIn("primary_usb_reset_settle_until.store(", source)
        self.assertIn("primary_usb_session_settle_millis = 8", source)
        self.assertIn(
            "(int32_t)(millis() - settle_until) < 0", source
        )
        access = source[
            source.index("static bool canAccessPrimaryUsbSession(") :
            source.index("#endif", source.index(
                "static bool canAccessPrimaryUsbSession("
            ))
        ]
        self.assertIn("primary_usb_line_state_dtr.load(", access)
        self.assertIn("primary_usb_allowed_generation.load(", access)
        self.assertIn("primary_usb_reset_generation.load(", access)

        loop = main[main.index("void loop() {"):]
        self.assertLess(
            loop.index("serviceUsbTerminalHostSessionReset();"),
            loop.index("the_mesh.loop();"),
        )

    def test_delayed_companion_work_keeps_exact_transport_ownership(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()

        self.assertIn("BaseSerialInterface* reply_route;", header)
        self.assertIn(
            "entry.reply_route = _serial->captureReplyRoute();", source
        )
        self.assertIn(
            "_serial->writeFrameToRoute(expected_ack_table[i].reply_route,",
            source,
        )
        self.assertIn("expected_ack_table[i].reply_route == route", source)
        ack_expiry = source[
            source.index("void MyMesh::expireExpectedAcks()") :
            source.index("MyMesh::AckTableEntry* MyMesh::findPendingTextMessage(")
        ]
        self.assertIn("entry.reply_route = NULL;", ack_expiry)
        self.assertNotIn("clearExpectedAck(entry);", ack_expiry)
        route_cancel = source[
            source.index("void MyMesh::cancelSerialOperationsForRoute(") :
            source.index("void MyMesh::resetUsbHostSessionInput()")
        ]
        self.assertIn(
            "expected_ack_table[i].reply_route = NULL;", route_cancel
        )
        self.assertNotIn(
            "clearExpectedAck(expected_ack_table[i]);", route_cancel
        )

        self.assertIn("const bool starts_long_lived_request", source)
        self.assertIn(
            "starts_long_lived_request && hasPendingReqs()", source
        )
        self.assertEqual(
            source.count(
                "pending_serial_reply_deadline =\n"
                "            futureMillis(est_timeout + est_timeout / 5);"
            ),
            6,
        )
        self.assertIn("servicePendingSerialReply();", source)
        self.assertIn(
            "another Companion request is still pending", source
        )

        self.assertIn(
            "binary_trace_reply_route = _serial->captureReplyRoute();",
            source,
        )
        self.assertIn(
            "_serial->writeFrameToRoute(binary_trace_reply_route,", source
        )
        self.assertIn("binary_trace_reply_route == route", source)

        self.assertIn("sign_data_reply_route != signing_route", source)
        self.assertIn("sign_data_reply_route == route", source)
        self.assertIn("SIGN_SESSION_TIMEOUT_MILLIS = 120000", source)
        self.assertIn("serviceSigningSession();", source)

        app_start = source[
            source.index("cmd_frame[0] == CMD_APP_START"):
            source.index("cmd_frame[0] == CMD_GET_CONTACTS")
        ]
        self.assertIn("cancelSerialOperationsForRoute(app_route);", app_start)
        self.assertNotIn("cancelPendingRadioParamApply();", app_start)
        serial_service = source[
            source.index("void MyMesh::checkSerialInterface()"):
            source.index("void MyMesh::loop()")
        ]
        self.assertNotIn("cancelPendingRadioParamApply();", serial_service)

    def test_meshcore_debug_macros_use_the_bounded_formatter_only_on_nrf52(self):
        source = (ROOT / "src/MeshCore.h").read_text()

        self.assertIn("#if defined(NRF52_PLATFORM)", source)
        self.assertIn('mesh::nrf52DebugPrintf("DEBUG: " F', source)
        self.assertIn(
            'mesh::usbLoggingPort().printf("DEBUG: " F',
            source,
        )


if __name__ == "__main__":
    unittest.main()
