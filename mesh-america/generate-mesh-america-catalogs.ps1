$ErrorActionPreference = 'Stop'

$Repo = 'mikecarper/MeshCore'
$OutputDir = $PSScriptRoot
$Root = Split-Path -Parent $PSScriptRoot

$Catalogs = @(
    @{
        Name = 'non-logging'
        SourceDir = Join-Path $Root '.releases\non-logging'
        Tag = 'v1.16.0-halo-keymind-dev-28ee5d2c'
        Output = Join-Path $OutputDir 'keymind-cascade-v1.16.0-provider.json'
        Description = 'Keymind Cascade MeshCore v1.16.0 dev firmware with Halo/Keymind retry tuning and Cascade defaults. This is the standard build without extra logging.'
    },
    @{
        Name = 'logging'
        SourceDir = Join-Path $Root '.releases\logging'
        Tag = 'logging-v1.16.0-halo-keymind-dev-28ee5d2c'
        Output = Join-Path $OutputDir 'keymind-cascade-logging-v1.16.0-provider.json'
        Description = 'Keymind Cascade MeshCore v1.16.0 dev firmware with Halo/Keymind retry tuning, Cascade defaults, and extra logging enabled for diagnostics.'
    }
)

$RoleDefinitions = [ordered]@{
    companionSerial = [ordered]@{ name = 'Companion Serial' }
    companionWifi = [ordered]@{ name = 'Companion WiFi' }
    repeaterBridgeEspnow = [ordered]@{ name = 'Repeater Bridge ESP-NOW' }
    repeaterBridgeRs232 = [ordered]@{ name = 'Repeater Bridge RS232' }
    sensor = [ordered]@{ name = 'Sensor' }
    terminalChat = [ordered]@{ name = 'Terminal Chat' }
}

