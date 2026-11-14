#!/usr/bin/env python3
"""Build an AppImage using system GTK/WebKitGTK and a verified type-2 runtime."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SHA256 = '617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46'
RUNTIME_COMMIT = 'fedfad51acc2860b2916a1eef32ba4a500ff31ef'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--mksquashfs', type=Path, default=Path('mksquashfs'))
    parser.add_argument('--runtime-notices', type=Path, required=True)
    parser.add_argument('--dependency-notices', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'release-artifacts')
    args = parser.parse_args()
    with args.runtime.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != RUNTIME_SHA256:
            parser.error('AppImage runtime checksum mismatch')
    binary = ROOT / 'target/release/muse-video-bridge'
    if not binary.is_file() or binary.read_bytes()[:4] != b'\x7fELF':
        parser.error('Optimized asset-embedded Linux ELF build required')
    version = json.loads((ROOT / 'src-tauri/tauri.conf.json').read_text())['version']
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='muse-appimage-') as temporary:
        base = Path(temporary)
        stage = base / 'Muse-Video-Bridge.AppDir'
        (stage / 'usr/bin').mkdir(parents=True)
        shutil.copy2(binary, stage / 'usr/bin/muse-video-bridge')
        shutil.copy2(ROOT / 'src-tauri/icons/icon.png', stage / 'muse-video-bridge.png')
        shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE')
        shutil.copy2(ROOT / 'docs/INSTALL.md', stage / 'INSTALL.md')
        shutil.copytree(args.dependency_notices, stage / 'licenses/desktop')
        shutil.copytree(args.runtime_notices, stage / 'licenses/appimage-runtime')
        (stage / 'muse-video-bridge.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Muse Video Bridge\n'
            'Comment=Prepare and transfer video for ESP32 CYD playback\n'
            'Exec=muse-video-bridge\nIcon=muse-video-bridge\nTerminal=false\nCategories=Utility;\n')
        (stage / '.DirIcon').symlink_to('muse-video-bridge.png')
        runner = stage / 'AppRun'
        runner.write_text('#!/bin/sh\nset -eu\napp_dir=${APPDIR:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}\nexec "$app_dir/usr/bin/muse-video-bridge" "$@"\n')
        runner.chmod(0o755)
        (stage / 'appimage-provenance.json').write_text(json.dumps({
            'version': version, 'architecture': 'x86_64',
            'runtime_commit': RUNTIME_COMMIT, 'runtime_sha256': RUNTIME_SHA256,
            'system_dependencies': ['GTK3', 'WebKitGTK 4.1', 'libudev', 'FFmpeg/FFprobe 8+'],
            'data_storage': 'normal XDG config/cache under dev.muse.videobridge',
        }, indent=2) + '\n')
        squashfs = base / 'payload.squashfs'
        subprocess.run([str(args.mksquashfs), str(stage), str(squashfs),
                        '-noappend', '-comp', 'zstd', '-all-root', '-no-xattrs',
                        '-processors', '2'], check=True)
        output = args.output / f'Muse-Video-Bridge-{version}-Linux-x86_64.AppImage'
        with output.open('wb') as target:
            for source in [args.runtime, squashfs]:
                with source.open('rb') as stream:
                    shutil.copyfileobj(stream, target)
        output.chmod(0o755)
        print(output)


if __name__ == '__main__':
    main()
