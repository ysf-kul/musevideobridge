#!/usr/bin/env python3
"""Combine checked Windows packages, a diagnostic CLI and owner test media."""
import argparse
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import build_windows as builder


MEDIA = {
    "baseline": "Baseline-q8-10fps",
    "balanced-candidate": "Balanced-q8-15fps",
    "best-quality-candidate": "Best-Quality-q4-15fps",
    "smooth-candidate": "Smooth-Playback-q10-20fps",
}


def extract_checked(archive_path, destination):
    builder.verify_package(archive_path)
    destination.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.namelist():
            relative = Path(*Path(member).parts[1:])
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))


def checksum_stage(stage):
    checksum = stage / "SHA256SUMS.txt"
    paths = sorted(p for p in stage.rglob("*") if p.is_file() and p != checksum)
    checksum.write_text(
        "".join(f"{builder.digest(p)}  {p.relative_to(stage).as_posix()}\n" for p in paths),
        encoding="ascii",
    )


def pe_imports(path):
    builder.check_pe(path)
    inspected = subprocess.run(
        ["llvm-readobj", "--coff-imports", str(path)],
        check=True, capture_output=True, text=True,
    )
    return [line.strip().removeprefix("Name: ") for line in inspected.stdout.splitlines()
            if line.strip().startswith("Name: ")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable", type=Path, required=True)
    parser.add_argument("--flasher", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--media", type=Path, required=True,
                        help="Encoding evidence directory containing encoding-source and encoding-detailed")
    parser.add_argument("--extra-media", type=Path, action="append",
                        help="Additional benchmark directory containing report.json and profile folders; repeatable")
    parser.add_argument("--guide", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True,
                        help="New staging directory; existing artifacts are never replaced")
    parser.add_argument("--output", type=Path, default=builder.ROOT / "dist-packages")
    parser.add_argument("--revision", default="usb-reliability-2026-11-12")
    parser.add_argument("--name", default="Muse-Video-Bridge-Windows-USB-Test-2026-11-12")
    parser.add_argument("--encoding-fps", type=int, choices=range(1, 31), default=10)
    parser.add_argument("--encoding-qscale", type=int, choices=range(2, 32), default=8)
    parser.add_argument("--encoding-scaling", choices=("bicubic", "area", "lanczos"), default="bicubic")
    parser.add_argument("--encoding-width", type=int, default=320)
    parser.add_argument("--encoding-height", type=int, default=240)
    parser.add_argument("--playback-decoder", choices=("jpegdec", "rom", "tjpgd_fast", "new_jpeg"), default="jpegdec")
    parser.add_argument("--playback-render-mode", choices=("legacy", "blocks", "dma"), default="blocks")
    parser.add_argument("--playback-tft-spi-hz", type=int, choices=(20000000, 40000000, 80000000), default=20000000)
    parser.add_argument("--playback-command-spi-hz", type=int, choices=(10000000, 20000000, 40000000, 80000000), default=20000000)
    parser.add_argument("--playback-big-endian", action="store_true")
    parser.add_argument("--playback-prefetch", action="store_true")
    parser.add_argument("--playback-target-fps", type=int, choices=range(1, 31), default=30)
    parser.add_argument("--playback-decoder-mcus", type=int, choices=range(1, 33), default=20)
    args = parser.parse_args()
    if not 1 <= args.encoding_width <= 320 or not 1 <= args.encoding_height <= 240:
        parser.error("Encoding bounds must fit the 320x240 display")
    cli_imports = pe_imports(args.cli)
    if any(name.lower().startswith(("vcruntime", "msvcp")) for name in cli_imports):
        raise ValueError("Diagnostic CLI must be built with the static CRT")
    args.stage.mkdir(parents=True, exist_ok=False)
    args.output.mkdir(parents=True, exist_ok=True)
    portable = args.stage / "Portable-App"
    flasher = args.stage / "CYD-Flasher"
    extract_checked(args.portable, portable)
    extract_checked(args.flasher, flasher)
    builder.copy(args.cli, portable / "tools/muse-video-cli.exe")
    # Refresh documentation and reproducible source after kit-specific instructions.
    for doc in sorted((builder.ROOT / "docs").rglob("*")):
        if doc.is_file() and doc.suffix in {".md", ".json"}:
            builder.copy(doc, portable / "docs" / doc.relative_to(builder.ROOT / "docs"))
            builder.copy(doc, flasher / "docs" / doc.relative_to(builder.ROOT / "docs"))
    builder.copy(builder.ROOT / "packaging/windows/PORTABLE_README.md", portable / "README.md")
    source = args.stage / "sources/project-source.zip"
    source.parent.mkdir()
    builder.source_zip(source)
    builder.copy(source, portable / "sources/project-source.zip")
    builder.copy(source, flasher / "sources/project-source.zip")
    app_provenance = json.loads((portable / "provenance.json").read_text())
    app_provenance.update({"build_revision": args.revision,
                           "diagnostic_cli_static_crt": True,
                           "diagnostic_cli_imports": cli_imports,
                           "project_source_sha256": builder.digest(source)})
    builder.write_json(portable / "provenance.json", app_provenance)
    checksum_stage(portable)
    flasher_provenance = json.loads((flasher / "provenance.json").read_text())
    flasher_provenance["project_source_sha256"] = builder.digest(source)
    builder.write_json(flasher / "provenance.json", flasher_provenance)
    checksum_stage(flasher)

    media = args.stage / "Test-Media"
    media.mkdir()
    source_video = args.media / "encoding-source/detail-pattern.mp4"
    generation = json.loads((args.media / "encoding-source/generation.json").read_text())
    if builder.digest(source_video) != generation["source_sha256"]:
        raise ValueError("Source video checksum mismatch")
    builder.copy(source_video, media / "SourceVideo.mp4")
    builder.copy(args.media / "encoding-source/generation.json", media / "source-generation.json")
    profiles = []
    for directory, name in MEDIA.items():
        folder = args.media / "encoding-detailed" / directory
        manifest = json.loads((folder / "manifest.json").read_text())
        video = folder / "video.mvb"
        if builder.digest(video) != manifest["sha256"] or video.stat().st_size != manifest["header"]["file_bytes"]:
            raise ValueError(f"Prepared video mismatch: {directory}")
        builder.copy(video, media / f"{name}.mvb")
        builder.copy(folder / "manifest.json", media / f"{name}.json")
        builder.copy(folder / "sample.jpg", media / f"{name}-sample.jpg")
        profiles.append({"file": f"Test-Media/{name}.mvb", "sha256": manifest["sha256"],
                         "bytes": video.stat().st_size, "options": manifest["options"],
                         "windows_physical_quality_or_fps_tested": False})

    for comparison_root in args.extra_media or []:
        comparison = json.loads((comparison_root / "report.json").read_text())
        if comparison["source_sha256"] != generation["source_sha256"]:
            raise ValueError("Additional profiles must use the included source video")
        for profile in comparison["profiles"]:
            name = profile["name"]
            if not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in name):
                raise ValueError("Unsafe additional profile name")
            folder = comparison_root / name
            manifest = json.loads((folder / "manifest.json").read_text())
            video = folder / "video.mvb"
            if builder.digest(video) != manifest["sha256"] or video.stat().st_size != manifest["header"]["file_bytes"]:
                raise ValueError(f"Additional prepared video mismatch: {name}")
            destination = media / f"{name}.mvb"
            if destination.exists():
                raise ValueError(f"Duplicate profile name: {name}")
            builder.copy(video, destination)
            builder.copy(folder / "manifest.json", media / f"{name}.json")
            builder.copy(folder / "sample.jpg", media / f"{name}-sample.jpg")
            profiles.append({"file": f"Test-Media/{name}.mvb", "sha256": manifest["sha256"],
                             "bytes": video.stat().st_size, "options": manifest["options"],
                             "windows_physical_quality_or_fps_tested": False})

    for filename in ["Run-Device-Tests.ps1", "Device.Tests.Common.ps1", "Run-Device-Tests.cmd"]:
        builder.copy(builder.ROOT / "packaging/windows" / filename, args.stage / filename)

    builder.copy(args.guide, args.stage / "README-FIRST.md")
    (args.stage / "Flash-Firmware-English.cmd").write_text(
        '@echo off\ncall "%~dp0CYD-Flasher\\Flash-CYD.cmd" -Language en -ApplicationOnly %*\n',
        encoding="ascii", newline="\r\n",
    )
    (args.stage / "Flash-Performance-English.cmd").write_text(
        '@echo off\ncall "%~dp0CYD-Flasher\\Flash-CYD.cmd" -Language en -ApplicationOnly %*\n',
        encoding="ascii", newline="\r\n",
    )
    (args.stage / "Flash-Full-Firmware-English.cmd").write_text(
        '@echo off\ncall "%~dp0CYD-Flasher\\Flash-CYD.cmd" -Language en %*\n',
        encoding="ascii", newline="\r\n",
    )
    (args.stage / "Start-App.cmd").write_text(
        '@echo off\nstart "" "%~dp0Portable-App\\Muse Video Bridge.exe"\n',
        encoding="ascii", newline="\r\n",
    )
    firmware_manifest = json.loads((flasher / "firmware-manifest.json").read_text())
    for entry in firmware_manifest["files"]:
        path = flasher / entry["path"]
        if path.stat().st_size != entry["bytes"] or builder.digest(path) != entry["sha256"]:
            raise ValueError("Firmware manifest mismatch")
    provenance = {
        "schema_version": 1, "purpose": "private Windows owner USB/playback test kit",
        "build_revision": args.revision,
        "windows_cross_build_verified": True, "windows_runtime_tested": False,
        "windows_physical_flashing_tested": False, "windows_physical_usb_transfer_tested": False,
        "windows_physical_playback_or_visual_quality_tested": False,
        "linux_device_evidence": "Portable-App/docs/REAL_HARDWARE_PERFORMANCE_REPORT.md",
        "inputs": {"portable_zip_sha256": builder.digest(args.portable),
                   "flasher_zip_sha256": builder.digest(args.flasher),
                   "diagnostic_cli_sha256": builder.digest(args.cli)},
        "app_sha256": builder.digest(portable / "Muse Video Bridge.exe"),
        "project_source_sha256": builder.digest(source),
        "firmware": firmware_manifest,
        "default_flash_scope": "app0 only at 0x10000 after full backup and exact partition-table comparison",
        "encoding_default": {"qscale": args.encoding_qscale, "fps": args.encoding_fps,
                             "bounds": [args.encoding_width, args.encoding_height],
                             "scaling": args.encoding_scaling, "chroma": "4:2:0"},
        "playback_default": {"decoder": args.playback_decoder,
                             "render_mode": args.playback_render_mode,
                             "tft_spi_hz": args.playback_tft_spi_hz,
                             "command_spi_hz": args.playback_command_spi_hz,
                             "big_endian": args.playback_big_endian,
                             "prefetch": args.playback_prefetch,
                             "target_fps": args.playback_target_fps,
                             "decoder_mcus": args.playback_decoder_mcus},
        "test_profiles": profiles,
        "protected_documents": {name: builder.digest(builder.ROOT / name)
                                for name in ["MUSE_GUIDELINES.md", "MUSE_DESIGN_SYSTEM.md", "AGENTS.md"]},
    }
    info = builder.finish(args.stage, args.output, args.name, provenance)
    builder.write_json(args.output / f"{args.name}.package.json", info)
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