$RolePatterns = @(
    @{ Suffix = 'logging_repeater_bridge_espnow'; Role = 'repeaterBridgeEspnow'; Title = 'Repeater Bridge ESP-NOW'; SubTitle = 'Logging' },
    @{ Suffix = 'logging_repeater'; Role = 'repeater'; Title = 'Repeater'; SubTitle = 'Logging' },
    @{ Suffix = 'companion_radio_ble_femoff'; Role = 'companionBle'; Title = 'Companion BLE'; SubTitle = 'FEM off' },
    @{ Suffix = 'companion_radio_ble_femon'; Role = 'companionBle'; Title = 'Companion BLE'; SubTitle = 'FEM on' },
    @{ Suffix = 'companion_radio_ble_ps_femoff'; Role = 'companionBle'; Title = 'Companion BLE'; SubTitle = 'Power saving, FEM off' },
    @{ Suffix = 'companion_radio_ble_ps_femon'; Role = 'companionBle'; Title = 'Companion BLE'; SubTitle = 'Power saving, FEM on' },
    @{ Suffix = 'companion_radio_ble_ps'; Role = 'companionBle'; Title = 'Companion BLE'; SubTitle = 'Power saving' },
    @{ Suffix = 'companion_radio_ble_'; Role = 'companionBle'; Title = 'Companion BLE'; SubTitle = $null },
    @{ Suffix = 'companion_radio_ble'; Role = 'companionBle'; Title = 'Companion BLE'; SubTitle = $null },
    @{ Suffix = 'companion_ble'; Role = 'companionBle'; Title = 'Companion BLE'; SubTitle = $null },
    @{ Suffix = 'companion_radio_usb_'; Role = 'companionUsb'; Title = 'Companion USB'; SubTitle = $null },
    @{ Suffix = 'companion_radio_usb'; Role = 'companionUsb'; Title = 'Companion USB'; SubTitle = $null },
    @{ Suffix = 'companion_usb'; Role = 'companionUsb'; Title = 'Companion USB'; SubTitle = $null },
    @{ Suffix = 'comp_radio_usb'; Role = 'companionUsb'; Title = 'Companion USB'; SubTitle = $null },
    @{ Suffix = 'companion_radio_serial'; Role = 'companionSerial'; Title = 'Companion Serial'; SubTitle = $null },
    @{ Suffix = 'companion_radio_wifi'; Role = 'companionWifi'; Title = 'Companion WiFi'; SubTitle = $null },
    @{ Suffix = 'repeater_bridge_rs232_serial1'; Role = 'repeaterBridgeRs232'; Title = 'Repeater Bridge RS232'; SubTitle = 'Serial 1' },
    @{ Suffix = 'repeater_bridge_rs232_serial2'; Role = 'repeaterBridgeRs232'; Title = 'Repeater Bridge RS232'; SubTitle = 'Serial 2' },
    @{ Suffix = 'repeater_bridge_rs232'; Role = 'repeaterBridgeRs232'; Title = 'Repeater Bridge RS232'; SubTitle = $null },
    @{ Suffix = 'repeater_bridge_espnow_'; Role = 'repeaterBridgeEspnow'; Title = 'Repeater Bridge ESP-NOW'; SubTitle = $null },
    @{ Suffix = 'repeater_bridge_espnow'; Role = 'repeaterBridgeEspnow'; Title = 'Repeater Bridge ESP-NOW'; SubTitle = $null },
    @{ Suffix = 'Repeater'; Role = 'repeater'; Title = 'Repeater'; SubTitle = $null },
    @{ Suffix = 'repeater_'; Role = 'repeater'; Title = 'Repeater'; SubTitle = $null },
    @{ Suffix = 'repeatr'; Role = 'repeater'; Title = 'Repeater'; SubTitle = $null },
    @{ Suffix = 'repeater'; Role = 'repeater'; Title = 'Repeater'; SubTitle = $null },
    @{ Suffix = 'room_server_'; Role = 'roomServer'; Title = 'Room Server'; SubTitle = $null },
    @{ Suffix = 'room_server'; Role = 'roomServer'; Title = 'Room Server'; SubTitle = $null },
    @{ Suffix = 'room_svr'; Role = 'roomServer'; Title = 'Room Server'; SubTitle = $null },
    @{ Suffix = 'terminal_chat'; Role = 'terminalChat'; Title = 'Terminal Chat'; SubTitle = $null },
    @{ Suffix = 'sensor'; Role = 'sensor'; Title = 'Sensor'; SubTitle = $null }
)

