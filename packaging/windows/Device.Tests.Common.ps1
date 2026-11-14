Set-StrictMode -Version Latest

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    # CommandLineToArgvW escaping for Windows PowerShell 5.1 / .NET Framework.
    $result = '"'; $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') { $result += ('\' * (2 * $slashes + 1)) + '"' }
        else { $result += ('\' * $slashes) + $character }
        $slashes = 0
    }
    $result + ('\' * (2 * $slashes)) + '"'
}

function Get-CheckedTestInputs {
    param([string]$Root)
    $manifest = Get-Content -LiteralPath (Join-Path $Root 'provenance.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $cli = Join-Path $Root 'Portable-App/tools/muse-video-cli.exe'
    $expected = [string]$manifest.inputs.diagnostic_cli_sha256
    if ($expected -notmatch '^[a-f0-9]{64}$' -or
        (Get-Item -LiteralPath $cli).Attributes -band [IO.FileAttributes]::ReparsePoint -or
        (Get-FileHash -LiteralPath $cli -Algorithm SHA256).Hash -ne $expected) {
        throw 'Diagnostic CLI checksum mismatch. Extract a fresh complete kit.'
    }
    $profiles = @($manifest.test_profiles)
    if ($profiles.Count -eq 0 -or $profiles.Count -gt 32) { throw 'Missing or excessive test profiles.' }
    foreach ($profile in $profiles) {
        if ([string]$profile.file -notmatch '^Test-Media/[A-Za-z0-9][A-Za-z0-9._-]*\.mvb$' -or
            [string]$profile.sha256 -notmatch '^[a-f0-9]{64}$' -or
            [long]$profile.bytes -lt 136 -or [long]$profile.bytes -gt 2147483647) {
            throw 'Invalid prepared-media metadata.'
        }
        $path = Join-Path $Root $profile.file
        $item = Get-Item -LiteralPath $path
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint -or
            $item.Length -ne [long]$profile.bytes -or
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $profile.sha256) {
            throw ('Prepared-video checksum mismatch: ' + $profile.file)
        }
    }
    @{ Manifest = $manifest; Cli = $cli; Profiles = $profiles }
}

function Get-PlaybackTestDefaults {
    param([object]$Manifest)
    $raw = $Manifest.playback_default
    foreach ($name in @('decoder', 'render_mode', 'tft_spi_hz', 'command_spi_hz',
                         'big_endian', 'prefetch', 'target_fps', 'decoder_mcus')) {
        if ($null -eq $raw -or $raw.PSObject.Properties.Name -notcontains $name) {
            throw 'The kit is missing its recorded playback defaults. Extract a fresh complete kit.'
        }
    }
    if ($raw.decoder -notin @('jpegdec', 'rom', 'tjpgd_fast', 'new_jpeg') -or
        $raw.render_mode -notin @('legacy', 'blocks', 'dma') -or
        $raw.big_endian -isnot [bool] -or $raw.prefetch -isnot [bool]) {
        throw 'Invalid recorded playback defaults.'
    }
    foreach ($name in @('tft_spi_hz', 'command_spi_hz', 'target_fps', 'decoder_mcus')) {
        if ($raw.$name -isnot [int] -and $raw.$name -isnot [long]) {
            throw 'Invalid numeric playback default.'
        }
    }
    if ($raw.tft_spi_hz -notin @(20000000, 40000000, 80000000) -or
        $raw.command_spi_hz -notin @(10000000, 20000000, 40000000, 80000000) -or
        $raw.target_fps -lt 1 -or $raw.target_fps -gt 30 -or
        $raw.decoder_mcus -lt 1 -or $raw.decoder_mcus -gt 32 -or
        $Manifest.firmware.application_uart_baud -notin @(115200, 460800, 921600, 2000000, 3000000)) {
        throw 'Unsupported recorded playback default or UART baud.'
    }
    @{ Decoder = $raw.decoder; RenderMode = $raw.render_mode
       TftSpiHz = [int]$raw.tft_spi_hz; CommandSpiHz = [int]$raw.command_spi_hz
       BigEndian = $raw.big_endian; Prefetch = $raw.prefetch
       TargetFps = [int]$raw.target_fps; DecoderMcus = [int]$raw.decoder_mcus
       Baud = [int]$Manifest.firmware.application_uart_baud }
}

function Invoke-DeviceCli {
    param([string]$Cli, [string[]]$Arguments, [string]$Folder, [string]$Name)
    if ($Name -notmatch '^[a-zA-Z0-9._-]+$') { throw 'Invalid log name.' }
    $stem = Join-Path $Folder $Name
    foreach ($suffix in @('.stdout.json', '.stderr.log', '.command.json')) {
        if (Test-Path -LiteralPath ($stem + $suffix)) { throw 'Existing test log would be overwritten.' }
    }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Cli
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.Arguments = (@($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $launched = $false
    $outputFile = $null; $errorFile = $null
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $started = [DateTime]::UtcNow.ToString('o')
    Write-Host ('Running ' + $Name + '; USB progress is being saved to the log folder.')
    try {
        if (-not $process.Start()) { throw 'Could not start the diagnostic CLI.' }
        $launched = $true
        # Drain both pipes concurrently into exclusive files. Progress is retained
        # while the command runs, and neither pipe nor host memory grows unbounded.
        $outputFile = [IO.File]::Open($stem + '.stdout.json', [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        $errorFile = [IO.File]::Open($stem + '.stderr.log', [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        $stdout = $process.StandardOutput.BaseStream.CopyToAsync($outputFile)
        $stderr = $process.StandardError.BaseStream.CopyToAsync($errorFile)
        while (-not $process.WaitForExit(250)) { }
        $watch.Stop()
        [void]$stdout.GetAwaiter().GetResult()
        [void]$stderr.GetAwaiter().GetResult()
        $result = @{
            started_utc = $started; arguments = $Arguments; exit_code = $process.ExitCode
            operation_seconds = $watch.Elapsed.TotalSeconds
            timing_scope = 'whole CLI operation; includes validation and any playback benchmark'
        }
        $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath ($stem + '.command.json') -Encoding UTF8
        if ($process.ExitCode -ne 0) {
            Write-Host ('FAILED: ' + $Name + '. Read its stdout JSON and stderr log.') -ForegroundColor Red
        }
        $result
    } finally {
        if ($launched -and -not $process.HasExited) {
            # Only this owned child is stopped; firmware timeout retains its checkpoint.
            $process.Kill()
            $process.WaitForExit()
        }
        if ($null -ne $outputFile) { $outputFile.Dispose() }
        if ($null -ne $errorFile) { $errorFile.Dispose() }
        $process.Dispose()
    }
}
