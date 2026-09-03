#!/usr/bin/env python3
"""Static contracts for lossless Companion contact-list streaming."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def function_body(text: str, signature: str) -> str:
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1 : index]
    raise AssertionError(f"unterminated function {signature}")


class ContactStreamContractTests(unittest.TestCase):
    def test_contact_frame_write_result_is_observable(self):
        header = source("examples/companion_radio/MyMesh.h")
        impl = source("examples/companion_radio/MyMesh.cpp")
        self.assertIn(
            "bool writeContactRespFrame(uint8_t code, const ContactInfo &contact);",
            header,
        )
        body = function_body(
            impl,
            "bool MyMesh::writeContactRespFrame(uint8_t code, const ContactInfo &contact)",
        )
        self.assertRegex(
            body,
            r"return\s+_serial->writeFrame\(out_frame,\s*i\)\s*==\s*\(size_t\)i\s*;",
        )

    def test_iterator_retries_start_contact_and_end_frames(self):
        impl = source("examples/companion_radio/MyMesh.cpp")
        body = function_body(impl, "void MyMesh::checkSerialInterface()")
        self.assertIn("if (_iter_start_pending)", body)
        self.assertIn("_iter_pending_contact = contact;", body)
        self.assertIn("_iter_contact_pending = true;", body)
        self.assertRegex(
            body,
            r"if\s*\(writeContactRespFrame\(RESP_CODE_CONTACT,\s*"
            r"_iter_pending_contact\)\)",
        )
        self.assertRegex(
            body,
            r"if\s*\(_serial->writeFrame\(out_frame,\s*5\)\s*==\s*5\)\s*\{\s*"
            r"stopContactsIterator\(\)\s*;",
        )

        # The cached contact is cleared only inside the successful-write arm.
        success = body.index(
            "if (writeContactRespFrame(RESP_CODE_CONTACT, _iter_pending_contact))"
        )
        clear = body.index("_iter_contact_pending = false;", success)
        self.assertGreater(clear, success)

    def test_contact_stream_is_paced_for_slow_usb_consumers(self):
        impl = source("examples/companion_radio/MyMesh.cpp")
        self.assertIn("#define CONTACT_STREAM_FRAME_INTERVAL_MS 5", impl)
        body = function_body(impl, "void MyMesh::checkSerialInterface()")
        self.assertIn("millisHasNowPassed(_iter_next_frame_at)", body)
        self.assertGreaterEqual(
            body.count(
                "_iter_next_frame_at = "
                "futureMillis(CONTACT_STREAM_FRAME_INTERVAL_MS);"
            ),
            2,
        )

    def test_structural_contact_change_restarts_the_stream_snapshot(self):
        impl = source("examples/companion_radio/MyMesh.cpp")
        base = source("src/helpers/BaseChatMesh.cpp")
        base_header = source("src/helpers/BaseChatMesh.h")
        body = function_body(impl, "void MyMesh::checkSerialInterface()")

        self.assertIn("uint32_t getContactTableRevision() const", base_header)
        self.assertGreaterEqual(base.count("contact_table_revision++;"), 2)
        self.assertIn(
            "_iter_table_revision != getContactTableRevision()", body
        )
        restart = body.index(
            "_iter_table_revision != getContactTableRevision()"
        )
        marker = body.index("if (_iter_start_pending)", restart)
        for statement in (
            "_iter = startContactsIterator();",
            "_iter_start_pending = true;",
            "_iter_contact_pending = false;",
            "_iter_total_count = getNumContacts();",
            "_iter_table_revision = getContactTableRevision();",
        ):
            self.assertLess(body.index(statement, restart), marker)

    def test_disconnected_flow_control_reports_required_frame_failure(self):
        impl = source("src/helpers/ArduinoSerialInterface.cpp")
        body = function_body(
            impl,
            "size_t ArduinoSerialInterface::writeFrame(const uint8_t src[], size_t len)",
        )
        self.assertIn(
            "return mesh::companionFrameRequiresDelivery(src, len) ? 0 : len;",
            body,
        )
        self.assertNotIn(
            "return len;   // nobody is listening, drop instead of filling the TX buffer",
            body,
        )

    def test_hil_requires_advertised_contact_count(self):
        harness = source("tools/hil/t1000e_extrafs_stress.py")
        start = harness.index("    async def _contact_snapshot_once(")
        body = harness[start : harness.index("    async def channels(self)", start)]
        self.assertIn('getattr(reader, "contact_nb", None)', body)
        self.assertIn("if len(snapshot) == expected", body)
        self.assertIn("incomplete contact enumeration", body)


if __name__ == "__main__":
    unittest.main()