$DeviceNameOverrides = @{
    'GAT562_30S_Mesh_Kit' = 'GAT-IoT GAT562 30s'
    'GAT562_Mesh_Tracker_Pro' = 'GAT-IoT GAT562 Tracker'
    'Heltec_E213' = 'Heltec Vision Master E213'
    'Heltec_E290' = 'Heltec Vision Master E290'
    'Heltec_mesh_solar' = 'Heltec MeshSolar / MeshTower'
    'Heltec_t096' = 'Heltec Mesh Node T096'
    'Heltec_t1' = 'Heltec Mesh Node T1'
    'Heltec_t114' = 'Heltec T114'
    'Heltec_t114_without_display' = 'Heltec T114'
    'Heltec_T190' = 'Heltec T114'
    'heltec_tracker_v2' = 'Heltec Wireless Tracker v2'
    'heltec_v4' = 'Heltec v4'
    'heltec_v4_3' = 'Heltec v4'
    'heltec_v4_expansionkit' = 'Heltec v4 + Expansion Kit (Touch)'
    'heltec_v4_expansionkit_tft' = 'Heltec v4 + Expansion Kit (Touch)'
    'heltec_v4_tft' = 'Heltec v4'
    'Heltec_Wireless_Paper' = 'Heltec Heltec Wireless Paper'
    'ikoka_nano_nrf_22dbm' = 'Ikoka Nano'
    'ikoka_nano_nrf_30dbm' = 'Ikoka Nano'
    'ikoka_nano_nrf_33dbm' = 'Ikoka Nano'
    'ikoka_stick_nrf_22dbm' = 'Ikoka Stick'
    'ikoka_stick_nrf_30dbm' = 'Ikoka Stick'
    'ikoka_stick_nrf_33dbm' = 'Ikoka Stick'
    'KeepteenLT1' = 'Keepteen LT1'
    'LilyGo_T3S3_sx1262' = 'LilyGo T3 S3 (SX126x)'
    'LilyGo_T3S3_sx1276' = 'LilyGo T3 S3 (SX127x)'
    'LilyGo_TBeam_1W' = 'LilyGo T-Beam (SX1262)'
    'LilyGo_TDeck' = 'LilyGo T-Deck'
    'LilyGo_T-Echo-Lite' = 'LilyGo T-Echo Lite'
    'LilyGo_T-Echo-Lite_non_shell' = 'LilyGo T-Echo Lite'
    'LilyGo_TLora_V2_1_1_6' = 'LilyGo LoRa32 V2.1_1.6'
    'Mesh_pocket' = 'Heltec MeshPocket'
    'Nano_G2_Ultra' = 'UnitEng Nano G2 Ultra'
    'ProMicro' = 'ProMicro nrf52 (faketec)'
    'R1Neo' = 'Muzi Works R1 Neo'
    'RAK_3112' = 'RAK WisBlock 3112'
    'RAK_3401' = 'RAK WisMesh 1W Booster (3401 + 13302)'
    'RAK_4631' = 'RAK WisBlock / WisMesh (RAK 4631)'
    'SenseCap_Solar' = 'Seeed Studio SenseCAP Solar'
    'Station_G2' = 'UnitEng Station G2'
    'Station_G3_ESP32' = 'UnitEng/BQ Voyage Station G3'
    'T_Beam_S3_Supreme_SX1262' = 'LilyGo T-Beam Supreme (SX1262)'
    't1000e' = 'Seeed Studio SenseCAP T1000-E'
    'Tbeam_SX1262' = 'LilyGo T-Beam (SX1262)'
    'Tbeam_SX1276' = 'LilyGo T-Beam 1.2 (SX1276)'
    'ThinkNode_M1' = 'Elecrow ThinkNode M1'
    'ThinkNode_M2' = 'Elecrow ThinkNode M2'
    'ThinkNode_M3' = 'Elecrow ThinkNode M3'
    'ThinkNode_M5' = 'Elecrow ThinkNode M5'
    'ThinkNode_M6' = 'Elecrow ThinkNode M6'
    'waveshare_rp2040_lora' = 'RPI Pico 2040 + WaveShare SX1262'
    'WioTrackerL1' = 'Seeed Studio Wio Tracker L1 Pro'
    'WioTrackerL1Eink' = 'Seeed Studio Wio Tracker L1 EINK'
    'Xiao_C3' = 'Seeed Studio Xiao C3'
    'Xiao_nrf52' = 'Seeed Studio Xiao nRF52 WIO'
    'Xiao_S3' = 'Seeed Studio Xiao S3 WIO'
    'Xiao_S3_WIO' = 'Seeed Studio Xiao S3 WIO'
}

$DeviceSubTitleOverrides = @{
    'Heltec_t114_without_display' = 'Without display'
    'Heltec_T190' = 'T190'
    'heltec_v4_3' = 'v4.3'
    'heltec_v4_tft' = 'TFT'
    'heltec_v4_expansionkit' = 'Expansion Kit'
    'heltec_v4_expansionkit_tft' = 'Expansion Kit TFT'
    'LilyGo_T-Echo-Lite_non_shell' = 'Non shell'
    'ikoka_nano_nrf_22dbm' = '22 dBm'
    'ikoka_nano_nrf_30dbm' = '30 dBm'
    'ikoka_nano_nrf_33dbm' = '33 dBm'
    'ikoka_stick_nrf_22dbm' = '22 dBm'
    'ikoka_stick_nrf_30dbm' = '30 dBm'
    'ikoka_stick_nrf_33dbm' = '33 dBm'
}

function ConvertTo-DeviceName {
    param([string]$Prefix)
    if ($DeviceNameOverrides.ContainsKey($Prefix)) {
        return $DeviceNameOverrides[$Prefix]
    }

    $name = $Prefix.Trim('_')
    $name = $name -replace '_', ' '
    $name = $name -replace '\s+', ' '
    return $name.Trim()
}

