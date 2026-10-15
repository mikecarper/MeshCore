"""Run the wrapper against a fake flasher and /dev/null, never a real radio."""

import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "tools/hil/mcfirmware_bench_flash.sh"
WINDOWS_WRAPPER = ROOT / "tools/hil/firmware_bench_flash.ps1"
WINDOWS_FLASHER = Path("C:/git/meshfirmware/firmware.cmd")
PWSH = shutil.which("pwsh") or shutil.which("powershell")

FAKE_FLASHER = r'''
set -euo pipefail
cleanup() { printf 'cleanup:%s\n' "$FIRMWARE_ROOT" >>"$TEST_LOG"; }
error_handler() { printf 'error\n' >>"$TEST_LOG"; exit 1; }
trap 'echo UNWANTED_TRAP >>"$TEST_LOG"' EXIT
initialize_privilege_mode() { echo UNWANTED_GROUP_CHANGE >>"$TEST_LOG"; }
meshfirmware_check_pi_usb_host_speed() { echo UNWANTED_REBOOT >>"$TEST_LOG"; }
configure_usb_autosuspend() { echo UNWANTED_USB_CHANGE >>"$TEST_LOG"; }
initialize_privilege_mode
meshfirmware_check_pi_usb_host_speed
configure_usb_autosuspend
FIRMWARE_ROOT=/unrelated/cache
NORESET=no-reset
ERASEFLASH=erase-flash
WRITEFLASH=write-flash
WATCHDOGRESET=watchdog-reset
READMAC=read-mac
BOOTLOADER_PROBE_ACTIVE=0
ESP32_FLASH_EXPECTED_MAC=''
save_selected_serial_port() { echo save-selected >>"$TEST_LOG"; DEVICE_PORT="$1"; }
prepare_esp32_flash_session() { echo prepare >>"$TEST_LOG"; ESP32_FLASH_EXPECTED_MAC=aabbccddeeff; ESP32_SESSION_IS_S3="${TEST_CHIP_IS_S3:-1}"; }
esp32_verified_destructive_port() { echo verify-MAC >>"$TEST_LOG"; printf '%s\n' "$1"; }
run_esp32_session_esptool() { printf 'run:%s\n' "$*" >>"$TEST_LOG"; }
finish_esp32_flash_session() { echo finish >>"$TEST_LOG"; }
esp32_prepare_merged_ota_mirror() { printf 'app' >"$2"; echo 0x650000; }
esp_firmware_layout() { echo merged; }
esp32_validate_image_for_device() { echo validate >>"$TEST_LOG"; }
esp32_write_after_mode() { echo no-reset; }
ensure_pipx_uv_backend() { echo backend >>"$TEST_LOG"; }
get_espcmd() { echo espcmd >>"$TEST_LOG"; }
# MAIN
echo UNWANTED_MENU >>"$TEST_LOG"
'''


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "POSIX bash required")
class BenchFlasherTests(unittest.TestCase):
    def run_fake(self, mode, mismatch=False, s3=True):
        with tempfile.TemporaryDirectory(prefix="meshcore-flasher-wrapper-") as directory:
            work = Path(directory)
            flasher = work / "fake.sh"
            flasher.write_text(FAKE_FLASHER)
            log = work / "actions.log"
            bindir = work / "bin"
            bindir.mkdir()
            readlink = bindir / "readlink"
            readlink.write_text("#!/bin/sh\nif [ -n \"$TEST_MISMATCH\" ]; then case \"$*\" in *by-id*) echo /dev/zero;exit 0;;esac;fi\necho /dev/null\n")
            readlink.chmod(0o755)
            image = work / "test-merged.bin"
            image.write_bytes(b"fake fixture only")
            env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"],
                       TEST_LOG=str(log), TEST_MISMATCH="1" if mismatch else "",
                       TEST_CHIP_IS_S3="1" if s3 else "0")
            args = ["bash", str(WRAPPER), "--flasher", str(flasher), "--bench-dir", str(work),
                    "--port", "/dev/null", "--usb-path", "exact-bench-usb-path",
                    "--usb-id", "exact-bench-usb-id", "--device", "heltec_v4"]
            if mode == "probe":
                args.append("--probe")
            elif mode == "watchdog":
                args.append("--watchdog-recover")
            elif mode == "mixed":
                args.extend(("--probe", "--watchdog-recover"))
            else:
                args.extend(("--image", str(image)))
                if mode == "erase":
                    args.append("--erase")
            result = subprocess.run(args, text=True, capture_output=True, env=env)
            actions = log.read_text() if log.exists() else ""
            return result, actions

    def test_probe_uses_flasher_and_skips_global_startup_and_erase(self):
        result, actions = self.run_fake("probe")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ("save-selected", "prepare", "chip-id", "flash-id", "finish", "cleanup:"):
            self.assertIn(expected, actions)
        for forbidden in ("UNWANTED", "erase-flash", "write-flash", "/unrelated/cache"):
            self.assertNotIn(forbidden, actions)
        self.assertIn("/mcfirmware-cache.", actions)

    def test_merged_install_requires_explicit_erase(self):
        result, actions = self.run_fake("image")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(actions, "")

    def test_install_runs_verified_session_with_dual_slot_mirror(self):
        result, actions = self.run_fake("erase")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("erase-flash", actions)
        self.assertIn("write-flash 0x0000", actions)
        self.assertIn("0x650000", actions)
        self.assertIn("finish", actions)
        self.assertNotIn("UNWANTED", actions)
        self.assertLess(actions.index("prepare"), actions.index("erase-flash"))

    def test_mismatched_usb_ids_fail_before_flashing_or_startup(self):
        result, actions = self.run_fake("probe", mismatch=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identities disagree", result.stderr)
        self.assertEqual(actions, "")

    def test_watchdog_recovery_is_s3_only_masked_and_mac_verified(self):
        result, actions = self.run_fake("watchdog")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("write-mem 0x6000812c 0 0x1", actions)
        self.assertIn("--after watchdog-reset --baud 115200 read-mac", actions)
        self.assertEqual(actions.count("verify-MAC"), 2)
        self.assertLess(actions.index("verify-MAC"), actions.index("write-mem"))
        for forbidden in ("erase-flash", "write-flash", "finish", "UNWANTED"):
            self.assertNotIn(forbidden, actions)
        self.assertIn("Runtime is not yet verified", result.stdout)

    def test_watchdog_recovery_refuses_non_s3_before_volatile_write(self):
        result, actions = self.run_fake("watchdog", s3=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("positively identified ESP32-S3", result.stderr)
        self.assertNotIn("write-mem", actions)
        self.assertNotIn("--after watchdog-reset", actions)

    def test_watchdog_recovery_cannot_be_combined_with_probe(self):
        result, actions = self.run_fake("mixed")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(actions, "")


WINDOWS_APP_HARNESS = r'''
$ErrorActionPreference = 'Stop'
$case = $env:BENCH_TEST_CASE
$work = $env:BENCH_TEST_WORK
$actions = [Collections.Generic.List[string]]::new()
function Add-Action([string]$value) { $script:actions.Add($value) }
function Import-Definition([string]$path, [string[]]$names) {
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Source parse failure' }
    foreach ($name in $names) {
        $definitions = @($ast.EndBlock.Statements | Where-Object {
            $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq $name
        })
        if ($definitions.Count -ne 1) { throw "Missing unique function: $name" }
        # Install only the named definition in script scope, never top-level source.
        . ([scriptblock]::Create(($definitions[0].Extent.Text -replace '^function ', 'function script:')))
    }
}
Import-Definition $env:BENCH_TEST_WRAPPER @('Assert-BenchAppImage', 'Get-BenchPartitionFailureReason', 'Get-BenchAppUpdatePlan', 'Assert-BenchUsb')
Import-Definition $env:BENCH_TEST_FLASHER @('updateFlashViaEspTool')
$script:pythonCommand = 'Invoke-FakeValidator'
$script:ESPTOOL_NO_RESET = 'no-reset'
$script:ESPTOOL_FLASH_ID = 'flash-id'
$script:ESPTOOL_WRITE_FLASH = 'write-flash'
$ExpectedUsbInstanceId = 'USB\VID_10C4&PID_EA60\0001'
$ExpectedLocationPath = 'PCIROOT(0)#USBROOT(0)#USB(7)'
$ComPort = 'COM6'
$identity = [pscustomobject]@{
    ParentInstanceId = $ExpectedUsbInstanceId; LocationPath = $ExpectedLocationPath
    SerialNumber = '0001'; Esp32ChipMac = '3C0F02EE20DC'
}
function Get-CimInstance { @([pscustomobject]@{
    PNPDeviceID=$ExpectedUsbInstanceId; Present=$true; ConfigManagerErrorCode=0; Name='USB (COM6)'
}) }
function Get-UsbComPortIdentity {
    param($ComPort, $PnpDevice)
    $result = $identity.PSObject.Copy()
    if ($case -eq 'location') { $result.LocationPath = 'OTHER-PORT' }
    if ($case -eq 'identity') { $result.ParentInstanceId = 'USB\VID_10C4&PID_EA60\OTHER' }
    return $result
}
function Get-EspUsbBootloaderStrategy { param($UsbIdentity) 'uart' }
function Get-EspFlashStrategy {
    param($Path)
    if ($case -eq 'merged') { return [pscustomobject]@{Classification='LIKELY_MERGED';ClassifiedMode='install'} }
    if ($case -eq 'unknown') { return [pscustomobject]@{Classification='UNKNOWN';ClassifiedMode='unknown'} }
    [pscustomobject]@{Classification='LIKELY_SINGLE';ClassifiedMode='update'}
}
function Enter-Esp32Bootloader { param($ComPort,$UsbIdentity,$EspToolCommand) Add-Action 'boot'; $ComPort }
function Assert-Esp32RomChipIdentity {
    param($ComPort,$UsbIdentity,$EspToolCommand,$Purpose)
    Add-Action 'verify-mac'
    if ($case -eq 'mac') { throw 'MAC changed' }
    $ComPort
}
function Get-Esp32RomBeforeMode { param($UsbIdentity) 'default-reset' }
function Get-Esp32WriteAfterMode { param($UsbIdentity) 'hard-reset' }
function Resolve-EspUsbComPort { param($PreferredComPort,$UsbIdentity,$TimeoutMs,$Purpose) $PreferredComPort }
function Get-SelectedUsbIdentityForFlash { param($Hardware) $Hardware.UsbIdentity }
function get_esptool_cmd { 'fake-esptool' }
function run_cmd {
    param($CommandLine,[switch]$Stream)
    Add-Action $CommandLine
    if ($Stream) { return 0 }
    'Detecting chip type... ESP32-S3; Detected flash size: 8MB'
}
function Read-EspPartitionSnapshot {
    param($ComPort,$UsbIdentity,$EspToolCommand,$Offset,$Length,$Path)
    Add-Action 'read-table'
    $script:snapshotDir = Split-Path $Path -Parent
    $bytes = [byte[]]::new(4096)
    for ($i=0; $i -lt $bytes.Length; $i++) { $bytes[$i]=255 }
    foreach ($entry in @(@(0,0x10000),@(32,0x30000))) {
        $o=$entry[0]; $bytes[$o]=0xaa; $bytes[$o+1]=0x50; $bytes[$o+2]=0
        [BitConverter]::GetBytes([uint32]$entry[1]).CopyTo($bytes,$o+4)
        [BitConverter]::GetBytes([uint32]0x20000).CopyTo($bytes,$o+8)
    }
    if ($case -eq 'short-table') { $bytes=[byte[]]::new(64) }
    if ($case -eq 'bad-table') { $bytes[0]=0 }
    [IO.File]::WriteAllBytes($Path,$bytes)
}
function Get-EspOtadataSnapshot {
    param($PartitionTable,$ComPort,$UsbIdentity,$EspToolCommand,$Path)
    Add-Action 'read-ota'; [IO.File]::WriteAllBytes($Path,[byte[]]::new(8192)); $Path
}
function Get-EspNvsSnapshot {
    param($PartitionTable,$ComPort,$UsbIdentity,$EspToolCommand,$Path)
    Add-Action 'read-nvs'; [IO.File]::WriteAllBytes($Path,[byte[]]::new(4096)); $Path
}
function Invoke-FakeValidator {
    Add-Action ('validator:' + ($args -join ' '))
    $global:LASTEXITCODE=0
    $proof=[ordered]@{ok=$true;needed=$false;firmware_sha256=$script:hash;app_offsets=@(0x30000,0x10000)
        nvs_migration=@{namespace_exists=$true;namespace_empty=$true;'cfg-restored'=$false;'cfg-pending'=$false;'id-pending'=$false}}
    if ($case -eq 'pending') { $proof.needed=$true; $proof.resume=$true }
    if ($case -eq 'proof-hash') { $proof.firmware_sha256='0'*64 }
    if ($case -eq 'duplicate') { $proof.app_offsets=@(0x10000,0x10000) }
    if ($case -eq 'missing-slot') { $proof.app_offsets=@(0x10000) }
    if ($case -eq 'invalid-offset') { $proof.app_offsets=@(0x8000,0x10000) }
    if ($case -eq 'validator-error') { $global:LASTEXITCODE=1; $proof.ok=$false }
    if ($case -eq 'nvs-error') {
        $global:LASTEXITCODE=1; $proof.ok=$false; $proof.error='Migration NVS entry format is invalid or unsupported.'
    }
    if ($case -eq 'private-error') {
        $global:LASTEXITCODE=1; $proof.ok=$false; $proof.error='PRIVATE-SSID key=private-value C:\secret\snapshot.bin'
    }
    $proof | ConvertTo-Json -Compress
}
$success=$false; $message=''; $offsets=@()
try {
    $null=Assert-BenchUsb
    $image=Join-Path $work 'application.bin'
    $bytes=if ($case -eq 'oversized') {[byte[]]::new(0x20001)} else {[byte[]]::new(100)}
    [IO.File]::WriteAllBytes($image,$bytes)
    $script:hash=(Get-FileHash -LiteralPath $image -Algorithm SHA256).Hash.ToLowerInvariant()
    $expectedHash=if ($case -eq 'image-hash') {'0'*64} else {$script:hash}
    $image=Assert-BenchAppImage -Path $image -Sha256 $expectedHash
    $hardware=[pscustomobject]@{ComPort='COM6';FirmwareFile=$image;UsbIdentity=$identity}
    $offsets=@(Get-BenchAppUpdatePlan -Hardware $hardware -EspToolCommand 'fake-esptool' `
        -FlasherPath (Join-Path $work 'firmware.cmd') -ExpectedImageSha256 $expectedHash)
    if ($case -eq 'changed-image') { [IO.File]::AppendAllText($image,'changed') }
    $null=Assert-BenchAppImage -Path $image -Sha256 $expectedHash
    $success=updateFlashViaEspTool -hw $hardware
} catch { $message=$_.Exception.Message }
[pscustomobject]@{success=$success;error=$message;actions=@($actions.ToArray());offsets=$offsets
    nvs_proof=if ($hardware) {$hardware.EspUpdateNvsProof} else {$null}
    snapshots_removed=($script:snapshotDir -and -not (Test-Path -LiteralPath $script:snapshotDir))} | ConvertTo-Json -Compress
'''


@unittest.skipUnless(PWSH and WINDOWS_FLASHER.is_file(), "local PowerShell/flasher source required")
class WindowsAppUpdateTests(unittest.TestCase):
    """Only AST-loaded functions plus fake USB/ROM/validator operations run."""

    def run_case(self, case):
        with tempfile.TemporaryDirectory(prefix="meshcore-app-update-") as directory:
            work = Path(directory)
            (work / "tools").mkdir()
            shutil.copyfile(WINDOWS_FLASHER.parent / "tools/meshcore_partition_expansion.py",
                            work / "tools/meshcore_partition_expansion.py")
            if case == "helper-hash":
                (work / "tools/meshcore_partition_expansion.py").write_text("not the pinned helper")
            env = dict(os.environ, BENCH_TEST_CASE=case, BENCH_TEST_WORK=str(work),
                       BENCH_TEST_WRAPPER=str(WINDOWS_WRAPPER), BENCH_TEST_FLASHER=str(WINDOWS_FLASHER))
            result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                                     WINDOWS_APP_HARNESS], text=True, capture_output=True, env=env,
                                    timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])

    def test_checked_update_uses_real_flasher_app_writer_inactive_first(self):
        result = self.run_case("valid")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["offsets"], ["0x30000", "0x10000"])
        self.assertEqual(result["nvs_proof"], {"namespace_exists": True, "namespace_empty": True,
                         "restored": False, "config_pending": False, "identity_pending": False})
        actions = result["actions"]
        write = next(action for action in actions if "write-flash" in action)
        self.assertLess(write.index("0x30000"), write.index("0x10000"))
        self.assertTrue(result["snapshots_removed"])
        self.assertIn("read-ota", actions)
        self.assertIn("read-nvs", actions)
        self.assertLess(next(i for i, action in enumerate(actions) if action.startswith("validator:")),
                        actions.index(write))
        for forbidden in ("erase", "0x8000 ", "0x9000 ", "migration", "download"):
            self.assertNotIn(forbidden, write)

    def test_bad_image_identity_and_missing_pinned_helper_fail_before_rom(self):
        for case in ("location", "identity", "merged", "unknown", "image-hash", "helper-hash"):
            with self.subTest(case=case):
                result = self.run_case(case)
                self.assertFalse(result["success"], result)
                self.assertEqual(result["actions"], [])

    def test_bad_table_and_oversize_cannot_enter_validator_or_download_branch(self):
        for case in ("short-table", "bad-table", "oversized"):
            with self.subTest(case=case):
                result = self.run_case(case)
                self.assertFalse(result["success"], result)
                self.assertTrue(result["snapshots_removed"])
                self.assertFalse(any(action.startswith("validator:") for action in result["actions"]))
                self.assertFalse(any("write-flash" in action for action in result["actions"]))

    def test_bad_proofs_mac_and_changed_application_never_write(self):
        for case in ("pending", "proof-hash", "duplicate", "missing-slot", "invalid-offset",
                     "validator-error", "mac", "changed-image"):
            with self.subTest(case=case):
                result = self.run_case(case)
                self.assertFalse(result["success"], result)
                self.assertTrue(result["snapshots_removed"])
                self.assertFalse(any("write-flash" in action for action in result["actions"]))

    def test_wrapper_never_imports_migration_or_download_driver(self):
        text = WINDOWS_WRAPPER.read_text()
        self.assertIn("'AppUpdate'", text)
        self.assertIn("-ExpectedLocationPath; -Erase is forbidden", text)
        for forbidden in ("'Resolve-PartitionExpansionTool'", "'Invoke-EspPartitionExpansion'",
                          "'Invoke-PartitionExpansionTool'", "Invoke-WebRequest"):
            self.assertNotIn(forbidden, text)

    def test_planner_failure_reports_only_fixed_safe_reason(self):
        known = self.run_case("nvs-error")
        self.assertFalse(known["success"])
        self.assertIn("Reason: Migration NVS entry format is invalid or unsupported.", known["error"])
        self.assertTrue(known["snapshots_removed"])
        private = self.run_case("private-error")
        self.assertFalse(private["success"])
        self.assertIn("unrecognized details were withheld", private["error"])
        for secret in ("PRIVATE-SSID", "private-value", "snapshot.bin"):
            self.assertNotIn(secret, private["error"])
        self.assertTrue(private["snapshots_removed"])


if __name__ == "__main__":
    unittest.main()
