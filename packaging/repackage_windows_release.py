#!/usr/bin/env python3
"""Package a fresh desktop with unchanged tools from a verified production ZIP."""
import argparse
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from build_ffmpeg_windows import SOURCE_SHA256
from build_windows import ROOT, check_pe, copy, digest, finish, verify_package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--previous-package', type=Path, required=True)
    parser.add_argument('--previous-sha256', required=True)
    parser.add_argument('--ffmpeg-source', type=Path, required=True)
    parser.add_argument('--dependency-notices', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if digest(args.previous_package) != args.previous_sha256:
        parser.error('Previous published ZIP checksum mismatch')
    if digest(args.ffmpeg_source) != SOURCE_SHA256:
        parser.error('FFmpeg corresponding source checksum mismatch')
    check_pe(args.exe)
    verify_package(args.previous_package)
    version = json.loads((ROOT / 'src-tauri/tauri.conf.json').read_text())['version']
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mvb-windows-repackage-') as temporary:
        stage = Path(temporary)
        with zipfile.ZipFile(args.previous_package) as archive:
            prefix = archive.namelist()[0].split('/', 1)[0] + '/'
            previous = json.loads(archive.read(prefix + 'provenance.json'))
            if previous.get('purpose') != 'production portable distribution':
                parser.error('A production portable ZIP is required')
            for name in archive.namelist():
                relative = name.removeprefix(prefix)
                if relative.startswith(('tools/', 'licenses/ffmpeg/', 'licenses/llvm-mingw/')):
                    target = stage / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        manifest = json.loads((stage / 'licenses/ffmpeg/release-build.json').read_text())
        if (manifest['source_sha256'] != SOURCE_SHA256 or
                manifest['license'] != 'LGPL-2.1-or-later' or
                manifest['external_codec_libraries']):
            parser.error('Unexpected conversion tools or corresponding source')
        for required in ['ffmpeg.exe', 'ffprobe.exe']:
            check_pe(stage / 'tools' / required)
        copy(args.exe, stage / 'Muse Video Bridge.exe')
        copy(ROOT / 'packaging/windows/Start-App.cmd', stage / 'Start-App.cmd')
        copy(ROOT / 'packaging/windows/RELEASE_README.md', stage / 'README.md')
        copy(ROOT / 'LICENSE', stage / 'LICENSE.txt')
        copy(ROOT / 'public/fonts/OFL.txt', stage / 'licenses/Manrope-OFL.txt')
        shutil.copytree(args.dependency_notices, stage / 'licenses/desktop')
        (stage / 'portable-mode.json').write_text('{"schema_version":1}\n')
        source_notice = stage / 'licenses/ffmpeg/SOURCE.md'
        source_notice.write_text(source_notice.read_text().replace(
            '/releases/tag/v' + previous['desktop_version'], '/releases/tag/v' + version))
        provenance = {
            'schema_version': 1, 'purpose': 'production portable distribution',
            'desktop_version': version, 'optimization': 'Cargo release',
            'app_sha256': digest(args.exe), 'windows_runtime_tested': False,
            'windows_usb_hardware_tested': False, 'ffmpeg': manifest,
            'unchanged_tools_from': {
                'desktop_version': previous['desktop_version'],
                'published_package_sha256': args.previous_sha256,
            },
        }
        result = finish(stage, args.output,
                        f'Muse-Video-Bridge-{version}-Windows-x64-portable', provenance)
    source_target = args.output / 'FFmpeg-source.tar.gz'
    if source_target.exists():
        if digest(source_target) != SOURCE_SHA256:
            parser.error('Existing corresponding source checksum mismatch')
    else:
        copy(args.ffmpeg_source, source_target)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
