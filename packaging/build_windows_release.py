#!/usr/bin/env python3
"""Assemble the production portable application from optimized, inspected inputs."""
import argparse
import json
import re
import shlex
import shutil
import tempfile
from pathlib import Path

from build_windows import ROOT, check_pe, copy, digest, finish, write_json
from build_ffmpeg_windows import CONFIGURATION, SOURCE_COMMIT, SOURCE_SHA256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--ffmpeg-install", type=Path, required=True)
    parser.add_argument("--ffmpeg-source-directory", type=Path, required=True)
    parser.add_argument("--ffmpeg-source-archive", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True,
                        help="LLVM-MinGW toolchain supplying linked runtime notices")
    parser.add_argument("--dependency-notices", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())["version"]
    check_pe(args.exe)
    if digest(args.ffmpeg_source_archive) != SOURCE_SHA256:
        parser.error("FFmpeg corresponding source checksum mismatch")
    manifest = json.loads((args.ffmpeg_install / "release-build.json").read_text())
    if (manifest["source_commit"] != SOURCE_COMMIT or
            manifest["license"] != "LGPL-2.1-or-later" or
            manifest["external_codec_libraries"]):
        parser.error("Unexpected FFmpeg release build manifest")
    strings = re.findall(rb"[ -~]{12,}", (args.ffmpeg_install / "bin/ffmpeg.exe").read_bytes())
    configurations = [shlex.split(item.decode()) for item in strings if item.startswith(b"--prefix=")]
    if len(configurations) != 1 or set(configurations[0][1:]) != set(CONFIGURATION):
        parser.error("FFmpeg executable configuration differs from the release recipe")
    source_extensions = {".rs", ".c", ".cpp", ".h", ".ts", ".js", ".svelte", ".py"}
    if any(file.suffix in source_extensions for file in args.dependency_notices.rglob("*") if file.is_file()):
        parser.error("Dependency notice input contains development source files")
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mvb-windows-production-") as temporary:
        stage = Path(temporary)
        copy(args.exe, stage / "Muse Video Bridge.exe")
        copy(ROOT / "packaging/windows/Start-App.cmd", stage / "Start-App.cmd")
        copy(ROOT / "packaging/windows/RELEASE_README.md", stage / "README.md")
        copy(ROOT / "LICENSE", stage / "LICENSE.txt")
        copy(ROOT / "public/fonts/OFL.txt", stage / "licenses/Manrope-OFL.txt")
        copy(args.toolchain / "LICENSE.TXT", stage / "licenses/llvm-mingw/LICENSE.TXT")
        for file in sorted((args.toolchain / "x86_64-w64-mingw32/share/mingw32").glob("COPYING*")):
            copy(file, stage / "licenses/llvm-mingw" / file.name)
        shutil.copytree(args.dependency_notices, stage / "licenses/desktop")
        write_json(stage / "portable-mode.json", {"schema_version": 1})
        for file in sorted((args.ffmpeg_install / "bin").iterdir()):
            if file.suffix.lower() in {".exe", ".dll"}:
                check_pe(file)
                copy(file, stage / "tools" / file.name)
        for required in ("ffmpeg.exe", "ffprobe.exe"):
            if not (stage / "tools" / required).is_file():
                parser.error("Missing required conversion tool: " + required)
        for name in ("COPYING.LGPLv2.1", "COPYING.LGPLv3", "COPYING.GPLv2", "COPYING.GPLv3", "LICENSE.md"):
            copy(args.ffmpeg_source_directory / name, stage / "licenses/ffmpeg" / name)
        copy(args.ffmpeg_install / "release-build.json", stage / "licenses/ffmpeg/release-build.json")
        (stage / "licenses/ffmpeg/SOURCE.md").write_text(
            "FFmpeg is unmodified, built from commit " + SOURCE_COMMIT + ".\n\n"
            "Replaceable shared libraries are licensed LGPL 2.1 or later. "
            "No external codec libraries, GPL build flags or nonfree flags are enabled.\n\n"
            "The exact corresponding source is the FFmpeg-source.tar.gz asset on the same Muse release:\n"
            "https://github.com/ysf-kul/musevideobridge/releases/tag/v" + version + "\n\n"
            "Source SHA-256: " + SOURCE_SHA256 + "\n\n"
            "Build instructions are packaging/build_ffmpeg_windows.py in the Muse source repository. "
            "You may replace/rebuild these tools and DLLs; the Muse app launches them as separate processes.\n",
            encoding="utf-8",
        )
        provenance = {
            "schema_version": 1, "purpose": "production portable distribution",
            "desktop_version": version, "optimization": "Cargo release",
            "app_sha256": digest(args.exe), "windows_runtime_tested": False,
            "windows_usb_hardware_tested": False, "ffmpeg": manifest,
        }
        result = finish(stage, args.output,
                        f"Muse-Video-Bridge-{version}-Windows-x64-portable", provenance)
    source_target = args.output / "FFmpeg-source.tar.gz"
    if source_target.exists():
        if digest(source_target) != SOURCE_SHA256:
            parser.error("Existing FFmpeg source asset has a different checksum")
    else:
        copy(args.ffmpeg_source_archive, source_target)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
