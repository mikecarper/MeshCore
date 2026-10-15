# Narrow bench entry to firmware.cmd's actual ESP32 install/update/reset helpers.
# Import definitions only: never execute its menus, dependency installers,
# background upgrades, backup prompts, or top-level cleanup.
[CmdletBinding()]
param(
    [ValidateSet('Inventory', 'Probe', 'Install', 'AppUpdate')][string]$Mode = 'Inventory',
    [Parameter(Mandatory)][ValidatePattern('^COM[0-9]+$')][string]$ComPort,
    [Parameter(Mandatory)][string]$ExpectedUsbInstanceId,
    [string]$ExpectedLocationPath = '',
    [string]$ExpectedMac = '',
    [string]$Image = '',
    [string]$ExpectedSha256 = '',
    [switch]$Erase,
    [string]$Flasher = 'C:\git\meshfirmware\firmware.cmd',
    [string]$PythonCommand = ''
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This bench wrapper requires Windows.'
}
$ComPort = $ComPort.ToUpperInvariant()
if ($ExpectedUsbInstanceId -notmatch '^USB\\VID_10C4&PID_EA60\\[^\\&]+$') {
    throw 'This narrow UART bench wrapper requires an exact CP210x USB instance ID with a serial.'
}
if ($Mode -eq 'Install') {
    if (-not $Erase -or -not [IO.Path]::IsPathRooted($Image) -or
        $ExpectedSha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        ($ExpectedMac -replace '[:\-]', '') -notmatch '^[0-9a-fA-F]{12}$') {
        throw 'Install requires -Erase, an absolute merged -Image, -ExpectedSha256 and the prior probe -ExpectedMac.'
    }
} elseif ($Mode -eq 'AppUpdate') {
    if ($Erase -or $Image -notmatch '^[A-Za-z]:[\\/]' -or
        $ExpectedSha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        ($ExpectedMac -replace '[:\-]', '') -notmatch '^[0-9a-fA-F]{12}$' -or
        [string]::IsNullOrWhiteSpace($ExpectedLocationPath)) {
        throw 'AppUpdate requires an absolute plain app -Image, -ExpectedSha256, prior probe -ExpectedMac and -ExpectedLocationPath; -Erase is forbidden.'
    }
} elseif ($Erase -or $Image -or $ExpectedSha256) {
    throw 'Inventory/Probe cannot erase, install, or select an image.'
}

$tokens = $null; $parseErrors = $null
$flasherPath = (Resolve-Path -LiteralPath $Flasher -ErrorAction Stop).ProviderPath
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $flasherPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw "firmware.cmd parse failed: $parseErrors" }
$required = @(
    'Get-AvailableComPorts', 'Test-IsWindowsHost', 'Resolve-UsbParentInstanceId',
    'Get-UsbInterfaceNumberFromInstanceId', 'Get-UsbIdentityInterfaceNumber',
    'Get-UsbPnpProperties', 'Get-UsbComPortIdentity', 'Test-UsbComPortIdentityMatch',
    'Get-UsbIdentityDisplayText', 'Resolve-UsbComPortForIdentity', 'Resolve-LiveUsbComPort',
    'Resolve-EspUsbComPort', 'Assert-UsbComPortIdentityFor1200Touch',
    'Get-EspUsbBootloaderStrategy', 'Get-Esp32RomBeforeMode', 'Get-Esp32RomSessionStamp',
    'Get-Esp32WriteAfterMode', 'Assert-Esp32FinishCapability', 'Get-EspRomMac',
    'Set-Esp32VerifiedChipMac', 'Assert-Esp32RomChipIdentity', 'Enter-Esp32Bootloader',
    'New-EspUsbTouchSerialPort', 'Invoke-EspTinyUsbTouch1200',
    'Split-CommandLine', 'run_cmd', 'get_esptool_cmd',
    'Wait-FirmwareToolUpdates', 'Enter-FirmwareToolUse', 'Exit-FirmwareToolUse',
    'Get-EspRuntimeStorageLayout', 'Complete-Esp32FlashSession',
    'Get-EspFileNameMode', 'Get-EspBinClassification', 'Get-EspFlashStrategy',
    'Install-SimpleMergedEspImage'
)
if ($Mode -eq 'AppUpdate') {
    $required += @('Get-SelectedUsbIdentityForFlash', 'Read-EspPartitionSnapshot',
        'Get-EspOtadataSnapshot', 'Get-EspNvsSnapshot', 'updateFlashViaEspTool')
}
foreach ($name in $required) {
    $definitions = @($ast.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq $name
    })
    if ($definitions.Count -ne 1) { throw "Expected exactly one firmware.cmd function: $name" }
    Invoke-Expression $definitions[0].Extent.Text
}
# This is the sole non-function initializer needed by run_cmd. Select the
# exact existing interop block, not arbitrary executable top-level statements.
$interop = @($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.IfStatementAst] -and
    $_.Extent.Text -match 'public static extern IntPtr CommandLineToArgvW' -and
    $_.Extent.Text -match 'public static class NativeMethods'
})
if ($interop.Count -ne 1) { throw 'Missing/ambiguous firmware.cmd native command-line interop.' }
Invoke-Expression $interop[0].Extent.Text

