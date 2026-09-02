#!/usr/bin/env python3
"""Static contract for the low-level ESP32 OTA commit reboot handoff."""

from pathlib import Path
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


class OtaDevRebootGuardContractTest(unittest.TestCase):
    def test_slot_arm_never_reboots_inline(self):
        header = (ROOT / "src/helpers/ota/OtaApply.h").read_text()
        apply = (ROOT / "src/helpers/ota/OtaApply.cpp").read_text()

        self.assertIn("bool ota_apply_arm();", header)
        arm = function_body(apply, "bool ota_apply_arm()")
        self.assertIn("esp_ota_set_boot_partition", arm)
        self.assertNotIn("esp_restart", arm)
        self.assertNotIn("ota_apply_commit", apply)

    def test_dev_commit_arms_the_guarded_mesh_reboot(self):
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        context = (ROOT / "src/helpers/ota/OtaContext.h").read_text()
        mesh = (ROOT / "src/Mesh.cpp").read_text()

        dev = function_body(
            cli,
            "static bool handle_dev(const char* d, char* reply, OtaContext& c) {",
        )
        commit_at = dev.index('strncmp(sub, "commit", 6)')
        commit = dev[commit_at : dev.index("} else {", commit_at)]
        arm_at = commit.index("ota_apply_arm()")
        pending_at = commit.index("c.apply_pending = true")
        self.assertLess(arm_at, pending_at)
        self.assertIn("c.bootloader_apply_pending = false", commit)
        self.assertIn("c.apply_at = 0", commit)
        self.assertIn("c.apply_hard = 0", commit)
        self.assertNotIn("ota_reboot_to_apply", commit)
        self.assertNotIn("esp_restart", commit)

        self.assertIn("bool     apply_pending = false;", context)
        maintenance = function_body(
            mesh, "void __attribute__((noinline)) Mesh::serviceLoopMaintenance()"
        )
        guard_at = maintenance.index("prepareForOtaReboot()")
        reboot_at = maintenance.index("ota::ota_reboot_to_apply()")
        self.assertLess(guard_at, reboot_at)


if __name__ == "__main__":
    unittest.main()
