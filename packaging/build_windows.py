#!/usr/bin/env python3
"""Build owner-test ZIPs from explicitly supplied, hash-checked build inputs."""
import argparse
import configparser
import hashlib
import json
import re
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    "ffmpeg": {
        "filename": "ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip",
        "url": "https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/latest/ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip",
        "sha256": "17059ded875218b0251977abab6aea21adb6937bb4fd2130c81ada5c89f879d4",
        "license": "LGPL-3.0-or-later shared build; upstream dependencies retain their licenses",
        "source": "https://github.com/FFmpeg/FFmpeg/tree/c179411bbb",
        "build": "https://github.com/FFmpeg-Builds/FFmpeg-Builds",
    },
    "esptool": {
        "filename": "esptool-v5.6.0-windows-amd64.zip",
        "url": "https://github.com/espressif/esptool/releases/download/v5.6.0/esptool-v5.6.0-windows-amd64.zip",
        "sha256": "d55a96fbeb97fd4cb05bd93f201e5c8f6518b7bfcde2fc6966fd0190fd891629",
        "license": "GPL-2.0-or-later",
        "source": "https://github.com/espressif/esptool/tree/v5.6.0",
    },
}
IMAGES = {
    "bootloader.bin": "0x1000",
    "partitions.bin": "0x8000",
    "boot_app0.bin": "0xe000",
    "firmware.bin": "0x10000",
}


def firmware_environments():
    configuration = configparser.ConfigParser(interpolation=None)
    configuration.read(ROOT / "firmware/platformio.ini", encoding="utf-8")
    return tuple(section.removeprefix("env:") for section in configuration.sections()
                 if section.startswith("env:"))


def configured_uart_baud(environment):
    configuration = configparser.ConfigParser(interpolation=None)
    configuration.read(ROOT / "firmware/platformio.ini", encoding="utf-8")
    section = "env:" + environment
    visited = set()
    while section not in visited and section in configuration:
        visited.add(section)
        values = configuration[section]
        if "monitor_speed" in values:
            baud = int(values["monitor_speed"])
            if baud not in (115200, 460800, 921600, 2000000, 3000000):
                raise ValueError("Unsupported configured UART rate; supply the compiled rate explicitly")
            return baud
        section = values.get("extends", "").strip()
    raise ValueError("Cannot resolve UART rate; supply --application-uart-baud explicitly")


def digest(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def check_archive(path, key):
    if digest(path) != ASSETS[key]["sha256"]:
        raise ValueError(f"Upstream archive digest mismatch: {key}")


def check_pe(path):
    with Path(path).open("rb") as file:
        if file.read(2) != b"MZ":
            raise ValueError("Not a Windows executable")
        file.seek(60)
        offset = struct.unpack("<I", file.read(4))[0]
        file.seek(offset)
        if file.read(6) != b"PE\x00\x00\x64\x86":
            raise ValueError("Executable must be Windows x64")


def copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def source_zip(target):
    # Explicit source roots exclude private device data, credentials and generated output.
    roots = ["src", "src-tauri", "crates", "firmware", "public", "localization", "docs", "research", "tests", "packaging"]
    excluded = {".pio", ".dev-tools", ".git", "gen", "__pycache__", ".verification", "node_modules", "target"}
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
        for base in roots:
            for file in sorted((ROOT / base).rglob("*")):
                if file.is_file() and not file.is_symlink() and not excluded.intersection(file.relative_to(ROOT).parts):
                    archive.write(file, file.relative_to(ROOT).as_posix())
        for name in ["README.md", "MUSE_GUIDELINES.md", "MUSE_DESIGN_SYSTEM.md", "AGENTS.md", "Cargo.toml", "Cargo.lock", "package.json", "package-lock.json", "vite.config.ts", "svelte.config.js", "tsconfig.json", "index.html", ".gitignore", ".prettierrc.json", "playwright.config.ts"]:
            archive.write(ROOT / name, name)


def finish(stage, output, name, provenance):
    write_json(stage / "provenance.json", provenance)
    paths = sorted(p for p in stage.rglob("*") if p.is_file() and p != stage / "SHA256SUMS.txt")
    (stage / "SHA256SUMS.txt").write_text(
        "".join(f"{digest(p)}  {p.relative_to(stage).as_posix()}\n" for p in paths), encoding="ascii"
    )
    target = output / f"{name}.zip"
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED, compresslevel=6,
                         strict_timestamps=False) as archive:
        for file in sorted(p for p in stage.rglob("*") if p.is_file()):
            archive.write(file, f"{name}/{file.relative_to(stage).as_posix()}")
    verify_package(target)
    return {"filename": target.name, "bytes": target.stat().st_size, "sha256": digest(target)}


