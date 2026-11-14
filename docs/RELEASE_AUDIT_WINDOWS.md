# Windows release audit — 0.3.0

The Windows x64 desktop executable was rebuilt from the current source and fresh
production frontend assets in Cargo's optimized release configuration, using
`cargo-xwin 0.23.1`, the MSVC Rust target, Windows SDK, clang-cl and lld-link on
Linux. No debug executable, PDB, import library, source archive, benchmark media,
device-test runner, private device information or development dependency is
included in the portable application.

## Produced artifacts

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `Muse-Video-Bridge-0.3.0-Windows-x64-portable.zip` | 12,775,167 | `ea8c30a75ad1ec01aa9108307f633d265079b9157470f7e9cd60095aff041460` |
| `FFmpeg-source.tar.gz` | 17,330,649 | `4270bde68c027b376899f26f7973e213aeaa5ddb6fef8a352e0d6ee457766c67` |

The portable ZIP includes the asset-embedded Muse executable, launcher,
portable-mode marker, setup instructions, MIT license, Manrope/desktop/LLVM-MinGW
notices, a complete file checksum manifest, and focused FFmpeg/FFprobe tools.
Windows 10/11 x64 with Microsoft Edge WebView2 Runtime is the intended target.
WebView2 remains a separately installed Microsoft dependency. Configuration,
conversion cache and WebView2 data use the adjacent `Data/` directory, so the
extracted folder must be writable. Explicit FFmpeg paths remain available.

## FFmpeg distribution

Unmodified FFmpeg 9.0.2 source at commit
`c179411bbb2864cc71323d3055acda25d5c2aace` was cross-built using the checksum-pinned
LLVM-MinGW 20261104 UCRT toolchain. FFmpeg declares the selected configuration
**LGPL version 2.1 or later**. The build disables external-library autodetection,
network protocols, GPL/nonfree flags, FFplay, debug output and assembly, and enables
only required encoding/output/filter capabilities with available built-in decoders.

The replaceable shared tools support MOV/MP4 and MJPEG inputs, including H.264,
HEVC and MPEG-4. AV1 and other codecs requiring external libraries are unavailable
in this focused build. The desktop's exact FPS, scale/reset-SAR, format, pad/crop,
deinterlace and optimized-Huffman MJPEG pipeline is enabled. A compatible external
FFmpeg/FFprobe pair can be selected for other input codecs.

The same release supplies exact FFmpeg source separately. Its source contains
unused GPL code as well as LGPL code; the bundled configuration is LGPL.
`licenses/ffmpeg/` preserves upstream notices and configuration, and the toolchain's
runtime notices accompany linked startup/runtime components. No externally built
codec DLL or proprietary codec download is required by these tools.

## Checks run on Linux

- Fresh MSVC desktop release builds passed, including a final build after license
  metadata updates. Existing Windows SDK LNK4099 warnings about absent external
  debug PDB files remain visible; they did not prevent linking.
- Minimal Windows FFmpeg release cross-build passed. Upstream compiler warnings
  include unused optional functions, one fallthrough and `pred_x`/`pred_y` in the
  optional H.263 macroblock-info path when that encoder is disabled. Inspection
  confirmed that the product's MJPEG command does not enable that path; no warning
  was hidden and no speculative upstream modification was made.
- PE inspection confirms AMD64 Windows GUI for Muse. Its imports are Windows
  system/UCRT DLLs; it does not require a separately supplied VCRUNTIME DLL.
- All eight FFmpeg executables/shared libraries were inspected. Every non-system
  import resolves to a DLL packaged in `tools/`. Only Windows system/UCRT imports
  remain external.
- Complete internal SHA-256 coverage and ZIP extraction-path validation passed for
  all 1,253 files. Development/source/test artifacts are absent. The packager checks
  embedded FFmpeg configuration against the pinned recipe and rejects source files
  accidentally supplied as dependency notices.
- Seventeen package/PowerShell host tests passed, including a regression for
  upstream license timestamps predating the ZIP epoch. Four device-helper
  PowerShell host tests passed. These tests do not open a physical port.
