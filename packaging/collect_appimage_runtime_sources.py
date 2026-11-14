#!/usr/bin/env python3
"""Package verified AppImage runtime sources, Alpine patches and runtime notices."""
import argparse
import hashlib
import json
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

RUNTIME_COMMIT = "fedfad51acc2860b2916a1eef32ba4a500ff31ef"
APORTS_COMMIT = "8f01bd5af18e4f66b7df105bdd7cab10de2ee1fa"
RUNTIME_SHA256 = "617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46"
ALPINE_DIGEST = "ce64758a109eb420d874a118f87920e625e12d3634e03b4a5573fd9f6e5d3507"
SOURCES = [
    ("type2-runtime.tar.gz", "sha256", "e27ea3c837e9c660e1a9a2993b673ae93bbb53077330efba8654031e302d4e8e", "https://github.com/AppImage/type2-runtime/archive/" + RUNTIME_COMMIT + ".tar.gz"),
    ("fuse-3.15.0.tar.xz", "sha256", "70589cfd5e1cff7ccd6ac91c86c01be340b227285c5e200baa284e401eea2ca0", "https://github.com/libfuse/libfuse/releases/download/fuse-3.15.0/fuse-3.15.0.tar.xz"),
    ("squashfuse-0.5.2.tar.gz", "sha256", "db0238c5981dabbd80ee09ae15387f390091668ca060a7bc38047912491443d3", "https://github.com/vasi/squashfuse/archive/0.5.2.tar.gz"),
    ("musl-1.2.5.tar.gz", "sha512", "7bb7f7833923cd69c7a1a9b8a5f1784bfd5289663eb6061dcd43d583e45987df8a68a1be05d75cc1c88a3f5b610653d1a70f4a9cff4d8f7fd41ae73ee058c17c", "https://musl.libc.org/releases/musl-1.2.5.tar.gz"),
    ("zstd-1.5.6.tar.gz", "sha512", "ca12dffd86618ca008e1ecc79056c1129cb4e61668bf13a3cd5b2fa5c93bc9c92c80f64c1870c68b9c20009d9b3a834eac70db72242d5106125a1c53cccf8de8", "https://github.com/facebook/zstd/archive/v1.5.6.tar.gz"),
    ("zlib-1.3.2.tar.gz", "sha512", "70963771ea5d763614278a69b474f09b7d237ef8f53b675a10fe31d9923aeef601504b35d7ebd1b1e7f347e9ebb048e6b3b47fffdf137e7bdc7e8d5eb4ec4692", "https://zlib.net/fossils/zlib-1.3.2.tar.gz"),
    ("mimalloc-2.1.7.tar.gz", "sha512", "4e30976758015c76a146acc1bfc8501e2e5c61b81db77d253de0d58a8edef987669243f232210667b32ef8da3a33286642acb56ba526fd24c4ba925b44403730", "https://github.com/microsoft/mimalloc/archive/v2.1.7/mimalloc-2.1.7.tar.gz"),
    ("aports.tar.gz", "sha256", "c466ec757fe4779c4912c05a9d6312ce6c2cbd951df513b32dfc4be9bac3c059", "https://github.com/alpinelinux/aports/archive/" + APORTS_COMMIT + ".tar.gz"),
    ("GCC-COPYING.RUNTIME", "sha256", "9d6b43ce4d8de0c878bf16b54d8e7a10d9bd42b75178153e3af6a815bdc90f74", "https://raw.githubusercontent.com/gcc-mirror/gcc/releases/gcc-14.2.0/COPYING.RUNTIME"),
    ("GCC-COPYING3", "sha256", "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903", "https://raw.githubusercontent.com/gcc-mirror/gcc/releases/gcc-14.2.0/COPYING3"),
]


def checksum(path, algorithm="sha256"):
    with path.open("rb") as file:
        return hashlib.file_digest(file, algorithm).hexdigest()