def verify_package(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        windows_devices = {"con", "prn", "aux", "nul", "conin$", "conout$"}
        windows_devices.update(f"{prefix}{number}" for prefix in ["com", "lpt"]
                               for number in "123456789¹²³")
        canonical = set()
        for name in names:
            # Check raw components before pathlib can collapse ./ or repeated
            # separators. Normal Win32 extraction also folds case and strips
            # trailing dots/spaces, so these names must not alias another file.
            parts = name.split("/")
            if any(
                part in {"", ".", ".."} or part.endswith((".", " ")) or
                any(character in '<>:"\\|?*' or ord(character) < 32
                    for character in part) or
                part.split(".", 1)[0].rstrip(" ").casefold() in windows_devices
                for part in parts
            ):
                raise ValueError("Unsafe ZIP paths")
            key = "/".join(part.casefold() for part in parts)
            if key in canonical:
                raise ValueError("Unsafe ZIP paths: extraction aliases")
            canonical.add(key)
        if any("/".join(name.split("/")[:index]) in canonical
               for name in canonical for index in range(1, len(name.split("/")))):
            raise ValueError("Unsafe ZIP paths: file/directory conflict")
        roots = {n.split("/", 1)[0] for n in names}
        if len(roots) != 1:
            raise ValueError("Package must have one enclosing folder")
        prefix = next(iter(roots)) + "/"
        sums = prefix + "SHA256SUMS.txt"
        if sums not in names:
            raise ValueError("Root checksum list is missing")
        listed = set()
        for line in archive.read(sums).decode("ascii").splitlines():
            expected, relative = line.split("  ", 1)
            member = prefix + relative
            if member in listed or not re.fullmatch("[0-9a-f]{64}", expected):
                raise ValueError("Invalid checksum list")
            if hashlib.sha256(archive.read(member)).hexdigest() != expected:
                raise ValueError(f"Package checksum mismatch: {relative}")
            listed.add(member)
        if listed != set(names) - {sums}:
            raise ValueError("Checksum coverage mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--cli", type=Path, help="Optional Windows x64 diagnostic CLI")
    parser.add_argument("--revision", default="private-owner-test")
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--esptool", type=Path, required=True)
    parser.add_argument("--ffmpeg-source", type=Path, required=True)
    parser.add_argument("--esptool-source", type=Path, required=True)
    parser.add_argument("--gpl3", type=Path, required=True)
    parser.add_argument("--boot-app0", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, default=ROOT / "firmware/.pio/build/cyd_ili9341")
    parser.add_argument("--firmware-version", default="0.2.1")
    parser.add_argument("--firmware-environment", choices=firmware_environments(),
                        default="cyd_ili9341")
    parser.add_argument("--application-uart-baud", type=int,
                        choices=(115200, 460800, 921600, 2000000, 3000000),
                        help="Explicit compiled runtime UART baud for inherited/final profiles")
    parser.add_argument("--output", type=Path, default=ROOT / "dist-packages")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.firmware_version):
        parser.error("--firmware-version must be a three-part numeric version")
    check_pe(args.exe)
    if args.cli:
        check_pe(args.cli)
    check_archive(args.ffmpeg, "ffmpeg")
    check_archive(args.esptool, "esptool")
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mvb-packaging-") as temporary:
        root = Path(temporary)
        sources = root / "project-source.zip"
        source_zip(sources)
        portable = root / "portable"
        portable.mkdir()
        copy(args.exe, portable / "Muse Video Bridge.exe")
        copy(ROOT / "packaging/windows/Start-App.cmd", portable / "Start-App.cmd")
        if args.cli:
            copy(args.cli, portable / "tools/muse-video-cli.exe")
        write_json(portable / "portable-mode.json", {"schema_version": 1})
        copy(ROOT / "packaging/windows/PORTABLE_README.md", portable / "README.md")
        copy(ROOT / "packaging/windows/THIRD_PARTY.md", portable / "THIRD_PARTY.md")
        with zipfile.ZipFile(args.ffmpeg) as archive:
            prefix = "ffmpeg-n9.0-latest-win64-lgpl-shared-9.0/"
            members = [n for n in archive.namelist() if n.startswith(prefix + "bin/") and n.endswith((".exe", ".dll")) and not n.endswith("ffplay.exe")]
            for member in members:
                path = portable / "tools" / Path(member).name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(member))
                if path.suffix == ".exe":
                    check_pe(path)
            for filename in ["LICENSE.txt", "doc/ffmpeg.html", "doc/ffprobe.html", "doc/ffmpeg-scaler.html"]:
                path = portable / "licenses/ffmpeg" / Path(filename).name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(prefix + filename))
            configuration = next(s.decode() for s in re.findall(rb"[ -~]{12,}", (portable / "tools/ffmpeg.exe").read_bytes()) if s.startswith(b"--prefix="))
            if "--enable-gpl" in configuration or "--enable-nonfree" in configuration or "--enable-shared" not in configuration:
                raise ValueError("FFmpeg build must be shared LGPL")
            (portable / "licenses/ffmpeg/build-configuration.txt").write_text(configuration + "\n", encoding="utf-8")
        copy(args.gpl3, portable / "licenses/ffmpeg/COPYING.GPLv3")
        copy(args.ffmpeg_source, portable / "sources/ffmpeg-source.tar.gz")
        copy(sources, portable / "sources/project-source.zip")
        copy(ROOT / "public/fonts/OFL.txt", portable / "licenses/Manrope-OFL.txt")
        for doc in sorted((ROOT / "docs").rglob("*")):
            if doc.is_file() and doc.suffix in {".md", ".json"}:
                copy(doc, portable / "docs" / doc.relative_to(ROOT / "docs"))
        common_provenance = {"schema_version": 1, "purpose": "private owner test build", "desktop_version": "0.3.0", "build_revision": args.revision, "app_sha256": digest(args.exe), "project_source_sha256": digest(sources), "windows_runtime_tested": False, "windows_physical_playback_tested": False, "linux_device_evidence": "docs/REAL_HARDWARE_PERFORMANCE_REPORT.md"}
        p = finish(portable, args.output, "Muse-Video-Bridge-0.3.0-Windows-x64-portable", {**common_provenance, "ffmpeg": ASSETS["ffmpeg"], "ffmpeg_source_sha256": digest(args.ffmpeg_source)})
        flasher = root / "flasher"
        flasher.mkdir()
        for filename in ["Flash-CYD.ps1", "Flash.Common.ps1", "Flash-CYD.cmd", "Flash-Firmware-English.cmd", "Flash-Performance-English.cmd", "Flash-Full-Firmware-English.cmd", "localization/en.json", "localization/tr.json", "THIRD_PARTY.md"]:
            copy(ROOT / "packaging/windows" / filename, flasher / filename)
        copy(ROOT / "packaging/windows/FLASHER_README.md", flasher / "README.md")
        entries = []
        for filename, offset in IMAGES.items():
            source = args.boot_app0 if filename == "boot_app0.bin" else args.firmware / filename
            copy(source, flasher / "firmware" / filename)
            entries.append({"path": f"firmware/{filename}", "offset": offset, "bytes": source.stat().st_size, "sha256": digest(source)})
        with zipfile.ZipFile(args.esptool) as archive:
            for filename, target in [("esptool.exe", "tools/esptool.exe"), ("LICENSE", "licenses/esptool/LICENSE"), ("README.md", "licenses/esptool/README.md")]:
                path = flasher / target
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read("esptool-windows-amd64/" + filename))
        check_pe(flasher / "tools/esptool.exe")
        runtime_baud = args.application_uart_baud or configured_uart_baud(args.firmware_environment)
        write_json(flasher / "firmware-manifest.json", {"schema_version": 1, "profile": "cyd_ili9341", "firmware_version": args.firmware_version, "build_environment": args.firmware_environment, "compiler_optimization": "Os" if args.firmware_environment == "cyd_ili9341" else "O3", "chip": "esp32", "flash_size_bytes": 4194304, "flash_mode": "dio", "flash_frequency": "40m", "baud": 115200, "application_uart_baud": runtime_baud, "files": entries, "application_only_update": {"offset": "0x10000", "requires_complete_backup": True, "requires_matching_partition_table": True}, "esptool": {"path": "tools/esptool.exe", "sha256": digest(flasher / "tools/esptool.exe")}})
        copy(args.esptool_source, flasher / "sources/esptool-source.tar.gz")
        copy(sources, flasher / "sources/project-source.zip")
        for lib, filename in [("ArduinoJson", "LICENSE.txt"), ("JPEGDEC", "LICENSE"), ("TFT_eSPI", "license.txt")]:
            copy(ROOT / f"firmware/.pio/libdeps/cyd_ili9341/{lib}/{filename}", flasher / f"licenses/firmware/{lib}.txt")
        # These notices accompany the bundled experimental decoder code and
        # archives. Including them does not assert that every profile links them.
        for vendor, notice in [("tjpgd_fast", "provenance.json"), ("esp_new_jpeg", "PROVENANCE.json")]:
            copy(ROOT / f"firmware/vendor/{vendor}/LICENSE", flasher / f"licenses/firmware/{vendor}.txt")
            copy(ROOT / f"firmware/vendor/{vendor}/{notice}", flasher / f"licenses/firmware/{vendor}-provenance.json")
        for doc in sorted((ROOT / "docs").rglob("*")):
            if doc.is_file() and doc.suffix in {".md", ".json"}:
                copy(doc, flasher / "docs" / doc.relative_to(ROOT / "docs"))
        f = finish(flasher, args.output, f"Muse-Video-Bridge-CYD-Windows-Flasher-{args.firmware_version}", {**common_provenance, "esptool": ASSETS["esptool"], "esptool_source_sha256": digest(args.esptool_source), "firmware_build_environment": args.firmware_environment, "flash_mode_source": f"PlatformIO {args.firmware_environment} BOARD_FLASH_MODE=dio, BOARD_F_FLASH=40000000", "offsets_source": "PlatformIO FLASH_EXTRA_IMAGES and ESP32_APP_OFFSET"})
    write_json(args.output / "packages.json", {"schema_version": 1, "packages": [p, f]})
    print(json.dumps([p, f], indent=2))


if __name__ == "__main__":
    main()
