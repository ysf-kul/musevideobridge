# Reproduce the release

Version the desktop consistently in package.json/package-lock.json,
src-tauri/Cargo.toml and tauri.conf.json. Library/firmware versions are independent.
Inspect existing remote history, tags and releases before choosing a new semantic
version. Never force-push or reuse an existing tag. Release packages are ignored;
attach compiled artifacts to GitHub Releases rather than adding them to source.

Read [BUILD_AND_RUN.md](BUILD_AND_RUN.md) for source prerequisites and full checks.
Run the native simulator tests, including ignored cases. Enable `MVB_RUN_TSAN=1`
on compatible hosts and provide PowerShell on PATH for Windows-helper tests.
Keep measured hardware results distinct from new software checks.

## Dependency notices and source

Before packaging, supply the complete locked Rust dependency sources, original
notices, project source and rebuild instructions. This covers MPL serialport and
option-ext; replacing/rebuilding libraries and debugging changes is allowed.

```sh
npm ci
cargo vendor --locked /tmp/mvb-vendor
python3 packaging/collect_dependency_notices.py --vendor /tmp/mvb-vendor
mkdir -p release-artifacts
tar -czf release-artifacts/Muse-Video-Bridge-0.3.0-dependency-sources.tar.gz -C /tmp mvb-vendor
```

The collector includes extra locked build/other-target dependency notices for
completeness. It does not bundle dependency source code into desktop packages.
Frontend runtime package licenses and the Manrope OFL notice are also included.
Third-party components retain their own licenses; Muse's MIT license covers its
own code only, including the exception for Espressif-restricted vendor components.

## Linux x86-64

```sh
npm run tauri -- build --bundles deb,rpm
python3 packaging/build_linux_release.py --dependency-notices release-artifacts/dependency-notices
```

The Tauri configuration installs license/setup notices in the Debian/RPM packages
and declares FFmpeg/GUI/serial runtime dependencies. The portable archive embeds
frontend/fonts in the release executable and stores user data in `Data/`.
Check package contents, `ldd`, ELF symbol versions, and actual launch/close from an
extracted archive outside the checkout. Clean target-distribution installation
must be tested separately before claiming support.

AppImage bundling was investigated and built, but it is not distributed in v0.3.0:
the generated image contains many system GUI/media libraries whose corresponding
source/redistribution review is outside the qualified native-package boundary.
Do not publish it as an audited artifact merely because the bundler succeeded.

## Windows x64

Native Windows builds use `npm run tauri -- build --no-bundle` with the MSVC
toolchain/SDK and WebView2. The v0.3.0 build used the already-installed cargo-xwin
cross-build tooling and the same custom-protocol embedded frontend:

```sh
npm run build
CARGO_PROFILE_RELEASE_DEBUG=0 cargo xwin build --locked --release --target x86_64-pc-windows-msvc -p muse-video-bridge --features custom-protocol
```

For FFmpeg, download the exact source archive and LLVM-MinGW archive specified by
`packaging/build_ffmpeg_windows.py`; compare both SHA-256 values before extraction.
Build without external codec libraries/GPL/nonfree/network flags:

```sh
python3 packaging/build_ffmpeg_windows.py --source /path/ffmpeg-source.tar.gz --toolchain /path/llvm-mingw --output /tmp/mvb-ffmpeg-build
python3 packaging/build_windows_release.py --help
```

Use the packager's required arguments to select the freshly optimized MSVC app,
build/install/source directories, exact source archive, toolchain and dependency
notices. It attaches FFmpeg source as a separate release asset and includes
replaceable executable/DLL tools with their licenses and build provenance.
No diagnostic CLI, private flashing kit, benchmark video, SDK, source files or
user data belongs inside the production portable ZIP.

The manually dispatched `Windows release smoke` GitHub workflow downloads the
published archive, verifies external/internal checksums, runs bundled MP4 probe,
conversion/decode verification and checks native window creation. This is a Windows
runner smoke check, not physical USB or interactive end-user acceptance.

## Publication

Audit the staged source with secret scanning and review generated-file exclusions.
Make meaningful commits, push to the inspected repository without rewriting history,
and create a new tag pointing to the tested source commit. Generate SHA256SUMS.txt
for all downloads/corresponding-source assets and create a versioned GitHub Release
with installation/hardware/USB instructions and honest platform limitations.
Verify release URLs and asset hashes after downloading them again. Only then use
those real URLs in the Muse website catalog, test/build the website, and push its
changes preserving remote and local history.