function Join-SubTitle {
    param(
        [string]$DeviceKey,
        [string]$RoleSubTitle
    )

    $parts = @()
    if ($DeviceSubTitleOverrides.ContainsKey($DeviceKey)) {
        $parts += $DeviceSubTitleOverrides[$DeviceKey]
    }
    if ($RoleSubTitle) {
        $parts += $RoleSubTitle
    }

    if (-not $parts.Count) {
        return $null
    }

    return ($parts -join ', ')
}

function Get-RoleInfo {
    param([string]$DeviceRole)

    foreach ($pattern in $RolePatterns) {
        $suffix = $pattern.Suffix
        if ($DeviceRole.EndsWith("_$suffix", [StringComparison]::OrdinalIgnoreCase) -or
            $DeviceRole.EndsWith($suffix, [StringComparison]::OrdinalIgnoreCase)) {
            $prefixLength = $DeviceRole.Length - $suffix.Length
            if ($prefixLength -gt 0 -and ($DeviceRole[$prefixLength - 1] -eq '_' -or $DeviceRole[$prefixLength - 1] -eq '-')) {
                $prefixLength--
            }

            $devicePrefix = $DeviceRole.Substring(0, $prefixLength).Trim('_')
            if (-not $devicePrefix) {
                throw "Unable to parse device name from '$DeviceRole'"
            }

            return [ordered]@{
                DeviceKey = $devicePrefix
                DeviceName = ConvertTo-DeviceName $devicePrefix
                Role = $pattern.Role
                Title = $pattern.Title
                SubTitle = $pattern.SubTitle
            }
        }
    }

    throw "Unable to parse role from '$DeviceRole'"
}

function Get-FileSortRank {
    param([string]$Type, [string]$Name)

    switch ($Type) {
        'flash-wipe' { return 10 }
        'flash-update' { return 20 }
        'flash' { return 30 }
        default {
            if ($Name.EndsWith('.uf2', [StringComparison]::OrdinalIgnoreCase)) { return 40 }
            if ($Name.EndsWith('.hex', [StringComparison]::OrdinalIgnoreCase)) { return 50 }
            return 60
        }
    }
}

function Get-DeviceType {
    param([array]$Files)

    $extensions = @($Files | ForEach-Object { $_.Extension.ToLowerInvariant() } | Sort-Object -Unique)
    $hasMergedBin = @($Files | Where-Object {
        $_.Extension -ieq '.bin' -and $_.BaseName.EndsWith('-merged', [StringComparison]::OrdinalIgnoreCase)
    }).Count -gt 0

    if ($hasMergedBin) { return 'esp32' }
    if ($extensions -contains '.zip') { return 'nrf52' }
    return 'noflash'
}

function Get-FileType {
    param(
        [System.IO.FileInfo]$File,
        [string]$DeviceType
    )

    $extension = $File.Extension.ToLowerInvariant()
    $isMerged = $File.BaseName.EndsWith('-merged', [StringComparison]::OrdinalIgnoreCase)

    if ($DeviceType -eq 'esp32' -and $extension -eq '.bin' -and $isMerged) { return 'flash-wipe' }
    if ($DeviceType -eq 'esp32' -and $extension -eq '.bin') { return 'flash-update' }
    if ($DeviceType -eq 'nrf52' -and $extension -eq '.zip') { return 'flash' }
    return 'download'
}

function Get-FileTitle {
    param([string]$Type, [string]$Name)

    switch ($Type) {
        'flash-wipe' { return 'Full install (bootloader + firmware)' }
        'flash-update' { return 'Update (app only)' }
        'flash' { return 'Serial DFU package' }
        default {
            if ($Name.EndsWith('.uf2', [StringComparison]::OrdinalIgnoreCase)) { return 'UF2 download' }
            if ($Name.EndsWith('.hex', [StringComparison]::OrdinalIgnoreCase)) { return 'HEX download' }
            return 'Download'
        }
    }
}