- Python compilation, actionlint 1.7.12 and PowerShell parsing of all workflow
  scripts passed.

## Newly completed Windows runner smoke

After publication, [GitHub Actions run 43594244747](https://github.com/ysf-kul/musevideobridge/actions/runs/43594244747)
passed on **Microsoft Windows Server 2025 Datacenter, AMD64**. It tested the exact
published portable ZIP with the SHA-256 listed above, using source commit
`c4999f64f626968ecfb227f2807781533355e231`. Sanitized results are preserved in
[Windows runtime evidence](evidence/windows-release-runtime-smoke.json).

The workflow downloaded the public release archive and verified the release and
internal checksums. Bundled FFprobe inspected the generated H.264 MP4 fixture.
Bundled FFmpeg executed the progressive preserve-aspect conversion path: 25 FPS,
320×240 scaling/reset-SAR, YUV420 preparation, aligned padding, qscale 8 and
optimized-Huffman MJPEG encoding. It decoded the prepared stream successfully;
FFprobe confirmed all ten output frames at 320×240. Crop and interlaced conversion
paths were not exercised by this progressive fixture.

The packaged Muse executable created a native Windows window with a nonzero
`MainWindowHandle` and remained running for five seconds afterward with WebView2
available on the runner. Application stdout/stderr were empty. This establishes a
Windows runner startup smoke, rather than a review of rendered window contents or
interactive workflows. Settings persistence, physical USB, serial connection,
transfers, interactive controls and user-observed playback were not tested.
Windows 10/11 remain intended targets requiring their own runtime qualification;
the tested operating system was Windows Server 2025.

No Windows machine or Wine runtime was available during local packaging. The
cross-build and package results above remain separate from this later runner
evidence. The immutable v0.3.0 source tag records the packaging-time snapshot;
this post-publication evidence is documented on the repository's main branch.

Previously measured Linux-connected CYD results remain separate: full 320×240,
approximately 29.80 sustained actual FPS over five active minutes, 25 full-region
checks with zero pixel mismatches, and zero DMA ownership errors. Those values are
not new Windows or release-audit hardware results.

## Rebuild

After `npm ci` and `npm run build`, use the existing Windows cross-build:

```sh
PATH="$PWD/.dev-tools/cargo-xwin/bin:$PATH" \
TAURI_CONFIG='{"bundle":{"targets":["nsis"]}}' \
  cargo xwin build --locked -p muse-video-bridge --release \
    --target x86_64-pc-windows-msvc --features custom-protocol -j2
```

Download the exact source archive and checksum-pinned toolchain described in
`packaging/build_ffmpeg_windows.py`; verify the toolchain archive before extracting
it. That build-only toolchain is not shipped with the application.

```sh
python3 packaging/build_ffmpeg_windows.py \
  --source /path/to/FFmpeg-source.tar.gz \
  --toolchain /path/to/extracted-llvm-mingw \
  --output /path/to/new-ffmpeg-build
python3 packaging/build_windows_release.py \
  --exe target/x86_64-pc-windows-msvc/release/muse-video-bridge.exe \
  --ffmpeg-install /path/to/new-ffmpeg-build/install \
  --ffmpeg-source-directory /path/to/new-ffmpeg-build/FFmpeg-c179411bbb2864cc71323d3055acda25d5c2aace \
  --ffmpeg-source-archive /path/to/FFmpeg-source.tar.gz \
  --toolchain /path/to/extracted-llvm-mingw \
  --dependency-notices /path/to/dependency-notices \
  --output /path/to/new-release-assets
MVB_TEST_PWSH=/path/to/pwsh python3 tests/windows_package_test.py -v
MVB_TEST_PWSH=/path/to/pwsh python3 tests/windows_device_tests_test.py -v
```

Use the repository's dependency-source packaging workflow to regenerate complete
license notices and corresponding-source assets. The existing dated owner-test
packages and flash helpers remain separate from this production portable ZIP.
