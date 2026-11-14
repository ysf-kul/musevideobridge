"""Windows diagnostic runner host checks; no physical serial port is opened."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PWSH = os.environ.get("MVB_TEST_PWSH") or shutil.which("pwsh")
COMMON = ROOT / "packaging/windows/Device.Tests.Common.ps1"


@unittest.skipUnless(PWSH, "PowerShell is needed for diagnostic helper tests")
class DeviceHelperTests(unittest.TestCase):
    def run_ps(self, code, arguments):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "test.ps1"
            script.write_text("$ErrorActionPreference = 'Stop'\n" + code)
            return subprocess.run(
                [PWSH, "-NoProfile", "-File", str(script), *map(str, arguments)],
                capture_output=True, text=True, timeout=30,
            )

    def test_powershell_sources_parse(self):
        code = r"""
foreach ($path in $args) {
    $tokens = $null; $errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors -join "`n") }
}
"""
        result = self.run_ps(code, [COMMON, ROOT / "packaging/windows/Run-Device-Tests.ps1"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def fixture_kit(self, directory):
        root = Path(directory)
        cli = root / "Portable-App/tools/muse-video-cli.exe"
        cli.parent.mkdir(parents=True)
        cli.write_bytes(b"checked host fixture, never executed")
        media = root / "Test-Media/candidate.mvb"
        media.parent.mkdir()
        media.write_bytes(b"prepared fixture" * 64)
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {
            "schema_version": 1,
            "inputs": {"diagnostic_cli_sha256": digest(cli)},
            "test_profiles": [{"file": "Test-Media/candidate.mvb", "bytes": media.stat().st_size,
                               "sha256": digest(media), "options": {"fps": 30, "qscale": 8}}],
        }
        (root / "provenance.json").write_text(json.dumps(manifest))
        return root, cli, media, manifest

    def test_input_checks_detect_cli_media_corruption_and_traversal(self):
        code = r"""
. $args[0]
try {
    [void](Get-CheckedTestInputs $args[1])
    if ($args[2] -ne 'valid') { throw 'Invalid input was accepted.' }
} catch {
    if ($args[2] -eq 'valid' -or $_.Exception.Message -eq 'Invalid input was accepted.') { throw }
}
"""
        for condition in ["valid", "cli_corrupt", "media_corrupt", "path_traversal"]:
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as directory:
                root, cli, media, manifest = self.fixture_kit(directory)
                if condition == "cli_corrupt":
                    cli.write_bytes(b"changed")
                elif condition == "media_corrupt":
                    media.write_bytes(b"changed")
                elif condition == "path_traversal":
                    manifest["test_profiles"][0]["file"] = "Test-Media/../private.mvb"
                    (root / "provenance.json").write_text(json.dumps(manifest))
                result = self.run_ps(code, [COMMON, root, condition])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_arguments_logs_and_failure_exit_survive_large_stderr(self):
        with tempfile.TemporaryDirectory(prefix="muse test folder ") as directory:
            root = Path(directory)
            child = root / "child fixture.ps1"
            child.write_text(r"""
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Items)
[Console]::Out.Write(($Items | ConvertTo-Json -Compress))
[Console]::Error.Write(('diagnostic fixture ' * 5000))
exit 7
""")
            values = ["", "path with spaces", r'C:\path\ends\\', '{"method":"get_status"}', 'quote " and \\ slash', "$(never executes)"]
            arguments_path = root / "arguments.json"
            arguments_path.write_text(json.dumps(values))
            code = r"""
. $args[0]
$values = @(Get-Content -LiteralPath $args[3] -Raw | ConvertFrom-Json)
$result = Invoke-DeviceCli $args[1] (@('-NoProfile', '-File', $args[2]) + $values) (Split-Path $args[2]) 'native'
if ($result.exit_code -ne 7) { throw 'Native failure exit code was lost.' }
try {
    [void](Invoke-DeviceCli $args[1] @('-NoProfile') (Split-Path $args[2]) 'native')
    throw 'Existing logs were overwritten.'
} catch { if ($_.Exception.Message -eq 'Existing logs were overwritten.') { throw } }
"""
            result = self.run_ps(code, [COMMON, PWSH, child, arguments_path])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            received = json.loads((root / "native.stdout.json").read_text())
            self.assertEqual(received, values)
            self.assertGreater((root / "native.stderr.log").stat().st_size, 50_000)
            logged = json.loads((root / "native.command.json").read_text(encoding="utf-8-sig"))
            self.assertEqual(logged["exit_code"], 7)
            self.assertEqual(logged["arguments"][3:], values)
            self.assertGreater(logged["operation_seconds"], 0)

    def test_recorded_defaults_preserve_fast_uart_and_reject_unsafe_values(self):
        code = r"""
. $args[0]
$manifest = Get-Content -LiteralPath $args[1] -Raw | ConvertFrom-Json
try {
    $result = Get-PlaybackTestDefaults $manifest
    if ($args[2] -ne 'valid') { throw 'Invalid defaults were accepted.' }
    if ($result.Baud -ne 2000000 -or $result.Decoder -ne 'new_jpeg' -or
        $result.RenderMode -ne 'dma' -or $result.TftSpiHz -ne 40000000 -or
        $result.CommandSpiHz -ne 20000000 -or -not $result.BigEndian -or
        -not $result.Prefetch -or $result.TargetFps -ne 30) {
        throw 'Recorded performance defaults were lost.'
    }
} catch {
    if ($args[2] -eq 'valid' -or $_.Exception.Message -eq 'Invalid defaults were accepted.') { throw }
}
"""
        defaults = {"decoder": "new_jpeg", "render_mode": "dma", "tft_spi_hz": 40000000,
                    "command_spi_hz": 20000000, "big_endian": True, "prefetch": True,
                    "target_fps": 30, "decoder_mcus": 20}
        for condition in ["valid", "missing", "decoder", "clock", "string_boolean", "string_fps", "baud"]:
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as directory:
                manifest = {"playback_default": dict(defaults), "firmware": {"application_uart_baud": 2000000}}
                if condition == "missing":
                    del manifest["playback_default"]["decoder"]
                elif condition == "decoder":
                    manifest["playback_default"]["decoder"] = "unknown; command"
                elif condition == "clock":
                    manifest["playback_default"]["tft_spi_hz"] = 70000000
                elif condition == "string_boolean":
                    manifest["playback_default"]["big_endian"] = "false"
                elif condition == "string_fps":
                    manifest["playback_default"]["target_fps"] = "30"
                elif condition == "baud":
                    manifest["firmware"]["application_uart_baud"] = 123456
                path = Path(directory) / "manifest.json"
                path.write_text(json.dumps(manifest))
                result = self.run_ps(code, [COMMON, path, condition])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
