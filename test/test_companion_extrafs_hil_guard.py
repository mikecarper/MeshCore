#!/usr/bin/env python3
"""Static security contract for the destructive Companion ExtraFS HIL hook."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "examples" / "companion_radio" / "MyMesh.cpp"
HEADER_PATH = ROOT / "examples" / "companion_radio" / "MyMesh.h"
HARNESS_PATH = ROOT / "tools" / "hil" / "t1000e_extrafs_stress.py"

INTERNAL_EXTRAFS_GUARD = (
    "defined(NRF52_PLATFORM)&&defined(EXTRAFS)&&!defined(QSPIFLASH)"
)
HIL_GUARD = "defined(MESHCORE_EXTRAFS_HIL)"
INVALID_TARGET_GUARD = (
    "defined(MESHCORE_EXTRAFS_HIL)&&"
    "(!defined(NRF52_PLATFORM)||!defined(EXTRAFS)||defined(QSPIFLASH))"
)


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


def canonical_guard(expression: str) -> str:
    return re.sub(r"\s+", "", expression.replace("\\", ""))


def active_preprocessor_guards(source: str, offset: int) -> list[str]:
    """Return the active #if expressions at the source line containing offset."""
    target_line = source[:offset].count("\n")
    lines = source.splitlines()
    guards: list[str] = []
    line_number = 0

    while line_number <= target_line:
        logical = lines[line_number]
        while logical.rstrip().endswith("\\"):
            line_number += 1
            logical = logical.rstrip()[:-1] + lines[line_number]

        directive = logical.strip()
        if directive.startswith("#if "):
            guards.append(canonical_guard(directive[4:]))
        elif directive.startswith("#ifdef "):
            guards.append(f"defined({directive[7:].strip()})")
        elif directive.startswith("#ifndef "):
            guards.append(f"!defined({directive[8:].strip()})")
        elif directive.startswith("#elif "):
            if not guards:
                raise AssertionError("#elif without #if")
            guards[-1] = canonical_guard(directive[6:])
        elif directive == "#else":
            if not guards:
                raise AssertionError("#else without #if")
            guards[-1] = f"!({guards[-1]})"
        elif directive == "#endif":
            if not guards:
                raise AssertionError("#endif without #if")
            guards.pop()

        line_number += 1

    return guards


class CompanionExtraFsHilGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.header = HEADER_PATH.read_text(encoding="utf-8")
        cls.harness = HARNESS_PATH.read_text(encoding="utf-8")

    def test_enabling_hil_on_any_other_storage_target_is_a_build_error(self):
        error_text = (
            '#error "MESHCORE_EXTRAFS_HIL requires an nRF52 internal '
            'ExtraFS build"'
        )
        error_at = self.source.index(error_text)
        self.assertIn(
            INVALID_TARGET_GUARD,
            active_preprocessor_guards(self.source, error_at),
        )

    def test_hil_api_exists_only_inside_both_compile_time_guards(self):
        declaration_at = self.header.index(
            "bool handleExtraFsHilCommand(const char* command"
        )
        definition_at = self.source.index(
            "bool MyMesh::handleExtraFsHilCommand(const char* command"
        )

        for text, location in (
            (self.header, declaration_at),
            (self.source, definition_at),
        ):
            active = active_preprocessor_guards(text, location)
            self.assertIn(INTERNAL_EXTRAFS_GUARD, active)
            self.assertIn(HIL_GUARD, active)

    def test_only_direct_companion_run_cli_intercepts_the_hil_command(self):
        # Exclude the qualified MyMesh:: definition: exactly one invocation is
        # allowed in the whole implementation.
        invocations = list(
            re.finditer(r"(?<!::)\bhandleExtraFsHilCommand\s*\(", self.source)
        )
        self.assertEqual(len(invocations), 1)
        call_at = invocations[0].start()
        self.assertIn(
            HIL_GUARD, active_preprocessor_guards(self.source, call_at)
        )

        framed = function_body(self.source, "void MyMesh::handleCmdFrame(")
        run_cli_at = framed.index("mesh::companion::isRunCliFrame(")
        next_frame_at = framed.index(
            "cmd_frame[0] == CMD_SEND_TXT_MSG", run_cli_at
        )
        run_cli_branch = framed[run_cli_at:next_frame_at]
        hil_at = run_cli_branch.index("handleExtraFsHilCommand(")
        generic_at = run_cli_branch.index(
            "if (!handled) handled = handleCommand(", hil_at
        )
        self.assertLess(hil_at, generic_at)
        self.assertRegex(
            run_cli_branch,
            re.compile(
                r"#if defined\(MESHCORE_EXTRAFS_HIL\)\s+"
                r".*?handled = handleExtraFsHilCommand\(.*?\);\s+"
                r"#endif\s+if \(!handled\) handled = handleCommand\(",
                re.DOTALL,
            ),
        )

    def test_generic_and_on_air_command_paths_cannot_reach_hil(self):
        generic = function_body(
            self.source, "bool MyMesh::handleCommand(const char* command"
        )
        on_air = function_body(
            self.source, "void MyMesh::onCLICommandRecv("
        )

        self.assertNotIn("handleExtraFsHilCommand", generic)
        self.assertNotIn("hil extrafs", generic)
        self.assertNotIn("handleExtraFsHilCommand", on_air)
        self.assertIn("handleCommand(text, sender_timestamp, reply)", on_air)

    def test_filler_reports_only_read_verified_durable_bytes(self):
        body = function_body(
            self.source,
            "bool MyMesh::handleExtraFsHilCommand(const char* command",
        )
        fill_at = body.index('static const char* const fill_prefix = "fill ";')
        corrupt_at = body.index(
            'static const char* const corrupt_prefix = "corrupt-page ";',
            fill_at,
        )
        fill = body[fill_at:corrupt_at]
        self.assertIn("fill.flush();", fill)
        self.assertIn("fill.close();", fill)
        self.assertIn(
            "const uint32_t durable_size = verify ? verify.size() : 0;", fill
        )
        self.assertIn("if (!extent_written || durable_size != committed)", fill)
        self.assertIn("committed = durable_size;", fill)
        self.assertNotIn("written += actual", fill)

    def test_slow_durable_filler_has_a_scoped_extended_client_timeout(self):
        cli_at = self.harness.index("    async def cli(")
        cli_end = self.harness.index("    async def clear_filler(", cli_at)
        cli = self.harness[cli_at:cli_end]
        fill_at = self.harness.index(
            "    async def fill_storage_to_enospc("
        )
        fill_end = self.harness.index(
            "    async def fill_and_test_channel_atomicity(", fill_at
        )
        fill = self.harness[fill_at:fill_end]

        self.assertIn("timeout: Optional[float] = None", cli)
        self.assertIn("self.mc.commands.send(", cli)
        self.assertIn("timeout=timeout", cli)
        self.assertIn("EventType.CLI_REPLY", cli)
        self.assertIn("EventType.ERROR", cli)
        self.assertIn(
            "timeout=EXTRAFS_FILL_TIMEOUT_SECONDS",
            fill,
        )
        self.assertNotIn("self.mc.commands.default_timeout =", cli)

    def test_occupancy_corruption_is_explicit_verified_and_header_only(self):
        body = function_body(
            self.source,
            "bool MyMesh::handleExtraFsHilCommand(const char* command",
        )
        start = body.index(
            'static const char* const corrupt_occupied_prefix = '
            '"corrupt-occupied ";'
        )
        end = body.index(
            "if (strncmp(action, corrupt_prefix", start
        )
        corrupt = body[start:end]

        self.assertIn('strcmp(slot_end, " CONFIRM") != 0', corrupt)
        self.assertIn("slot >= 32", corrupt)
        self.assertIn("_store->hasPendingContactWrites()", corrupt)
        self.assertIn("const uint32_t byte_offset = 8 + slot / 8;", corrupt)
        self.assertIn("update.flush();", corrupt)
        self.assertIn("update.close();", corrupt)
        self.assertIn("const bool changed_on_flash", corrupt)
        self.assertNotIn("CONTACT_PAGE_HEADER_SIZE)", corrupt)

    def test_page_mask_reads_the_persisted_header(self):
        body = function_body(
            self.source,
            "bool MyMesh::handleExtraFsHilCommand(const char* command",
        )
        start = body.index(
            'static const char* const page_mask_prefix = "page-mask ";'
        )
        end = body.index(
            'static const char* const corrupt_occupied_prefix', start
        )
        page_mask = body[start:end]

        self.assertIn("page_file.read(raw_header", page_mask)
        self.assertIn("decodeContactPageHeader(", page_mask)
        self.assertIn('occupied=%08lx', page_mask)

    def test_advert_cache_fault_hooks_are_direct_only_and_confirmed(self):
        body = function_body(
            self.source,
            "bool MyMesh::handleExtraFsHilCommand(const char* command",
        )
        for command in (
            '"contact-slot "',
            '"seed-advert "',
            '"clear-advert "',
            '"fail-read-page "',
            '"fail-stat-page "',
        ):
            self.assertIn(command, body)
        self.assertGreaterEqual(body.count('strcmp(encoded + 14, confirm)'), 2)
        self.assertIn("_store->putBlobByKey(", body)
        self.assertIn("_store->deleteBlobByKey(", body)
        self.assertIn("_store->restoreContactSlot", self.source)
        self.assertIn("_store->hasPendingContactWrites()", body)

    def test_read_and_stat_faults_share_one_exclusive_marker(self):
        body = function_body(
            self.source,
            "bool MyMesh::handleExtraFsHilCommand(const char* command",
        )
        arm = function_body(body, "auto armContactPageFailure =")

        self.assertEqual(body.count('"/__hil.readfail"'), 1)
        self.assertIn("contact_stat_failure_flag = 0x80", body)
        self.assertIn("fs->exists(contact_failure_marker)", arm)
        self.assertIn("return -1;", arm)
        self.assertIn("marker.write(&encoded_failure, 1)", arm)
        self.assertIn("verify.read() == encoded_failure", arm)
        self.assertGreaterEqual(
            body.count("armContactPageFailure("), 2
        )  # both command modes invoke the shared writer
        self.assertIn(
            'strcmp(end, " CONFIRM") != 0',
            body[body.index('"fail-stat-page "'):],
        )
        self.assertGreaterEqual(
            body.count('"HIL contact failure already armed"'), 2
        )

    def test_destructive_contact_faults_reject_incomplete_loads(self):
        body = function_body(
            self.source,
            "bool MyMesh::handleExtraFsHilCommand(const char* command",
        )
        sections = (
            ('static const char* const fail_stat_prefix = "fail-stat-page ";',
             'static const char* const fail_read_prefix = "fail-read-page ";'),
            ('static const char* const fail_read_prefix = "fail-read-page ";',
             'static const char* const fill_prefix = "fill ";'),
            ('static const char* const corrupt_occupied_prefix = "corrupt-occupied ";',
             "if (strncmp(action, corrupt_prefix"),
            ("if (strncmp(action, corrupt_prefix",
             'if (strcmp(action, "unsafe-reset CONFIRM")'),
        )
        for start_marker, end_marker in sections:
            start = body.index(start_marker)
            end = body.index(end_marker, start + len(start_marker))
            command = body[start:end]
            self.assertIn("_store->hasPendingContactWrites()", command)
            self.assertIn("_store->hasIncompleteContactLoad()", command)


if __name__ == "__main__":
    unittest.main()
