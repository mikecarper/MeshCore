# 2>NUL & @set "MESHCORE_TERM_SCRIPT=%~f0" & @set "MESHCORE_TERM_ARG=%~1" & @powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression ((Get-Content -LiteralPath $env:MESHCORE_TERM_SCRIPT) -join [Environment]::NewLine)" & @set "MESHCORE_TERM_SCRIPT=" & @set "MESHCORE_TERM_ARG=" & goto :eof

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:BaudRate = 115200
$script:StartToken = '+++MESHCORE-TERM-START'
$script:StopToken = '+++MESHCORE-TERM-STOP'

function Remove-HybridLauncherEcho {
    # cmd.exe echoes the harmless PowerShell/CMD bridge before PowerShell takes
    # over. Remove that one line when running in an interactive console.
    try {
        if (-not [Console]::IsOutputRedirected -and [Console]::CursorTop -gt 0) {
            $row = [Console]::CursorTop - 1
            [Console]::SetCursorPosition(0, $row)
            [Console]::Write(' ' * [Math]::Max(1, [Console]::WindowWidth - 1))
            [Console]::SetCursorPosition(0, $row)
        }
    }
    catch {
    }
}

Remove-HybridLauncherEcho

function Get-SerialPortInventory {
    # Match firmware.cmd: only accept Plug-and-Play COM devices whose Windows
    # device ID identifies them as USB. This excludes motherboard COM1 ports,
    # Bluetooth serial ports, and other unrelated serial devices.
    $ports = @()

    try {
        $devices = @(
            Get-CimInstance -ClassName Win32_PnPEntity -ErrorAction Stop |
                Where-Object {
                    $_.DeviceID -like '*USB*' -and $_.Name -match '\(COM\d+\)'
                }
        )

        foreach ($device in $devices) {
            if ($device.Name -match 'COM(\d+)') {
                $portName = $matches[0].ToUpperInvariant()
                $portNumber = [int]$matches[1]
                $hardwareId = if ($device.HardwareID) {
                    ($device.HardwareID -split '\\')[-1]
                }
                else {
                    '--'
                }

                $ports += [pscustomobject]@{
                    Port = $portName
                    Label = $device.Name
                    DeviceName = $hardwareId
                    SortOrder = $portNumber
                }
            }
        }
    }
    catch {
        Write-Host ("Unable to query Windows USB serial devices: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    }

    return @($ports | Sort-Object SortOrder, Label)
}

function Show-Ports {
    $ports = @(Get-SerialPortInventory)

    if ($ports.Count -eq 0) {
        Write-Host 'No USB serial ports are currently available.' -ForegroundColor Yellow
        return
    }

    Write-Host 'Available USB serial ports:'
    foreach ($item in $ports) {
        Write-Host ("  {0,-7} {1} [{2}]" -f $item.Port, $item.Label, $item.DeviceName)
    }
}

function Select-SerialPort {
    param([string]$RequestedPort)

    if ($RequestedPort) {
        $candidate = $RequestedPort.Trim().ToUpperInvariant()
        if ($candidate -match '^\d+$') {
            $candidate = "COM$candidate"
        }
        if ($candidate -notmatch '^COM\d+$') {
            throw "Invalid COM port '$RequestedPort'. Use a value such as COM7."
        }

        $usbPorts = @(Get-SerialPortInventory | Select-Object -ExpandProperty Port)
        if ($usbPorts -notcontains $candidate) {
            $available = if ($usbPorts.Count -gt 0) { $usbPorts -join ', ' } else { 'none' }
            throw "$candidate is not a currently connected USB COM port. Available USB ports: $available."
        }
        return $candidate
    }

    while ($true) {
        $ports = @(Get-SerialPortInventory)
        if ($ports.Count -eq 0) {
            Write-Host 'No USB serial ports found. Connect the node with a USB data cable.' -ForegroundColor Yellow
            $answer = Read-Host 'Press Enter to scan again, or type Q to quit'
            if ($answer -match '^[Qq]$') { return '' }
            continue
        }

        if ($ports.Count -eq 1) {
            Write-Host ("Using {0}: {1}" -f $ports[0].Port, $ports[0].Label)
            return $ports[0].Port
        }

        Write-Host 'Select the MeshCore USB serial port:'
        for ($index = 0; $index -lt $ports.Count; $index++) {
            Write-Host ("  {0,2}) {1,-7} {2}" -f ($index + 1), $ports[$index].Port, $ports[$index].Label)
        }
        Write-Host '   R) Scan again'
        Write-Host '   Q) Quit'

        $answer = (Read-Host 'Choice').Trim()
        if ($answer -match '^[Rr]$') { continue }
        if ($answer -match '^[Qq]$') { return '' }

        $selection = 0
        if ([int]::TryParse($answer, [ref]$selection) -and
            $selection -ge 1 -and $selection -le $ports.Count) {
            return $ports[$selection - 1].Port
        }

        Write-Host 'Invalid selection.' -ForegroundColor Yellow
    }
}

function Read-CompanionModeChoice {
    Write-Host ''
    Write-Host 'Firmware type:'
    Write-Host '  1) USB/full Companion firmware (default)'
    Write-Host '  2) Terminal Chat firmware'
    $answer = (Read-Host 'Choice [1]').Trim()

    switch ($answer) {
        ''  { return $true }
        '1' { return $true }
        '2' { return $false }
        default {
            Write-Host 'Using USB/full Companion mode.' -ForegroundColor Yellow
            return $true
        }
    }
}

function Open-SerialPort {
    param([Parameter(Mandatory)][string]$PortName)

    $port = [System.IO.Ports.SerialPort]::new(
        $PortName,
        $script:BaudRate,
        [System.IO.Ports.Parity]::None,
        8,
        [System.IO.Ports.StopBits]::One
    )
    $port.Handshake = [System.IO.Ports.Handshake]::None
    $port.Encoding = [System.Text.UTF8Encoding]::new($false, $false)
    $port.NewLine = "`r`n"
    $port.ReadTimeout = 100
    $port.WriteTimeout = 1000
    $port.DtrEnable = $true
    $port.RtsEnable = $true
    $port.Open()
    return $port
}

function Enter-CompanionTerminalMode {
    param([Parameter(Mandatory)][System.IO.Ports.SerialPort]$Port)

    try { $Port.DiscardInBuffer() } catch { }
    $Port.Write($script:StartToken + "`r")

    # Do not render Binary Companion frames as console control characters while
    # the device changes modes. Start displaying once the text banner arrives.
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $idle = [System.Diagnostics.Stopwatch]::StartNew()
    $buffer = [System.Text.StringBuilder]::new()
    $bannerText = 'MeshCore Chat Terminal'
    $bannerSeen = $false

    while ($watch.ElapsedMilliseconds -lt 5000) {
        $received = $Port.ReadExisting()
        if ($received) {
            [void]$buffer.Append($received)
            $idle.Restart()
            $allText = $buffer.ToString()
            $bannerIndex = $allText.IndexOf($bannerText, [System.StringComparison]::OrdinalIgnoreCase)
            if ($bannerIndex -ge 0) {
                $bannerSeen = $true
            }
        }

        # The device can already have several Binary Companion frames queued
        # when it recognizes the switch token. Drain the entire transition and
        # wait for a quiet period before rendering live terminal output.
        if ($bannerSeen -and $idle.ElapsedMilliseconds -ge 350) {
            return $true
        }
        Start-Sleep -Milliseconds 20
    }

    if (-not $bannerSeen) {
        Write-Host 'Terminal switch sent; no banner was received.' -ForegroundColor Yellow
        return $false
    }

    Write-Host 'MeshCore responded, but startup traffic did not become quiet.' -ForegroundColor Yellow
    return $false
}

function Confirm-DedicatedTerminalChatMode {
    param([Parameter(Mandatory)][System.IO.Ports.SerialPort]$Port)

    # A dedicated Terminal Chat build may have printed its banner before the
    # port was opened. "ver" provides a harmless live firmware check. On a
    # brand-new node its LF also completes the initial key-generation prompt.
    $buffer = [System.Text.StringBuilder]::new()
    try {
        $startupText = $Port.ReadExisting()
        if ($startupText) { [void]$buffer.Append($startupText) }
        $Port.Write("ver`r`n")
    }
    catch {
        return $false
    }

    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $idle = [System.Diagnostics.Stopwatch]::StartNew()
    $terminalSeen = $false
    while ($watch.ElapsedMilliseconds -lt 5000) {
        $received = $Port.ReadExisting()
        if ($received) {
            [void]$buffer.Append($received)
            $idle.Restart()
        }

        $text = $buffer.ToString()
        if ($text -match '(?i)MeshCore Chat Terminal' -or
            $text -match '(?im)^\s*v\d+(?:\.\d+)*(?:\s|$)' -or
            $text -match '(?i)\bbuild\s*:') {
            $terminalSeen = $true
        }

        if ($terminalSeen -and $idle.ElapsedMilliseconds -ge 350) {
            return $true
        }

        Start-Sleep -Milliseconds 20
    }

    return ($terminalSeen -and $Port.BytesToRead -eq 0)
}

function Start-TerminalSession {
    param(
        [Parameter(Mandatory)][string]$PortName,
        [Parameter(Mandatory)][bool]$SwitchCompanionMode
    )

    $port = $null
    $oldTreatControlCAsInput = [Console]::TreatControlCAsInput
    $lineCharacterCount = 0
    $pendingHighSurrogate = ''

    try {
        Write-Host ''
        Write-Host "Opening $PortName at $($script:BaudRate) baud..."
        $port = Open-SerialPort -PortName $PortName

        # Some USB-to-UART boards reset when the port opens.
        Start-Sleep -Milliseconds 1500

        if ($SwitchCompanionMode) {
            $verified = Enter-CompanionTerminalMode -Port $port
        }
        else {
            $verified = Confirm-DedicatedTerminalChatMode -Port $port
        }

        if (-not $verified) {
            throw "$PortName opened, but it did not respond as the selected MeshCore terminal firmware."
        }

        Write-Host "Verified MeshCore terminal on $PortName." -ForegroundColor DarkGray

        Write-Host ''
        Write-Host 'Connected. Type help for commands. Press Ctrl+] or Ctrl+C to exit.' -ForegroundColor Green
        Write-Host ''
        [Console]::Write('> ')

        [Console]::TreatControlCAsInput = $true

        while ($port.IsOpen) {
            $received = $port.ReadExisting()
            if ($received) {
                [Console]::Write($received)
            }

            while ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                $characterCode = [int]$key.KeyChar

                if ($characterCode -eq 29 -or $characterCode -eq 3) {
                    return
                }

                if ($key.Key -eq [ConsoleKey]::Enter) {
                    $pendingHighSurrogate = ''
                    $port.Write("`r`n")
                    $lineCharacterCount = 0
                    continue
                }

                if ($key.Key -eq [ConsoleKey]::Backspace) {
                    if ($pendingHighSurrogate) {
                        $pendingHighSurrogate = ''
                        continue
                    }
                    $port.Write([string][char]8)
                    if ($lineCharacterCount -gt 0) { $lineCharacterCount-- }
                    continue
                }

                if ($characterCode -ne 0) {
                    if ([char]::IsHighSurrogate($key.KeyChar)) {
                        $pendingHighSurrogate = [string]$key.KeyChar
                        continue
                    }

                    if ($pendingHighSurrogate) {
                        if ([char]::IsLowSurrogate($key.KeyChar)) {
                            $port.Write($pendingHighSurrogate + [string]$key.KeyChar)
                            $pendingHighSurrogate = ''
                            $lineCharacterCount++
                            continue
                        }

                        $port.Write($pendingHighSurrogate)
                        $pendingHighSurrogate = ''
                        $lineCharacterCount++
                    }

                    $port.Write([string]$key.KeyChar)
                    $lineCharacterCount++
                }
            }

            Start-Sleep -Milliseconds 10
        }
    }
    finally {
        [Console]::TreatControlCAsInput = $oldTreatControlCAsInput

        if ($port) {
            if ($port.IsOpen -and $SwitchCompanionMode) {
                try {
                    # Clear an unfinished input line so the exact stop token is recognized.
                    for ($index = 0; $index -lt $lineCharacterCount; $index++) {
                        $port.Write([string][char]8)
                    }
                    $port.Write($script:StopToken + "`r")
                    Start-Sleep -Milliseconds 150
                }
                catch {
                }
            }

            try {
                if ($port.IsOpen) { $port.Close() }
            }
            finally {
                $port.Dispose()
            }
        }

        Write-Host ''
        Write-Host 'Terminal closed.'
    }
}

$requested = if ($env:MESHCORE_TERM_ARG) { $env:MESHCORE_TERM_ARG.Trim() } else { '' }

if ($requested -match '^(\/list|-list|--list)$') {
    Show-Ports
    return
}

if ($requested -match '^(\/\?|-h|--help)$') {
    Write-Host 'MeshCore terminal chat'
    Write-Host ''
    Write-Host 'Usage:'
    Write-Host '  meshcore-terminal-chat.cmd'
    Write-Host '  meshcore-terminal-chat.cmd COM7'
    Write-Host '  meshcore-terminal-chat.cmd /list'
    return
}

try {
    $selectedPort = Select-SerialPort -RequestedPort $requested
    if (-not $selectedPort) { return }

    $switchCompanionMode = Read-CompanionModeChoice
    Start-TerminalSession -PortName $selectedPort -SwitchCompanionMode $switchCompanionMode
}
catch {
    Write-Host ''
    Write-Host ("Unable to start terminal chat: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Close any MeshCore app, browser console, or other program using the COM port.' -ForegroundColor Yellow
    Read-Host 'Press Enter to close' | Out-Null
    $global:LASTEXITCODE = 1
}