function Get-DownloadUrl {
    param(
        [string]$Tag,
        [string]$Name
    )

    $escapedName = [Uri]::EscapeDataString($Name).Replace('%2F', '/')
    return "https://github.com/$Repo/releases/download/$Tag/$escapedName"
}

function New-Catalog {
    param([hashtable]$Definition)

    if (-not (Test-Path -LiteralPath $Definition.SourceDir)) {
        throw "Source directory not found: $($Definition.SourceDir)"
    }

    $sourceFiles = @(Get-ChildItem -LiteralPath $Definition.SourceDir -File | Sort-Object Name)
    if (-not $sourceFiles.Count) {
        throw "No release files found in $($Definition.SourceDir)"
    }

    $parsedFiles = foreach ($file in $sourceFiles) {
        $stem = $file.BaseName
        if ($stem.EndsWith('-merged', [StringComparison]::OrdinalIgnoreCase)) {
            $stem = $stem.Substring(0, $stem.Length - '-merged'.Length)
        }

        $suffix = "-$($Definition.Tag)"
        if (-not $stem.EndsWith($suffix, [StringComparison]::Ordinal)) {
            throw "File '$($file.Name)' does not end with expected tag '$($Definition.Tag)'"
        }

        $deviceRole = $stem.Substring(0, $stem.Length - $suffix.Length)
        $roleInfo = Get-RoleInfo $deviceRole

        [pscustomobject]@{
            File = $file
            DeviceKey = $roleInfo.DeviceKey
            DeviceName = $roleInfo.DeviceName
            Role = $roleInfo.Role
            Title = $roleInfo.Title
            SubTitle = Join-SubTitle $roleInfo.DeviceKey $roleInfo.SubTitle
        }
    }

    $devices = New-Object System.Collections.ArrayList
    foreach ($deviceGroup in @($parsedFiles | Group-Object DeviceName | Sort-Object Name)) {
        $deviceFiles = @($deviceGroup.Group | ForEach-Object { $_.File })
        $deviceType = Get-DeviceType $deviceFiles
        $firmware = New-Object System.Collections.ArrayList

        foreach ($roleGroup in @($deviceGroup.Group | Group-Object Role,Title,SubTitle | Sort-Object Name)) {
            $first = $roleGroup.Group[0]
            $files = @(
                $roleGroup.Group |
                    Sort-Object @{ Expression = { Get-FileSortRank (Get-FileType $_.File $deviceType) $_.File.Name } }, @{ Expression = { $_.File.Name } } |
                    ForEach-Object {
                        $type = Get-FileType $_.File $deviceType
                        [ordered]@{
                            type = $type
                            name = $_.File.Name
                            url = Get-DownloadUrl $Definition.Tag $_.File.Name
                            title = Get-FileTitle $type $_.File.Name
                        }
                    }
            )

            $firmwareEntry = [ordered]@{
                role = $first.Role
                title = $first.Title
            }

            if ($first.SubTitle) {
                $firmwareEntry.subTitle = $first.SubTitle
            }

            $firmwareEntry.version = [ordered]@{
                $Definition.Tag = [ordered]@{
                    notes = $Definition.Description
                    files = $files
                }
            }

            [void]$firmware.Add($firmwareEntry)
        }

        [void]$devices.Add([ordered]@{
            maker = 'keymindCascade'
            class = 'keymindCascade'
            name = $deviceGroup.Group[0].DeviceName
            type = $deviceType
            firmware = @($firmware)
        })
    }

    return [ordered]@{
        description = $Definition.Description
        maker = [ordered]@{
            keymindCascade = [ordered]@{ name = 'Keymind Cascade' }
        }
        role = $RoleDefinitions
        device = @($devices)
    }
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

foreach ($catalogDef in $Catalogs) {
    $catalog = New-Catalog $catalogDef
    $json = $catalog | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText($catalogDef.Output, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    Write-Output ("Wrote {0}" -f $catalogDef.Output)
}
