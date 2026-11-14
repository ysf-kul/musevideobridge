# Muse Video Bridge

Convert local MP4 videos for an **ESP32-2432S028R / Cheap Yellow Display (CYD)**,
transfer them over USB to the microSD card inside the device, and control playback
from a Linux or Windows desktop. Processing and settings stay local: no account,
cloud service, runtime AI API, or audio playback.

**Different Apps. One Muse.** Muse is an independent personal collection of
AI-assisted software. Muse Video Bridge's own source is [MIT licensed](LICENSE);
third-party components retain their [own licenses](THIRD_PARTY.md).

## Downloads and setup

Get the versioned desktop downloads and **SHA256SUMS.txt** from
[GitHub Releases](https://github.com/ysf-kul/musevideobridge/releases).
See [installation and USB setup](docs/INSTALL.md),
[source build instructions](docs/BUILD_AND_RUN.md), and
[release verification](docs/RELEASE_VERIFICATION.md).

- **Linux x86-64:** AppImage, portable `.tar.gz`, `.deb`, and `.rpm`. The optimized native
  application is runtime-tested on CachyOS; Debian/RPM packaging is build-verified,
  not installation-tested on those distributions. GTK3, WebKitGTK 4.1, libudev,
  and FFmpeg/FFprobe 8+ with scale `reset_sar` support are required.
- **Windows x64:** portable ZIP with conversion tools. Requires Windows 10/11
  and Microsoft's WebView2 Runtime. Optimized MSVC cross-build and package contents
  are verified; Published-package window creation and bundled conversion/decode passed on a
  Windows Server 2025 runner. Windows 10/11 interactive acceptance and physical USB
  remain unverified.
- Other Linux distributions/architectures and macOS are not qualified.

## Workflow

1. Select an MP4 and inspect its metadata and preview.
2. Convert to indexed JPEG `.mvb` video. First-launch defaults are qscale 8 and
   25 encoding FPS; existing saved settings are preserved.
3. Install compatible firmware using the [backup-first hardware instructions](docs/INSTALL.md#firmware-and-hardware),
   insert a FAT32 microSD card, and keep it inside the CYD.
4. Select the device's USB serial port and connect. Choose **Send over USB**.
   Progress counts acknowledged bytes; the ESP32 checks file size and SHA-256
   before making the transferred video available for playback.
5. Play, pause, resume, stop, restart, seek, or adjust position, size, scale,
   quarter-turn rotation, and target FPS. Telemetry separates target and actual FPS.

Explicit cancellation cleans only the confirmed upload's incomplete files.
Interrupted connections retain a digest-specific checkpoint for retry. The app
never formats the microSD. Mounted FAT32 export is also available.

Turkish is the first-launch language; English and light/dark/system themes are
saved locally. The desktop preview reconstructs prepared JPEGs using reported
playback state; it is not a camera or screen capture of the TFT.

## Measured hardware performance

Previous real-device measurements of the selected playback implementation achieved
**320×240 full-screen video at 29.7992 actual FPS over five active minutes**.
Separate clean runs with other workloads/native sizes reached approximately 29.97 FPS. Twenty-five complete-region TFT-memory
checks found **zero pixel mismatches**, with **zero DMA ownership errors**.
These are prior measurements, not hardware tests repeated for this release.

The previous native Linux desktop also converted, cancelled, resent, verified,
and played a 2,444,120-byte video over 115200-baud USB with the microSD installed.
See the [hardware report](docs/REAL_HARDWARE_PERFORMANCE_REPORT.md) and
[native USB acceptance](docs/NATIVE_DESKTOP_USB_E2E.md) for methods and limits.
No hardware is connected during the final software release audit.

## Build and test

Node.js 22.12+, Rust 1.90+, FFmpeg/FFprobe, and platform prerequisites are documented
in [BUILD_AND_RUN.md](docs/BUILD_AND_RUN.md). The GUI uses Tauri 2, Svelte 5 and Rust;
firmware uses pinned PlatformIO/Arduino-ESP32 dependencies.

```sh
npm ci
npm run check
npm test
npm run format:check
npm run build
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo test --locked --workspace
python3 research/build_native.py
MVB_SIMULATOR="$PWD/.verification/native/mvb-simulator" cargo test --locked --workspace -- --include-ignored --test-threads=1
python3 -m unittest discover -s tests -p '*_test.py'
npm run tauri -- build --bundles deb,rpm
```

`npm run tauri -- dev` starts the native development app. `npm run dev` alone
runs a browser frontend without native file/serial access. Test harnesses are
excluded from production assets. Production packaging is documented in
[RELEASING.md](docs/RELEASING.md).

## Documentation and limits

[Architecture](docs/ARCHITECTURE.md) · [video format](docs/VIDEO_FORMAT.md) ·
[USB protocol](docs/SERIAL_PROTOCOL.md) · [hardware](docs/HARDWARE.md) ·
[video pipeline](docs/VIDEO_PIPELINE.md) · [performance](docs/PERFORMANCE_AND_TELEMETRY.md)

The `.mvb` format is experimental. Audio, arbitrary rotation angles, and calibrated
classic-ESP32 CPU temperature are unavailable. USB at 115200 baud is reliable but
slow for large videos. Windows runtime, clean Debian/RPM installation, broader
Linux compatibility, and signed installers remain unverified. Firmware is supplied
as complete project source with pinned dependency/build instructions; this release
contains no firmware binary or private test/flashing kit. Earlier reports are
historical, with local-only evidence paths clearly labeled.
