Set-StrictMode -Version Latest

function Test-WindowsHost { $env:OS -eq 'Windows_NT' }
function Get-CYDPorts { [IO.Ports.SerialPort]::GetPortNames() | Sort-Object }

function Get-Catalog {
    param([string]$Root, [string]$Language)
    Get-Content -LiteralPath (Join-Path $Root "localization/$Language.json") -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Get-Text {
    param([object]$Catalog, [string]$Key, [object[]]$Values = @())
    $text = $Catalog.PSObject.Properties[$Key]
    if ($null -eq $text) { throw "Missing localization key: $Key" }
    [string]::Format([string]$text.Value, $Values)
}

function Get-CheckedManifest {
    param([string]$Root, [object]$Catalog)
    $manifest = Get-Content -LiteralPath (Join-Path $Root 'firmware-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or $manifest.profile -ne 'cyd_ili9341' -or
        $manifest.chip -ne 'esp32' -or $manifest.flash_size_bytes -ne 4194304 -or
        $manifest.flash_mode -ne 'dio' -or $manifest.flash_frequency -ne '40m' -or
        $manifest.baud -ne 115200 -or @($manifest.files).Count -ne 4) {
        throw (Get-Text $Catalog 'bad_manifest')
    }
    $expected = @{
        'firmware/bootloader.bin' = '0x1000'
        'firmware/partitions.bin' = '0x8000'
        'firmware/boot_app0.bin' = '0xe000'
        'firmware/firmware.bin' = '0x10000'
    }
    $seen = @{}
    $limits = @{
        'firmware/bootloader.bin' = 28672
        'firmware/partitions.bin' = 4096
        'firmware/boot_app0.bin' = 8192
        'firmware/firmware.bin' = 1310720
    }
    foreach ($entry in $manifest.files) {
        if (-not $expected.ContainsKey($entry.path) -or $seen.ContainsKey($entry.path) -or
            $entry.offset -ne $expected[$entry.path]) { throw (Get-Text $Catalog 'bad_manifest') }
        $seen[$entry.path] = $true
        $path = Join-Path $Root $entry.path
        $item = Get-Item -LiteralPath $path
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $item.Length -ne $entry.bytes -or $item.Length -eq 0 -or $item.Length -gt $limits[$entry.path] -or
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw (Get-Text $Catalog 'bad_hash' @($entry.path))
        }
    }
    if ($manifest.esptool.path -ne 'tools/esptool.exe') { throw (Get-Text $Catalog 'bad_manifest') }
    $tool = Join-Path $Root $manifest.esptool.path
    if ((Get-FileHash -LiteralPath $tool -Algorithm SHA256).Hash -ne $manifest.esptool.sha256) {
        throw (Get-Text $Catalog 'bad_hash' @($manifest.esptool.path))
    }
    $manifest
}

function Get-FlashPlan {
    param([string]$Root, [string]$Port, [string]$Backup)
    if ($Port -notmatch '^COM[1-9][0-9]*$') { throw 'Invalid COM port' }
    $base = @('--chip', 'esp32', '--port', $Port, '--baud', '115200', '--before', 'default-reset', '--after', 'hard-reset')
    $images = @(
        '0x1000', (Join-Path $Root 'firmware/bootloader.bin'),
        '0x8000', (Join-Path $Root 'firmware/partitions.bin'),
        '0xe000', (Join-Path $Root 'firmware/boot_app0.bin'),
        '0x10000', (Join-Path $Root 'firmware/firmware.bin')
    )
    @{
        Identify = $base + @('flash-id')
        Backup = $base + @('read-flash', '0x0', '0x400000', $Backup)
        Write = $base + @('write-flash', '-z', '--flash-mode', 'dio', '--flash-freq', '40m', '--flash-size', '4MB') + $images
        Verify = $base + @('verify-flash', '--flash-mode', 'dio', '--flash-freq', '40m', '--flash-size', '4MB') + $images
    }
}

function Get-ApplicationFlashPlan {
    param([string]$Root, [string]$Port, [string]$Backup)
    $plan = Get-FlashPlan $Root $Port $Backup
    $base = @('--chip', 'esp32', '--port', $Port, '--baud', '115200', '--before', 'default-reset', '--after', 'hard-reset')
    $image = @('0x10000', (Join-Path $Root 'firmware/firmware.bin'))
    $plan.Write = $base + @('write-flash', '-z', '--flash-mode', 'dio', '--flash-freq', '40m', '--flash-size', '4MB') + $image
    $plan.Verify = $base + @('verify-flash', '--flash-mode', 'dio', '--flash-freq', '40m', '--flash-size', '4MB') + $image
    $plan
}

function Test-BackupPartitionCompatibility {
    param([string]$Root, [string]$Backup)
    $item = Get-Item -LiteralPath $Backup
    if ($item.Length -ne 4194304 -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $false }
    $expected = [IO.File]::ReadAllBytes((Join-Path $Root 'firmware/partitions.bin'))
    if ($expected.Length -eq 0 -or $expected.Length -gt 4096) { return $false }
    $actual = New-Object byte[] $expected.Length
    $stream = [IO.File]::OpenRead($Backup)
    try {
        [void]$stream.Seek(0x8000, [IO.SeekOrigin]::Begin)
        $offset = 0
        while ($offset -lt $actual.Length) {
            $count = $stream.Read($actual, $offset, $actual.Length - $offset)
            if ($count -eq 0) { return $false }
            $offset += $count
        }
    } finally { $stream.Dispose() }
    for ($i = 0; $i -lt $expected.Length; $i++) {
        if ($expected[$i] -ne $actual[$i]) { return $false }
    }
    $true
}

function Invoke-Esptool {
    param([string]$Tool, [string[]]$Arguments, [object]$Catalog)
    # PS5.1 converts native stderr to ErrorRecord; keep it visible and judge the exit code.
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = @(& $Tool @Arguments 2>&1 | ForEach-Object { Write-Host $_; [string]$_ })
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $oldPreference }
    if ($code -ne 0) { throw (Get-Text $Catalog 'tool_failed' @($code)) }
    $lines -join "`n"
}
