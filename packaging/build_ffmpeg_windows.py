#!/usr/bin/env python3
"""Cross-build the release's replaceable LGPL FFmpeg tools without external codecs."""
import argparse
import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

SOURCE_COMMIT = "c179411bbb2864cc71323d3055acda25d5c2aace"
SOURCE_SHA256 = "4270bde68c027b376899f26f7973e213aeaa5ddb6fef8a352e0d6ee457766c67"
TOOLCHAIN_URL = "https://github.com/mstorsjo/llvm-mingw/releases/download/20261104/llvm-mingw-20261104-ucrt-ubuntu-22.04-x86_64.tar.xz"
TOOLCHAIN_SHA256 = "919380403de87dca3c581c2053230cd298af5e891286628ba0613b7fed370926"
CONFIGURATION = [
    "--arch=x86_64", "--target-os=mingw32",
    "--cross-prefix=x86_64-w64-mingw32-",
    "--cc=x86_64-w64-mingw32-clang", "--cxx=x86_64-w64-mingw32-clang++",
    "--enable-cross-compile", "--disable-autodetect", "--disable-everything",
    "--disable-network", "--disable-doc", "--disable-debug", "--disable-asm",
    "--disable-avdevice", "--disable-ffplay", "--disable-static", "--enable-shared",
    "--enable-ffmpeg", "--enable-ffprobe", "--enable-decoders", "--enable-parsers",
    "--enable-encoder=mjpeg,wrapped_avframe", "--enable-demuxer=mov,mjpeg",
    "--enable-muxer=image2pipe,null",
    "--enable-filter=bwdif,fps,scale,format,pad,crop,null",
    "--enable-protocol=file,pipe",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Exact FFmpeg GitHub source archive at the pinned commit")
    parser.add_argument("--toolchain", type=Path, required=True,
                        help="Extracted, checksum-verified LLVM-MinGW UCRT toolchain")
    parser.add_argument("--output", type=Path, required=True,
                        help="New build directory; existing data is never removed")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    with args.source.open("rb") as source_file:
        digest = hashlib.file_digest(source_file, "sha256").hexdigest()
    if digest != SOURCE_SHA256:
        parser.error("FFmpeg source checksum differs from the pinned release source")
    if not 1 <= args.jobs <= 64:
        parser.error("--jobs must be between 1 and 64")
    compiler = args.toolchain.resolve() / "bin/x86_64-w64-mingw32-clang"
    if not compiler.is_file():
        parser.error("LLVM-MinGW x86_64 compiler not found")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with tarfile.open(args.source) as archive:
        archive.extractall(output, filter="data")
    source = output / ("FFmpeg-" + SOURCE_COMMIT)
    build = output / "build"
    build.mkdir()
    install = output / "install"
    env = dict(os.environ)
    env["PATH"] = str(args.toolchain.resolve() / "bin") + os.pathsep + env["PATH"]
    commands = [
        [str(source / "configure"), "--prefix=" + str(install), *CONFIGURATION],
        ["make", "-j" + str(args.jobs)],
        ["make", "install"],
    ]
    for index, command in enumerate(commands):
        with (output / f"build-{index}.log").open("wb") as log:
            subprocess.run(command, cwd=build, env=env, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
    (install / "release-build.json").write_text(json.dumps({
        "source_commit": SOURCE_COMMIT, "source_sha256": SOURCE_SHA256,
        "source_url": "https://github.com/FFmpeg/FFmpeg/archive/" + SOURCE_COMMIT + ".tar.gz",
        "toolchain_url": TOOLCHAIN_URL, "toolchain_sha256": TOOLCHAIN_SHA256,
        "configuration": CONFIGURATION, "license": "LGPL-2.1-or-later",
        "external_codec_libraries": False, "windows_runtime_tested": False,
    }, indent=2) + "\n", encoding="utf-8")
    print(install)


if __name__ == "__main__":
    main()