def archive_file(archive, name, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    file = archive.extractfile(name)
    if file is None:
        raise ValueError("Source license is not a regular file: " + name)
    target.write_bytes(file.read())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True,
                        help="Directory containing downloads listed in SOURCES")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name, algorithm, expected, _ in SOURCES:
        if checksum(args.inputs / name, algorithm) != expected:
            parser.error("Pinned source checksum mismatch: " + name)
    args.output.mkdir(parents=True, exist_ok=True)
    notices = args.output / "appimage-runtime-notices"
    if notices.exists():
        parser.error("Notice directory already exists; use a fresh output directory")
    notices.mkdir()
    with tempfile.TemporaryDirectory(prefix="muse-runtime-source-") as temporary:
        stage = Path(temporary) / "AppImage-runtime-sources"
        (stage / "archives").mkdir(parents=True)
        for name, _, _, _ in SOURCES:
            if name != "aports.tar.gz":
                shutil.copyfile(args.inputs / name, stage / "archives" / name)
        packages = {"main/musl", "main/zstd", "main/zlib", "community/mimalloc2"}
        with tarfile.open(args.inputs / "aports.tar.gz") as archive:
            for member in archive.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) == 2 and any(parts[1].startswith(package + "/") for package in packages) and member.isfile():
                    archive_file(archive, member.name, stage / "alpine-recipes" / parts[1])
        for package in packages:
            recipe = stage / "alpine-recipes" / package
            entries = re.findall(r"^([0-9a-f]{128})  (.+)$", (recipe / "APKBUILD").read_text(), re.M)
            for expected, name in entries:
                path = stage / "archives" / name if name.endswith(".tar.gz") else recipe / name
                if checksum(path, "sha512") != expected:
                    raise ValueError("Alpine recipe source/patch checksum mismatch: " + str(path))
        licenses = [
            ("type2-runtime.tar.gz", "type2-runtime-" + RUNTIME_COMMIT + "/LICENSE", "type2-runtime-LICENSE.txt"),
            ("fuse-3.15.0.tar.xz", "fuse-3.15.0/LICENSE", "libfuse-LICENSE.txt"),
            ("fuse-3.15.0.tar.xz", "fuse-3.15.0/LGPL2.txt", "libfuse-LGPL2.txt"),
            ("fuse-3.15.0.tar.xz", "fuse-3.15.0/GPL2.txt", "libfuse-GPL2.txt"),
            ("squashfuse-0.5.2.tar.gz", "squashfuse-0.5.2/LICENSE", "squashfuse-LICENSE.txt"),
            ("musl-1.2.5.tar.gz", "musl-1.2.5/COPYRIGHT", "musl-COPYRIGHT.txt"),
            ("zstd-1.5.6.tar.gz", "zstd-1.5.6/LICENSE", "zstd-LICENSE.txt"),
            ("zstd-1.5.6.tar.gz", "zstd-1.5.6/COPYING", "zstd-COPYING.txt"),
            ("zlib-1.3.2.tar.gz", "zlib-1.3.2/LICENSE", "zlib-LICENSE.txt"),
            ("mimalloc-2.1.7.tar.gz", "mimalloc-2.1.7/LICENSE", "mimalloc-LICENSE.txt"),
        ]
        for name, member, target in licenses:
            with tarfile.open(args.inputs / name) as archive:
                archive_file(archive, member, notices / target)
        for name in ("GCC-COPYING.RUNTIME", "GCC-COPYING3"):
            shutil.copyfile(args.inputs / name, notices / (name + ".txt"))
        manifest = {
            "runtime_commit": RUNTIME_COMMIT, "runtime_sha256": RUNTIME_SHA256,
            "runtime_build_id": "ef655b05ac26feebfd3bc24ec7ccb30389cefc33",
            "runtime_url": "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64",
            "upstream_build_run": "https://github.com/AppImage/type2-runtime/actions/runs/39320578745/job/118293253809",
            "alpine_aports_commit": APORTS_COMMIT, "alpine_image_digest": ALPINE_DIGEST,
            "libraries": {
                "type2-runtime": {"version": RUNTIME_COMMIT, "license": "MIT"},
                "libfuse": {"version": "3.15.0", "license": "LGPL-2.1", "patch": "type2-runtime/patches/libfuse/mount.c.diff"},
                "squashfuse": {"version": "0.5.2", "license": "BSD-2-Clause"},
                "musl": {"version": "1.2.5-r11", "license": "MIT with retained component notices"},
                "zstd": {"version": "1.5.6-r2", "license": "BSD-3-Clause selected from dual BSD/GPL terms"},
                "zlib": {"version": "1.3.2-r1", "license": "Zlib"},
                "mimalloc": {"version": "2.1.7-r0", "license": "MIT"},
                "GCC compiler support": {"version": "14.2.0-r4", "license": "GPL-3.0 with GCC Runtime Library Exception 3.1"},
            },
            "inputs": [{"filename": name, "algorithm": algorithm, "digest": expected, "url": url} for name, algorithm, expected, url in SOURCES],
            "verification": "Sources/patches checked against exact upstream build recipe and Alpine source checksums; runtime not locally rebuilt",
        }
        for directory in (stage, notices):
            (directory / "provenance.json").write_text(json.dumps(manifest, indent=2) + "\n")
            shutil.copyfile(Path(__file__).with_name("APPIMAGE_RUNTIME_REBUILD.md"), directory / "README.md")
        shutil.copytree(notices, stage / "notices")
        shutil.copyfile(__file__, stage / "collect_appimage_runtime_sources.py")
        shutil.copyfile(Path(__file__).with_name("APPIMAGE_RUNTIME_REBUILD.md"),
                        stage / "APPIMAGE_RUNTIME_REBUILD.md")
        files = sorted(path for path in stage.rglob("*") if path.is_file())
        (stage / "SHA256SUMS.txt").write_text("".join(f"{checksum(file)}  {file.relative_to(stage).as_posix()}\n" for file in files))
        target = args.output / "AppImage-runtime-sources.tar.gz"
        with tarfile.open(target, "x:gz") as archive:
            archive.add(stage, arcname=stage.name)
        with tarfile.open(target) as archive:
            for line in (stage / "SHA256SUMS.txt").read_text().splitlines():
                expected, relative = line.split("  ", 1)
                data = archive.extractfile(stage.name + "/" + relative).read()
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError("Packaged source checksum mismatch: " + relative)
        print(json.dumps({"asset": target.name, "bytes": target.stat().st_size, "sha256": checksum(target)}, indent=2))


if __name__ == "__main__":
    main()
