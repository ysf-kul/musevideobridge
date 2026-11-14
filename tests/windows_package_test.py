"""Package integrity and host PowerShell control-flow tests; no physical port is opened."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_windows", ROOT / "packaging/build_windows.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
PWSH = os.environ.get("MVB_TEST_PWSH") or shutil.which("pwsh")


class PackageTests(unittest.TestCase):
    def test_recommended_english_launchers_preserve_unrelated_flash(self):
        for name in ["Flash-Firmware-English.cmd", "Flash-Performance-English.cmd"]:
            text = (ROOT / "packaging/windows" / name).read_text()
            self.assertIn('-Language en -ApplicationOnly', text)
        text = (ROOT / "packaging/windows/Flash-Full-Firmware-English.cmd").read_text()
        self.assertIn('-Language en', text)
        self.assertNotIn('-ApplicationOnly', text)

    def test_source_allowlist_keeps_vendors_and_excludes_private_build_trees(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "project-source.zip"
            builder.source_zip(path)
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                for name in names:
                    self.assertFalse({".dev-tools", ".verification", ".pio", ".git",
                                      "__pycache__", "target", "node_modules"}.intersection(Path(name).parts), name)
                self.assertIn("firmware/vendor/esp_new_jpeg/lib/esp32/libesp_new_jpeg.a", names)
                self.assertIn("firmware/vendor/esp_new_jpeg/PROVENANCE.json", names)
                self.assertIn("firmware/vendor/tjpgd_fast/upstream/tjpgd.c", names)
                for name in ["MUSE_GUIDELINES.md", "MUSE_DESIGN_SYSTEM.md", "AGENTS.md"]:
                    self.assertEqual(archive.read(name), (ROOT / name).read_bytes())

    def test_archive_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            (stage / "data").write_bytes(b"real payload")
            info = builder.finish(stage, root, "test", {"schema_version": 1})
            original = root / info["filename"]
            builder.verify_package(original)
            bad = root / "mutated.zip"
            with zipfile.ZipFile(original) as source, zipfile.ZipFile(bad, "w") as target:
                for name in source.namelist():
                    target.writestr(name, b"changed" if name.endswith("/data") else source.read(name))
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                builder.verify_package(bad)

    def test_license_files_with_epoch_timestamps_remain_intact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            notice = stage / "LICENSE.txt"
            payload = b"Upstream license retained byte-for-byte\n"
            notice.write_bytes(payload)
            os.utime(notice, (0, 0))
            info = builder.finish(stage, root, "epoch-license", {"schema_version": 1})
            with zipfile.ZipFile(root / info["filename"]) as archive:
                self.assertEqual(archive.read("epoch-license/LICENSE.txt"), payload)
                self.assertEqual(archive.getinfo("epoch-license/LICENSE.txt").date_time,
                                 (1980, 1, 1, 0, 0, 0))
            builder.verify_package(root / info["filename"])

    def test_upstream_archive_and_executable_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.exe"
            path.write_bytes(b"not the executable")
            with self.assertRaises(ValueError):
                builder.check_pe(path)
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                builder.check_archive(path, "esptool")

    def test_combined_package_checks_root_and_nested_manifests(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            nested = stage / "CYD-Flasher"
            nested.mkdir(parents=True)
            (nested / "firmware.bin").write_bytes(b"firmware fixture")
            (nested / "SHA256SUMS.txt").write_text("nested checksum fixture\n")
            info = builder.finish(stage, root, "combined", {"schema_version": 1})
            builder.verify_package(root / info["filename"])
            # Regenerating a copied stage must not include its own old root checksum.
            again = builder.finish(stage, root, "combined-again", {"schema_version": 1})
            builder.verify_package(root / again["filename"])
            mutated = root / "mutated-nested.zip"
            with zipfile.ZipFile(root / info["filename"]) as source, zipfile.ZipFile(mutated, "w") as target:
                for name in source.namelist():
                    payload = b"changed" if name.endswith("CYD-Flasher/SHA256SUMS.txt") else source.read(name)
                    target.writestr(name, payload)
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                builder.verify_package(mutated)

    def test_windows_archive_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "unsafe.zip"
            for name in ["kit/../escape", "kit/..\\escape", "C:/escape", "kit/file:stream",
                         "kit/./file", "kit//file", "/kit/file", "kit/file.", "kit/file ",
                         "kit/NUL", "kit/CoN.txt", "kit/PRN", "kit/AUX.json",
                         "kit/COM1.log", "kit/LPT9.txt", "kit/COM¹", "kit/CONIN$",
                         "kit/CONOUT$", "kit/NUL .txt", "kit/bad?name", 'kit/bad"name',
                         "kit/bad\x01name"]:
                with self.subTest(name=name):
                    with zipfile.ZipFile(path, "w") as archive:
                        archive.writestr(name, b"unsafe fixture")
                    with self.assertRaisesRegex(ValueError, "Unsafe ZIP paths"):
                        builder.verify_package(path)

    def test_windows_extraction_aliases_are_rejected_with_valid_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "aliases.zip"
            for names in [["file.txt", "FILE.TXT"], ["folder", "folder/file.txt"],
                          ["Folder", "folder/file.txt"]]:
                with self.subTest(names=names):
                    payloads = [(name, f"payload {index}".encode())
                                for index, name in enumerate(names)]
                    checksums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                                        for name, data in payloads)
                    with zipfile.ZipFile(path, "w") as archive:
                        for name, data in payloads:
                            archive.writestr(f"kit/{name}", data)
                        archive.writestr("kit/SHA256SUMS.txt", checksums)
                    with self.assertRaisesRegex(ValueError, "Unsafe ZIP paths"):
                        builder.verify_package(path)

    def test_windows_ordinary_spaces_and_non_reserved_names_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir()
            for name in ["Muse Video Bridge.exe", "COM10.txt", "console.txt", "auxiliary.json"]:
                (stage / name).write_bytes(b"payload fixture")
            info = builder.finish(stage, root, "kit", {"schema_version": 1})
            builder.verify_package(root / info["filename"])

    def test_localization_keys_and_format_arguments_match(self):
        tr = json.loads((ROOT / "packaging/windows/localization/tr.json").read_text())
        en = json.loads((ROOT / "packaging/windows/localization/en.json").read_text())
        import re
        self.assertEqual(tr.keys(), en.keys())
        for key in tr:
            self.assertTrue(tr[key].strip() and en[key].strip())
            self.assertEqual(re.findall(r"\{\d+\}", tr[key]), re.findall(r"\{\d+\}", en[key]))

    @unittest.skipUnless(PWSH, "PowerShell is required for helper host tests")
    def test_powershell_parser_and_arguments(self):
        code = r"""
        $ErrorActionPreference = 'Stop'
        foreach ($path in $args) {
            $tokens = $null; $errors = $null
            [void][Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
            if ($errors.Count) { throw ($errors -join "`n") }
        }
        . $args[0]
        $root = Join-Path ([IO.Path]::GetTempPath()) 'Test folder'
        $p = Get-FlashPlan $root 'COM12' 'C:\Backup folder\full.bin'
        if ($p.Backup[-1] -ne 'C:\Backup folder\full.bin' -or $p.Backup[-2] -ne '0x400000') { throw 'backup arguments' }
        if ($p.Write[10] -ne 'write-flash' -or $p.Write[11] -ne '-z' -or $p.Verify[10] -ne 'verify-flash') { throw 'command arguments' }
        if ($p.Write[18] -ne '0x1000' -or $p.Write[24] -ne '0x10000') { throw 'flash offsets' }
        $app = Get-ApplicationFlashPlan $root 'COM12' 'C:\Backup folder\full.bin'
        if ($app.Write.Count -ne 20 -or $app.Write[18] -ne '0x10000' -or
            $app.Verify.Count -ne 19 -or $app.Verify[17] -ne '0x10000') { throw 'application-only offsets' }
        foreach ($offset in @('0x1000', '0x8000', '0xe000')) {
            if ($app.Write -contains $offset -or $app.Verify -contains $offset) { throw 'unrelated flash write' }
        }
        foreach ($op in @('erase-flash','erase-region','write-flash-status','write-mem')) {
            if ($p.Write -contains $op -or $p.Backup -contains $op) { throw 'unwanted operation' }
        }
        try { [void](Get-FlashPlan 'C:\Test' 'COM3;calc.exe' 'x'); throw 'port accepted' }
        catch { if ($_.Exception.Message -eq 'port accepted') { throw } }
        $catalog = Get-Catalog (Split-Path $args[0]) 'en'
        $native = Join-Path $PSHOME ('pwsh' + $(if ($env:OS -eq 'Windows_NT') { '.exe' } else { '' }))
        try {
            [void](Invoke-Esptool $native @('-NoProfile','-Command','[Console]::Error.WriteLine("host fixture stderr"); exit 7') $catalog)
            throw 'failed tool accepted'
        } catch {
            if ($_.Exception.Message -notmatch 'exit 7') { throw }
        }
        """
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.ps1"
            path.write_text(code)
            result = subprocess.run([PWSH, "-NoProfile", "-File", str(path), str(ROOT / "packaging/windows/Flash.Common.ps1"), str(ROOT / "packaging/windows/Flash-CYD.ps1")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def run_helper(self, mode, corrupt=None, skip=False, answers="FLASH\n", application_only=False):
        with tempfile.TemporaryDirectory(prefix="mvb-helper-") as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "packaging/windows", root, dirs_exist_ok=True)
            (root / "firmware").mkdir()
            (root / "tools").mkdir()
            entries = []
            for name, offset in builder.IMAGES.items():
                path = root / "firmware" / name
                path.write_bytes(b"checked fixture")
                entries.append({"path": f"firmware/{name}", "offset": offset, "bytes": path.stat().st_size, "sha256": builder.digest(path)})
            tool = root / "tools/esptool.exe"
            tool.write_bytes(b"checked fixture tool - never executed")
            manifest = {"schema_version": 1, "profile": "cyd_ili9341", "chip": "esp32", "flash_size_bytes": 4194304, "flash_mode": "dio", "flash_frequency": "40m", "baud": 115200, "files": entries, "esptool": {"path": "tools/esptool.exe", "sha256": builder.digest(tool)}}
            if corrupt == "offset":
                entries[0]["offset"] = "0x0"
            if corrupt == "hash":
                (root / "firmware/firmware.bin").write_bytes(b"damaged")
            if corrupt == "tool":
                tool.write_bytes(b"damaged")
            (root / "firmware-manifest.json").write_text(json.dumps(manifest))
            # Replace only the physical adapters in this isolated copy. Ship none of these mocks.
            mock = r"""
function Test-WindowsHost { $true }
function Get-CYDPorts { 'COM12' }
function Invoke-Esptool {
    param([string]$Tool, [string[]]$Arguments, [object]$Catalog)
    $operation = $Arguments[10]
    Add-Content -LiteralPath (Join-Path $PSScriptRoot 'calls.txt') -Value $operation
    switch ($operation) {
        'flash-id' {
            if ($env:MVB_FLASH_TEST_MODE -eq 'wrong_flash') { return 'Detected flash size: 2MB' }
            return 'Detected flash size: 4MB'
        }
        'read-flash' {
            if ($env:MVB_FLASH_TEST_MODE -eq 'backup_error') { throw 'host test: read failed' }
            $file = [IO.File]::Create($Arguments[-1])
            try {
                if ($env:MVB_FLASH_TEST_MODE -eq 'short_backup') { $file.SetLength(65536) }
                else { $file.SetLength(4194304) }
                if ($file.Length -ge 0x9000) {
                    $partition = [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'firmware/partitions.bin'))
                    if ($env:MVB_FLASH_TEST_MODE -eq 'partition_mismatch') { $partition[0] = $partition[0] -bxor 1 }
                    [void]$file.Seek(0x8000, [IO.SeekOrigin]::Begin)
                    $file.Write($partition, 0, $partition.Length)
                }
            } finally { $file.Dispose() }
            return 'host fixture read'
        }
        'write-flash' { return 'host fixture write' }
        'verify-flash' {
            if ($env:MVB_FLASH_TEST_MODE -eq 'verify_error') { throw 'host test: verify failed' }
            return 'host fixture comparison'
        }
        default { throw 'unexpected operation' }
    }
}
"""
            with (root / "Flash.Common.ps1").open("a") as file:
                file.write(mock)
            command = [PWSH, "-NoProfile", "-File", str(root / "Flash-CYD.ps1"), "-Language", "en", "-Port", "COM12"]
            if skip:
                command.append("-SkipBackup")
            if application_only:
                command.append("-ApplicationOnly")
            result = subprocess.run(command, input=answers, env={**os.environ, "MVB_FLASH_TEST_MODE": mode}, capture_output=True, text=True)
            calls = (root / "calls.txt").read_text().splitlines() if (root / "calls.txt").exists() else []
            return result, calls

    @unittest.skipUnless(PWSH, "PowerShell is required for helper host tests")
    def test_helper_backup_failures_never_write(self):
        for mode in ["backup_error", "short_backup"]:
            with self.subTest(mode=mode):
                result, calls = self.run_helper(mode)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(calls, ["flash-id", "read-flash"])
                self.assertNotIn("Flash verification passed", result.stdout)

    @unittest.skipUnless(PWSH, "PowerShell is required for helper host tests")
    def test_helper_corruption_rejected_before_identification(self):
        for corrupt in ["hash", "offset", "tool"]:
            with self.subTest(corrupt=corrupt):
                result, calls = self.run_helper("success", corrupt=corrupt)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(calls, [])

    @unittest.skipUnless(PWSH, "PowerShell is required for helper host tests")
    def test_helper_success_requires_backup_and_flash_verification(self):
        result, calls = self.run_helper("success")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, ["flash-id", "read-flash", "write-flash", "verify-flash"])
        self.assertIn("Flash verification passed", result.stdout)
        result, calls = self.run_helper("verify_error")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Flash verification passed", result.stdout)

    @unittest.skipUnless(PWSH, "PowerShell is required for helper host tests")
    def test_helper_wrong_flash_and_cancel_never_write(self):
        result, calls = self.run_helper("wrong_flash")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, ["flash-id"])
        result, calls = self.run_helper("success", answers="\n")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, ["flash-id"])

    @unittest.skipUnless(PWSH, "PowerShell is required for helper host tests")
    def test_helper_skip_requires_separate_confirmation(self):
        result, calls = self.run_helper("success", skip=True, answers="FLASH\n\n")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, ["flash-id"])
        result, calls = self.run_helper("success", skip=True, answers="FLASH\nSKIP\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, ["flash-id", "write-flash", "verify-flash"])

    @unittest.skipUnless(PWSH, "PowerShell is required for helper host tests")
    def test_application_only_requires_matching_partition_and_complete_backup(self):
        result, calls = self.run_helper("success", application_only=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, ["flash-id", "read-flash", "write-flash", "verify-flash"])
        self.assertIn("Writing only firmware.bin in app0", result.stdout)
        for mode in ["partition_mismatch", "short_backup", "backup_error"]:
            with self.subTest(mode=mode):
                result, calls = self.run_helper(mode, application_only=True)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(calls, ["flash-id", "read-flash"])
        result, calls = self.run_helper("success", skip=True, application_only=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
