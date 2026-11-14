#!/usr/bin/env python3
"""Package an optimized, asset-embedded Linux Tauri build without system libraries."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'release-artifacts')
    parser.add_argument('--dependency-notices', type=Path, required=True)
    args = parser.parse_args()
    version = json.loads((ROOT / 'src-tauri/tauri.conf.json').read_text())['version']
    binary = ROOT / 'target/release/muse-video-bridge'
    if not binary.is_file():
        parser.error('Run the optimized Tauri release build first')
    if binary.read_bytes()[:4] != b'\x7fELF':
        parser.error('Linux ELF executable required')
    dependencies = subprocess.run(['ldd', str(binary)], check=True, text=True,
                                  capture_output=True).stdout
    if 'not found' in dependencies:
        parser.error('Release binary has unresolved runtime libraries')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = f'Muse-Video-Bridge-{version}-Linux-x86_64'
    with tempfile.TemporaryDirectory(prefix='muse-linux-release-') as temporary:
        stage = Path(temporary) / name
        stage.mkdir()
        shutil.copy2(binary, stage / 'muse-video-bridge')
        shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE')
        shutil.copy2(ROOT / 'docs/INSTALL.md', stage / 'README.md')
        shutil.copytree(args.dependency_notices, stage / 'licenses')
        shutil.copy2(ROOT / 'src-tauri/icons/icon.png', stage / 'icon.png')
        (stage / 'portable-mode.json').write_text('{"schema_version":1}\n')
        launcher = stage / 'Start-App.sh'
        launcher.write_text('#!/bin/sh\nset -eu\napp_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$app_dir/muse-video-bridge" "$@"\n')
        launcher.chmod(0o755)
        hashes = []
        for file in sorted(stage.rglob('*')):
            if file.is_file():
                with file.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                hashes.append(f'{digest}  {file.relative_to(stage).as_posix()}')
        (stage / 'SHA256SUMS.txt').write_text('\n'.join(hashes) + '\n')
        archive_path = output / f'{name}.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            archive.add(stage, arcname=name)
        print(archive_path)
    for extension, source_name, target_name in [
        ('deb', f'Muse Video Bridge_{version}_amd64.deb', f'Muse-Video-Bridge-{version}-Linux-amd64.deb'),
        ('rpm', f'Muse Video Bridge-{version}-1.x86_64.rpm', f'Muse-Video-Bridge-{version}-Linux-x86_64.rpm'),
    ]:
        source = ROOT / 'target/release/bundle' / extension / source_name
        if source.is_file():
            destination = output / target_name
            shutil.copy2(source, destination)
            print(destination)


if __name__ == '__main__':
    main()
