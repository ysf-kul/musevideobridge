#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Port = '',
    [ValidateRange(2, 600)][int]$Seconds = 8,
    [switch]$AllProfiles,
    [ValidateSet('legacy', 'blocks', 'dma')][string]$RenderMode = 'blocks',
    [ValidateSet(20000000, 40000000, 80000000)][int]$TftSpiHz = 20000000,
    [ValidateSet(10000000, 20000000, 40000000, 80000000)][int]$CommandSpiHz = 20000000,
    [ValidateSet('jpegdec', 'rom', 'tjpgd_fast', 'new_jpeg')][string]$Decoder = 'jpegdec',
    [ValidateSet(115200, 460800, 921600, 2000000, 3000000)][int]$Baud = 115200,
    [ValidateRange(1, 30)][int]$TargetFps = 30,
    [ValidateRange(1, 32)][int]$DecoderMcus = 20,
    [switch]$BigEndian,
    [switch]$Prefetch,
    [switch]$NativeSize,
    [switch]$FullSize
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Device.Tests.Common.ps1')
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper directly on Windows.' }
    if ($NativeSize -and $FullSize) { throw 'Choose either native or full display size.' }
    $inputs = Get-CheckedTestInputs $PSScriptRoot
    $defaults = Get-PlaybackTestDefaults $inputs.Manifest
    # Use the exact settings recorded when this kit was assembled. Named user
    # parameters remain available for a deliberate comparison, including :$false.
    foreach ($name in @('RenderMode', 'TftSpiHz', 'CommandSpiHz', 'Decoder', 'Baud',
                         'TargetFps', 'DecoderMcus', 'BigEndian', 'Prefetch')) {
        if (-not $PSBoundParameters.ContainsKey($name)) {
            Set-Variable -Name $name -Value $defaults[$name]
        }
    }
    $ports = @([IO.Ports.SerialPort]::GetPortNames() | Sort-Object)
    if ($ports.Count -eq 0) { throw 'No serial ports found. Check the direct USB connection and CH340 driver.' }
    Write-Host ('Available ports: ' + ($ports -join ', '))
    if ([string]::IsNullOrWhiteSpace($Port)) { $Port = Read-Host 'Enter the actual CYD COM port' }
    $Port = $Port.Trim().ToUpperInvariant()
    if ($Port -notmatch '^COM[1-9][0-9]*$' -or $ports -notcontains $Port) { throw 'Select an available COM port.' }

    Write-Host 'Keep the microSD card inside the ESP32. First test Send over USB in the desktop app.'
    Write-Host 'This helper records that owner observation separately from its CLI tests.'
    $desktop = (Read-Host 'Desktop transfer, final verification and visible playback: succeeded / failed / not_tested').Trim()
    if ($desktop -notin @('succeeded', 'failed', 'not_tested')) { throw 'Use succeeded, failed or not_tested.' }
    $notes = Read-Host 'Record the exact desktop error, or the verified file size and visible playback result'
    $card = (Read-Host 'Did the SD card stay in the ESP32 throughout? yes / no / unknown').Trim()
    if ($card -notin @('yes', 'no', 'unknown')) { throw 'Use yes, no or unknown.' }
    Write-Host 'Close the desktop app and every serial monitor before continuing.'
    if ((Read-Host 'Type READY when the COM port is free') -cne 'READY') { throw 'Test cancelled before opening the port.' }

    $profiles = $inputs.Profiles
    if (-not $AllProfiles) {
        for ($i = 0; $i -lt $profiles.Count; $i++) {
            Write-Host ('{0}: {1} ({2:N0} bytes)' -f ($i + 1), $profiles[$i].file, $profiles[$i].bytes)
        }
        $chosen = 0
        if (-not [int]::TryParse((Read-Host 'Select one profile number'), [ref]$chosen) -or
            $chosen -lt 1 -or $chosen -gt $profiles.Count) { throw 'Invalid profile selection.' }
        $profiles = @($profiles[$chosen - 1])
    }
    $folder = Join-Path $PSScriptRoot ('Device-Test-Results/' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $folder)
    @{
        evidence_source = 'owner-entered desktop observations; not an automated desktop test'
        desktop_end_to_end = $desktop; notes = $notes; sd_remained_inside = $card
        windows = [Environment]::OSVersion.VersionString; port = $Port
        kit_revision = $inputs.Manifest.build_revision
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $folder 'desktop-owner-observation.json') -Encoding UTF8
    $base = @('device', '--port', $Port, '--baud', [string]$Baud, '--lang', 'en')
    $diagnostics = '[{"method":"get_status"},{"method":"get_serial_diagnostics"},{"method":"get_display_info"}]'
    $failed = (Invoke-DeviceCli $inputs.Cli ($base + @('--request', $diagnostics)) $folder 'before').exit_code -ne 0
    foreach ($profile in $profiles) {
        $name = [IO.Path]::GetFileNameWithoutExtension([string]$profile.file)
        $arguments = $base + @('--upload', (Join-Path $PSScriptRoot $profile.file), '--benchmark-seconds', [string]$Seconds,
                              '--target-fps', [string]$TargetFps, '--decoder', $Decoder,
                              '--decoder-mcus', [string]$DecoderMcus,
                              '--prefetch', $(if ($Prefetch) { 'on' } else { 'off' }),
                              '--render-mode', $RenderMode, '--tft-spi-hz', [string]$TftSpiHz,
                              '--command-spi-hz', [string]$CommandSpiHz)
        if ($BigEndian) { $arguments += '--big-endian' }
        if ($FullSize) { $arguments += '--display-full' }
        else { $arguments += '--display-native' }
        $result = Invoke-DeviceCli $inputs.Cli $arguments $folder $name
        if ($result.exit_code -ne 0) { $failed = $true }
        @{
            expected_sha256 = $profile.sha256; expected_bytes = $profile.bytes
            encoded_options = $profile.options; requested_fps = $TargetFps; exit_code = $result.exit_code
            visible_quality = (Read-Host 'Describe physical motion, colors and image quality for this profile')
        } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $folder ($name + '.owner-observation.json')) -Encoding UTF8
    }
    if ((Invoke-DeviceCli $inputs.Cli ($base + @('--request', $diagnostics)) $folder 'after').exit_code -ne 0) { $failed = $true }
    Write-Host ('Results saved: ' + $folder)
    Write-Host 'A CLI result does not replace the desktop workflow. Keep backups private when sharing logs.'
    if ($failed) { exit 1 }
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