function Assert-BenchAppImage {
    param([string]$Path, [string]$Sha256)
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).ProviderPath
    $item = Get-Item -LiteralPath $resolved -ErrorAction Stop
    if ($resolved -match '[\s"]' -or $item.PSIsContainer -or
        $item.Extension -ine '.bin' -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'AppUpdate requires a regular plain .bin file without whitespace, quotes or reparse points.'
    }
    $strategy = Get-EspFlashStrategy -Path $resolved
    if ($strategy.Classification -ne 'LIKELY_SINGLE' -or $strategy.ClassifiedMode -ne 'update') {
        throw 'AppUpdate accepts only a content-classified single application, never a merged/data/unknown image.'
    }
    if ((Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash -ne $Sha256) {
        throw 'The selected application does not match the explicitly expected SHA-256.'
    }
    return $resolved
}

function Get-BenchPartitionFailureReason {
    param([object]$PlannerError, [bool]$MigrationNeeded)
    # Only fixed reviewed messages may cross the private-snapshot boundary.
    # Never echo arbitrary subprocess output, exception text, paths or NVS data.
    $known = @(
        'A required partition expansion input is missing or too large.',
        'Cannot read a required partition expansion input.',
        'ESP32 flash identification output is not valid text.',
        'Physical ESP32 chip and flash capacity were not unambiguously detected.',
        'This ESP32 chip family has no reviewed partition expansion bridge.',
        'Partition expansion requires a valid ESP32 application-only image.',
        'ESP32 image declares an unsupported flash capacity.',
        'Selected application does not match the connected ESP32 chip/flash capacity.',
        'Partition table must contain exactly 4096 bytes.',
        'Partition table has malformed trailing data.',
        'Partition table MD5 verification failed.',
        'Partition table has entries after its checksum.',
        'Partition table has an invalid entry.',
        'Partition table has an invalid label.',
        'Partition table contains invalid, duplicate or out-of-flash geometry.',
        'Partition table is empty.',
        'Partition table contains overlapping partitions.',
        'OTA metadata read must contain exactly 8192 bytes.',
        'OTA table does not have unique contiguous OTA slot identities.',
        'OTA metadata has no valid active sequence and is not erased initial data.',
        'OTA active sequence ordering is ambiguous.',
        'ESP32 table has ambiguous NVS partitions for migration-state verification.',
        'An actual NVS snapshot is required to check for unfinished partition migration.',
        'NVS snapshot size does not match its actual partition geometry.',
        'Migration NVS page verification failed or requires recovery.',
        'Migration NVS has missing or ambiguous page ordering.',
        'Migration NVS has multiple active pages and requires recovery.',
        'Migration NVS sequence ordering is ambiguous.',
        'Migration NVS contains an invalid entry state.',
        'Migration NVS entry CRC verification failed.',
        'Migration NVS contains an invalid key.',
        'Migration NVS entry format is invalid or unsupported.',
        'Migration NVS primitive entry geometry is invalid.',
        'Migration NVS variable-length entry is incomplete.',
        'Migration NVS data CRC verification failed.',
        'No verified partition migration NVS namespace exists.',
        'Partition migration NVS namespace identity is ambiguous.',
        'Partition migration NVS completion flag has an invalid type/value.',
        'Live NVS reports unfinished partition migration; restore the original bridge handoff before final flashing.',
        'Device cache contains invalid partition expansion state.',
        'Device has ambiguous pending partition expansion state.',
        'A partition expansion is pending; select its original exact firmware before updating another image.',
        'Pending partition expansion belongs to another physical device.',
        'Pending partition expansion layout changed unexpectedly; recovery is required.',
        'Bench app-only validation is offline; partition migration is forbidden.',
        'Partition expansion preparation or verification failed.'
    )
    if ($PlannerError -is [string] -and $known -ccontains $PlannerError) { return $PlannerError }
    if ($MigrationNeeded) { return 'Partition migration is required or pending.' }
    return 'Local partition validation failed; unrecognized details were withheld.'
}

