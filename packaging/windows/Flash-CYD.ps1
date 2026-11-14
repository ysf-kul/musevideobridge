#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('tr', 'en')][string]$Language = 'tr',
    [string]$Port = '',
    [switch]$SkipBackup,
    [switch]$ApplicationOnly
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Flash.Common.ps1')
$catalog = Get-Catalog $PSScriptRoot $Language
try {
    if (-not (Test-WindowsHost)) { throw (Get-Text $catalog 'windows_only') }
    if ($ApplicationOnly -and $SkipBackup) { throw (Get-Text $catalog 'application_backup_required') }
    Write-Host (Get-Text $catalog 'title')
    Write-Host (Get-Text $catalog 'profile')
    if ($ApplicationOnly) { Write-Host (Get-Text $catalog 'application_scope') }
    Write-Host (Get-Text $catalog 'close_app')
    $manifest = Get-CheckedManifest $PSScriptRoot $catalog
    Write-Host (Get-Text $catalog 'integrity_ok')
    $ports = @(Get-CYDPorts)
    if ($ports.Count -eq 0) { throw (Get-Text $catalog 'no_ports') }
    Write-Host (Get-Text $catalog 'ports' @(($ports -join ', ')))
    if ([string]::IsNullOrWhiteSpace($Port)) { $Port = Read-Host (Get-Text $catalog 'select_port') }
    $Port = $Port.Trim().ToUpperInvariant()
    if ($Port -notmatch '^COM[1-9][0-9]*$' -or $ports -notcontains $Port) {
        throw (Get-Text $catalog 'invalid_port')
    }
    $backupFolder = Join-Path $PSScriptRoot 'Backups'
    $backup = Join-Path $backupFolder ("flash-{0}-{1}.bin" -f $Port, (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    $plan = if ($ApplicationOnly) { Get-ApplicationFlashPlan $PSScriptRoot $Port $backup }
            else { Get-FlashPlan $PSScriptRoot $Port $backup }
    $tool = Join-Path $PSScriptRoot $manifest.esptool.path
    Write-Host (Get-Text $catalog 'identifying')
    $identity = Invoke-Esptool $tool $plan.Identify $catalog
    if ($identity -notmatch '(?im)Detected flash size:\s*4\s*MB\b') {
        throw (Get-Text $catalog 'wrong_flash')
    }
    Write-Host (Get-Text $catalog 'confirm' @($Port))
    if ((Read-Host (Get-Text $catalog 'type_flash')) -cne 'FLASH') {
        Write-Host (Get-Text $catalog 'cancelled')
        exit 2
    }
    if ($SkipBackup) {
        Write-Host (Get-Text $catalog 'skip_backup')
        if ((Read-Host (Get-Text $catalog 'type_skip')) -cne 'SKIP') {
            Write-Host (Get-Text $catalog 'cancelled')
            exit 2
        }
    } else {
        [void](New-Item -ItemType Directory -Path $backupFolder -Force)
        if (Test-Path -LiteralPath $backup) { throw (Get-Text $catalog 'backup_exists') }
        Write-Host (Get-Text $catalog 'backing_up')
        [void](Invoke-Esptool $tool $plan.Backup $catalog)
        if ((Get-Item -LiteralPath $backup).Length -ne 4194304) { throw (Get-Text $catalog 'backup_incomplete') }
        $hash = (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant()
        Set-Content -LiteralPath ($backup + '.sha256') -Value $hash -Encoding ASCII
        Write-Host (Get-Text $catalog 'backup_saved' @($backup))
    }
    if ($ApplicationOnly -and -not (Test-BackupPartitionCompatibility $PSScriptRoot $backup)) {
        throw (Get-Text $catalog 'partition_mismatch')
    }
    # Recheck immediately before writing; a failed/partial backup never reaches this point.
    [void](Get-CheckedManifest $PSScriptRoot $catalog)
    Write-Host (Get-Text $catalog $(if ($ApplicationOnly) { 'application_flashing' } else { 'flashing' }))
    [void](Invoke-Esptool $tool $plan.Write $catalog)
    Write-Host (Get-Text $catalog 'verifying')
    [void](Invoke-Esptool $tool $plan.Verify $catalog)
    Write-Host (Get-Text $catalog 'complete')
    if ($ApplicationOnly) { Write-Host (Get-Text $catalog 'application_next') }
    Write-Host (Get-Text $catalog 'next')
    exit 0
} catch {
    Write-Host (Get-Text $catalog 'failed' @($_.Exception.Message)) -ForegroundColor Red
    Write-Host (Get-Text $catalog 'recovery')
    exit 1
}