function Get-BenchAppUpdatePlan {
    param([psobject]$Hardware, [string]$EspToolCommand, [string]$FlasherPath,
        [string]$ExpectedImageSha256)
    # Use the exact local flasher validator, never its resolver/downloader or
    # migration driver. The precheck below makes its expansion branch unreachable.
    $tool = Join-Path (Split-Path $FlasherPath -Parent) 'tools\meshcore_partition_expansion.py'
    $toolSha256 = 'fd1cc2c2198d31878587a2a7aa48f7ec49055b29af3cb5f2a9630ec2d8787df8'
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf) -or
        (Get-FileHash -LiteralPath $tool -Algorithm SHA256).Hash -ine $toolSha256) {
        throw 'The required local partition validator is missing or not the reviewed version; no download or update is allowed.'
    }
    $temporaryPath = Join-Path ([IO.Path]::GetTempPath()) ('meshcore-bench-app-' + [guid]::NewGuid().ToString('N'))
    $tempDir = (New-Item -ItemType Directory -Path $temporaryPath -ErrorAction Stop).FullName
    $identity = $Hardware.UsbIdentity
    try {
        $port = Enter-Esp32Bootloader -ComPort $Hardware.ComPort -UsbIdentity $identity -EspToolCommand $EspToolCommand
        $Hardware.ComPort = $port
        $tablePath = Join-Path $tempDir 'partitions.bin'
        $flashPath = Join-Path $tempDir 'flash-id.txt'
        $otaPath = Join-Path $tempDir 'otadata.bin'
        $nvsPath = Join-Path $tempDir 'nvs.bin'
        Read-EspPartitionSnapshot -ComPort $port -UsbIdentity $identity -EspToolCommand $EspToolCommand `
            -Offset '0x8000' -Length 4096 -Path $tablePath
        $table = [IO.File]::ReadAllBytes($tablePath)
        if ($table.Length -ne 4096) { throw 'AppUpdate requires a complete physical partition table.' }
        $imageBytes = (Get-Item -LiteralPath $Hardware.FirmwareFile).Length
        $physicalAppOffsets = @()
        for ($offset = 0; $offset -lt 4096; $offset += 32) {
            if ($table[$offset] -eq 0xff -and $table[$offset + 1] -eq 0xff) { break }
            if ($table[$offset] -eq 0xeb -and $table[$offset + 1] -eq 0xeb) { break }
            if ($table[$offset] -ne 0xaa -or $table[$offset + 1] -ne 0x50) {
                throw 'The physical partition table is malformed; no application update is allowed.'
            }
            if ($table[$offset + 2] -eq 0) {
                $physicalAppOffsets += [int64][BitConverter]::ToUInt32($table, $offset + 4)
                if ($imageBytes -gt [BitConverter]::ToUInt32($table, $offset + 8)) {
                    throw 'The application does not fit every physical app slot; this mode never migrates partitions.'
                }
            }
        }
        if (-not $physicalAppOffsets.Count) { throw 'No physical application partitions were found.' }
        $port = Assert-Esp32RomChipIdentity -ComPort $port -UsbIdentity $identity `
            -EspToolCommand $EspToolCommand -Purpose 'App-only physical flash capacity'
        $before = Get-Esp32RomBeforeMode -UsbIdentity $identity
        $flashId = run_cmd "$EspToolCommand --baud 115200 --port $port --before $before --after $script:ESPTOOL_NO_RESET $script:ESPTOOL_FLASH_ID"
        [IO.File]::WriteAllText($flashPath, [string]$flashId)
        $ota = Get-EspOtadataSnapshot -PartitionTable $tablePath -ComPort $port -UsbIdentity $identity `
            -EspToolCommand $EspToolCommand -Path $otaPath
        $nvs = Get-EspNvsSnapshot -PartitionTable $tablePath -ComPort $port -UsbIdentity $identity `
            -EspToolCommand $EspToolCommand -Path $nvsPath
        # Reuse the flasher's per-MAC cache so interrupted migrations cannot be
        # hidden by choosing a new bench cache. Live NVS is independently checked.
        $cache = Join-Path (Split-Path $FlasherPath -Parent) ('.tmp\partition-expansion\' + $identity.Esp32ChipMac)
        # Keep the pinned planner offline even if an input changes between the
        # size precheck and its own read. Never execute its serial/bridge commands.
        $offlineChecker = @'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bench_partition_validator", sys.argv[1])
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
def refuse_download(*args, **kwargs):
    raise validator.ExpansionError("Bench app-only validation is offline; partition migration is forbidden.")
validator.download = refuse_download
raise SystemExit(validator.main(sys.argv[2:]))
'@
        $arguments = @('-c', $offlineChecker, $tool, 'plan', '--firmware', $Hardware.FirmwareFile,
            '--partition-table', $tablePath, '--flash-id-output', $flashPath,
            '--output-dir', $cache, '--device-mac', $identity.Esp32ChipMac)
        if ($ota) { $arguments += @('--otadata', $ota) }
        if ($nvs) { $arguments += @('--nvs', $nvs) }
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $output = (& $script:pythonCommand @arguments 2>&1 | Out-String)
            $exitCode = $LASTEXITCODE
        } finally { $ErrorActionPreference = $previousPreference }
        try { $plan = $output | ConvertFrom-Json -ErrorAction Stop }
        catch { throw 'The local partition validator returned no valid app-update proof.' }
        if ($exitCode -ne 0 -or $plan.ok -ne $true -or $plan.needed -ne $false -or $plan.resume) {
            $reason = Get-BenchPartitionFailureReason -PlannerError $plan.error `
                -MigrationNeeded ($plan.needed -eq $true -or $plan.resume -eq $true)
            throw "Physical partition/NVS/OTA proof failed; no application was written. Reason: $reason"
        }
        if ($plan.firmware_sha256 -cne $ExpectedImageSha256.ToLowerInvariant() -or
            -not @($plan.app_offsets).Count -or @($plan.app_offsets).Count -ne $physicalAppOffsets.Count) {
            throw 'The partition proof does not match the exact application and all physical app slots.'
        }
        $offsets = @($plan.app_offsets | ForEach-Object {
            $value = [int64]$_
            if ($value -lt 0x10000 -or $value % 0x10000 -ne 0 -or $value -notin $physicalAppOffsets) {
                throw 'Invalid verified application offset.'
            }
            '0x{0:x}' -f $value
        })
        if (@($offsets | Select-Object -Unique).Count -ne $offsets.Count) { throw 'Duplicate application offsets in proof.' }
        $Hardware | Add-Member -NotePropertyName EspUpdateFirmwareSha256 -NotePropertyValue $plan.firmware_sha256 -Force
        $Hardware | Add-Member -NotePropertyName EspUpdateOffsets -NotePropertyValue ([string[]]$offsets) -Force
        $Hardware | Add-Member -NotePropertyName EspUpdateNvsProof -NotePropertyValue ([pscustomobject]@{
            namespace_exists = ($plan.nvs_migration.namespace_exists -eq $true)
            namespace_empty = ($plan.nvs_migration.namespace_empty -eq $true)
            restored = ($plan.nvs_migration.'cfg-restored' -eq $true)
            config_pending = ($plan.nvs_migration.'cfg-pending' -eq $true)
            identity_pending = ($plan.nvs_migration.'id-pending' -eq $true)
        }) -Force
        return $offsets
    } finally {
        # NVS may contain keys/bonds. Delete only our exact snapshots, never log
        # their bytes and never retain them as ordinary bench evidence.
        foreach ($name in @('partitions.bin', 'flash-id.txt', 'otadata.bin', 'nvs.bin')) {
            $path = Join-Path $tempDir $name
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
        }
        Remove-Item -LiteralPath $tempDir -Force
    }
}

function Assert-BenchUsb {
    $device = @(Get-CimInstance Win32_PnPEntity | Where-Object {
        $_.PNPDeviceID -eq $ExpectedUsbInstanceId -and $_.Present -ne $false -and
        [int]$_.ConfigManagerErrorCode -eq 0 -and $_.Name -match "\($([regex]::Escape($ComPort))\)$"
    })
    if ($device.Count -ne 1) {
        throw 'The exact selected CP210x instance is not healthy on the requested COM port.'
    }
    $identity = Get-UsbComPortIdentity -ComPort $ComPort -PnpDevice $device[0]
    if ($null -eq $identity -or $identity.ParentInstanceId -ne $ExpectedUsbInstanceId -or
        [string]::IsNullOrWhiteSpace([string]$identity.LocationPath)) {
        throw 'The selected USB parent/location could not be captured; refusing a reset or write.'
    }
    if ($ExpectedLocationPath -and $identity.LocationPath -ne $ExpectedLocationPath) {
        throw 'The selected USB physical location changed; refusing a reset or write.'
    }
    if ((Get-EspUsbBootloaderStrategy -UsbIdentity $identity) -ne 'uart') {
        throw 'The selected CP210x is not recognized as a UART transport by firmware.cmd.'
    }
    return $identity
}
$identity = Assert-BenchUsb
if ($Mode -eq 'Inventory') {
    [pscustomobject]@{
        Mode = $Mode; ComPort = $ComPort; UsbInstanceId = $identity.ParentInstanceId
        LocationPath = $identity.LocationPath; UsbSerial = $identity.SerialNumber
        Description = $identity.BusReportedDescription; HardwareOpened = $false
    } | ConvertTo-Json
    return
}
$ExpectedLocationPath = [string]$identity.LocationPath
if ($ExpectedMac) {
    $identity | Add-Member -NotePropertyName Esp32ChipMac `
        -NotePropertyValue ($ExpectedMac -replace '[:\-]', '').ToUpperInvariant() -Force
}
if ($Mode -eq 'Install') {
    $imagePath = (Resolve-Path -LiteralPath $Image -ErrorAction Stop).ProviderPath
    # firmware.cmd's native streaming adapter currently reparses its arguments.
    # Avoid its whitespace-path ambiguity rather than risking a different file.
    if ($imagePath -match '[\s"]' -or (Get-Item -LiteralPath $imagePath).PSIsContainer) {
        throw 'Use an unambiguous file path without whitespace or double quotes for this bench install.'
    }
    $strategy = Get-EspFlashStrategy -Path $imagePath
    if ($strategy.Classification -ne 'LIKELY_MERGED' -or $strategy.ClassifiedMode -ne 'install') {
        throw 'Only a content-classified merged ESP32 image can be installed by this wrapper.'
    }
    if ((Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash -ne $ExpectedSha256) {
        throw 'The selected merged image does not match the explicitly expected SHA-256.'
    }
} elseif ($Mode -eq 'AppUpdate') {
    $imagePath = Assert-BenchAppImage -Path $Image -Sha256 $ExpectedSha256
}

# Match firmware.cmd's per-user tool lock, without starting an updater or
# installing dependencies. Its helpers observe this already-held mutex.
$script:FirmwareToolUpdateJob = $null
$script:FirmwareToolUseLocked = $false
$account = [Security.Principal.WindowsIdentity]::GetCurrent()
try { $mutexName = "Local\MeshFirmwareTools-$($account.User.Value)" }
finally { $account.Dispose() }
$script:FirmwareToolUseMutex = New-Object Threading.Mutex($false, $mutexName)
try {
    try { $script:FirmwareToolUseLocked = $script:FirmwareToolUseMutex.WaitOne(15000) }
    catch [Threading.AbandonedMutexException] { $script:FirmwareToolUseLocked = $true }
    if (-not $script:FirmwareToolUseLocked) { throw 'Firmware tools are busy in another process.' }
    if (-not $PythonCommand) { $PythonCommand = (Get-Command python.exe -ErrorAction Stop).Source }
    $script:pythonCommand = $PythonCommand
    $esptool = get_esptool_cmd
    $fresh = Assert-BenchUsb
    if ($fresh.LocationPath -ne $identity.LocationPath) { throw 'USB location changed before handoff.' }
    if ($Mode -eq 'Probe') {
        $livePort = Enter-Esp32Bootloader -ComPort $ComPort -UsbIdentity $identity -EspToolCommand $esptool
        $null = Assert-BenchUsb
        $before = Get-Esp32RomBeforeMode -UsbIdentity $identity
        $flashInfo = run_cmd "$esptool --baud 115200 --port $livePort --before $before --after $script:ESPTOOL_NO_RESET $script:ESPTOOL_FLASH_ID"
        $livePort = Complete-Esp32FlashSession -ComPort $livePort -UsbIdentity $identity -SkipApplicationVerification
        [pscustomobject]@{
            Mode = $Mode; ComPort = $livePort; UsbInstanceId = $identity.ParentInstanceId
            LocationPath = $identity.LocationPath; ChipMac = $identity.Esp32ChipMac
            FlashInfo = $flashInfo; FlashWritten = $false; ApplicationVerified = $false
        } | ConvertTo-Json
    } elseif ($Mode -eq 'AppUpdate') {
        $hardware = [pscustomobject]@{ ComPort = $ComPort; FirmwareFile = $imagePath; UsbIdentity = $identity }
        $offsets = @(Get-BenchAppUpdatePlan -Hardware $hardware -EspToolCommand $esptool `
            -FlasherPath $flasherPath -ExpectedImageSha256 $ExpectedSha256)
        $null = Assert-BenchUsb
        $null = Assert-BenchAppImage -Path $imagePath -Sha256 $ExpectedSha256
        if (-not (updateFlashViaEspTool -hw $hardware)) {
            throw 'firmware.cmd did not confirm the checked application-only write.'
        }
        $livePort = Complete-Esp32FlashSession -ComPort $hardware.ComPort -UsbIdentity $identity -SkipApplicationVerification
        [pscustomobject]@{
            Mode = $Mode; ComPort = $livePort; UsbInstanceId = $identity.ParentInstanceId
            LocationPath = $identity.LocationPath; ChipMac = $identity.Esp32ChipMac
            Image = $imagePath; Sha256 = $ExpectedSha256.ToLowerInvariant(); AppOffsets = $offsets
            NvsMigration = $hardware.EspUpdateNvsProof
            FlashWritten = $true; Erased = $false; PartitionTableWritten = $false
            ApplicationVerified = $false
        } | ConvertTo-Json
    } else {
        # No image substitution, backup prompt or update/partition migration:
        # call the real flasher's single merged-install path, including its
        # MAC rechecks before erase/write and qualified final hardware reset.
        $null = Assert-BenchUsb
        if ((Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash -ne $ExpectedSha256) {
            throw 'The selected image changed before the verified full install.'
        }
        if (-not (Install-SimpleMergedEspImage -ImagePath $imagePath -ComPort $ComPort -UsbIdentity $identity)) {
            throw 'firmware.cmd did not confirm the merged-image write.'
        }
        $null = Complete-Esp32FlashSession -ComPort $ComPort -UsbIdentity $identity -SkipApplicationVerification
        [pscustomobject]@{
            Mode = $Mode; ComPort = $ComPort; ChipMac = $identity.Esp32ChipMac
            Image = $imagePath; Sha256 = $ExpectedSha256.ToLowerInvariant()
            FlashWritten = $true; ApplicationVerified = $false
        } | ConvertTo-Json
    }
} finally {
    Exit-FirmwareToolUse
    if ($script:FirmwareToolUseMutex) { $script:FirmwareToolUseMutex.Dispose() }
}
