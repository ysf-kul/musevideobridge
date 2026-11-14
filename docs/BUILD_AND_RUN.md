# Build, run and verification

> For public v0.4.0 pre-release downloads, use [INSTALL.md](INSTALL.md). Production packaging
> and dependency notices are in [RELEASING.md](RELEASING.md); current release checks
> are in [RELEASE_VERIFICATION.md](RELEASE_VERIFICATION.md). The phase-era
> platform/private-kit descriptions below are retained as historical context.

Windows and Linux are the current targets. The available test environment is CachyOS
Linux in a VM. Windows x64 release cross-build is verified; Windows runtime and installers
remain unverified. Portable app/flashing test ZIPs are supplied. macOS is outside
scope. Commands assume the repository root and a normal user account.

## Desktop prerequisites

- Node.js 22.12 or newer; `npm ci` uses the committed lockfile.
- Rust 1.90 or newer and Cargo; tested toolchain is 1.99.1.
- FFmpeg and FFprobe on PATH, or explicit trusted executable paths in the application's
  tool settings. Required: MP4 video decoder, MJPEG encoder and scale `reset_sar` feature.
  FFmpeg 9.0.2 was tested; tests also require libx264. The app does not auto-download tools.
  Development/Linux installs use external FFmpeg; the private Windows portable package
  includes an unmodified shared LGPL build with FFprobe and required DLLs.
- Linux: C/C++ build tools, pkg-config, libudev development files, GTK3, WebKitGTK 4.1,
  and the distribution's usual Tauri development dependencies. A working graphical
  session is needed. Install the appropriate distribution packages; do not assume one
  command applies to every Linux distribution. Runtime WebKitGTK/GTK remain required.
- Windows: Microsoft C++ build tools/Windows SDK, Rust MSVC toolchain and WebView2.
  For source builds FFmpeg/FFprobe must be in PATH or selected through tool settings. COM/card adapters
  are implemented but have not been tested on Windows.

```sh
npm ci
npm run tauri -- dev
```

This starts Vite and Tauri together. `npm run dev` alone is a frontend server, not the
product desktop. Direct `cargo build -p muse-video-bridge` without custom-protocol assets
is a development build that expects Vite. Build a self-contained asset executable with:

```sh
npm run tauri -- build --debug --no-bundle
```

Linux binary: `target/debug/muse-video-bridge`. Intended release packaging:

```sh
npm run tauri -- build
```

Linux configuration requests deb/rpm bundles. On Windows use:

```powershell
npm run tauri -- build --config src-tauri/tauri.windows.conf.json
```

The Windows overlay requests NSIS. These installer/release commands are instructions,
not claims of tested installers, signatures or a distribution-ready license. FFmpeg
remains external for source/Linux installs; the private Windows test ZIP bundles it.
Shared Muse PNG/ICO assets and local Manrope/OFL
notice are included; no network font dependency exists.

## Windows portable app and flasher

Read [WINDOWS_TESTING.md](WINDOWS_TESTING.md). Extract the entire portable ZIP to a
writable folder and run `Muse Video Bridge.exe`. Windows 10/11 x64 and installed
WebView2 Runtime are required. No Python/Node/Rust/installer/admin session is needed.
The checked adjacent `portable-mode.json` selects `Data/config`, `Data/cache` and
Windows `Data/cache/webview`; move the whole folder to preserve jobs/settings.
Default FFmpeg/FFprobe settings find `tools/*.exe` beside the app. Explicit selected
paths take precedence. Keep the shared DLLs with the tools.

The separate flashing kit includes official esptool 5.6.0 and firmware 0.2.0.
Run `Flash-CYD.cmd` (Turkish) or `Flash-CYD.cmd -Language en`. It lists ports,
requires selecting the actual COM port, checks hashes and classic ESP32/4 MB flash,
backs up the full flash, writes the exact four build images at 115200 DIO/40 MHz,
then compares flash and resets. A backup error stops before writing. The separately
confirmed `-SkipBackup` option is explicit and not the default. The kit never formats/
writes SD or erases the full chip. Windows execution is still awaiting owner testing.

Some VM graphics configurations emit EGL warnings. The default Wayland runtime rendered
here. An X11/software-rendering smoke run also used the optional environment variables
`GDK_BACKEND=x11`, `WEBKIT_DISABLE_DMABUF_RENDERER=1`, `LIBGL_ALWAYS_SOFTWARE=1`. Use them
only when relevant to a local rendering issue; they are not universal Linux requirements.

## MP4, card and serial workflow

Choose an MP4 in the native dialog. Review source metadata, qscale 2…31 and encoding FPS
1…30. Preserve aspect by default or explicitly select crop. Convert and wait for full
integrity/decode verification. The estimate is a maximum bound, not a promised file size.
If a frame exceeds 48 KiB, choose a lower quality yourself and retry; options are not
silently altered. Conversion errors/cancellation retain partial/failure records in cache.

Choose an existing **mounted FAT32 card directory**. Linux qualification uses mountinfo,
statvfs and `lsblk` FAT32 FSVER; ambiguous, FAT16 or unsupported filesystems cannot be
written by the product. Windows uses volume/free-space APIs. Review space then write and
verify. No automatic formatting, unrelated-file deletion or automatic eject occurs.
Safely eject through the OS before removing the card. The app creates `muse-video/` and
selects a verified hashed video. The installed 4 GB card is not a hardcoded capacity limit.
Maximum video size is 2 GiB−1. Physical FAT32-card workflow is still unverified here.

After firmware installation/board qualification, insert the card,
power the CYD, choose its listed port and connect. A valid video auto-starts after loading.
Play/pause/resume/stop/restart, seek and transform controls are real serial commands.
To follow a device video/edit transformations, select and convert the matching original
with matching options. Unknown hashes show a mismatch rather than a false device preview.
USB upload is the normal transfer workflow and pauses playback while writing.
Explicit cancellation cleans only that confirmed upload's incomplete files.
Connection failures retain recovery; retry the same digest to resume a validated
checkpoint. The microSD stays inside the ESP32 and is not formatted by the app.

Serial access should not require running the application as root/administrator. Linux
access depends on the tty's actual group or session ACL. Inspect `ls -l /dev/ttyUSB*` and
`id`; ask the local administrator to grant appropriate normal-user serial access through
the distribution's supported mechanism. Some use `dialout`, this host uses `uucp`.
Temporary tty ACL access was used for authorized ROM identification/backup attempts
and restored afterward. No persistent group/udev change was made. Windows may need the proper trusted CH340
driver. Close another serial monitor using the selected port. Do not send discovery bytes
to unrelated ports. Only 115200 baud is enabled; DTR is disabled on open but real reset
behavior for firmware controls still needs testing. Actual chip/flash queries worked,
but VM USB flash-backup reads were unreliable; no firmware was written.

## CLI

The CLI uses the same processing/storage/protocol engine:

```sh
cargo run --locked -p muse-video-cli -- probe /path/input.mp4
cargo run --locked -p muse-video-cli -- convert /path/input.mp4 /path/new-job --fps 10 --quality 8
cargo run --locked -p muse-video-cli -- verify /path/new-job/video.mvb
cargo run --locked -p muse-video-cli -- frame /path/new-job/video.mvb 0 /path/new-frame.jpg
cargo run --locked -p muse-video-cli -- destination /path/mounted-card
cargo run --locked -p muse-video-cli -- export /path/new-job/video.mvb /path/mounted-card
cargo run --locked -p muse-video-cli -- ports
```

Output job/extracted-frame paths must be new. CLI rates can be rational (`15/1`),
`--crop` explicitly fills the screen, `--lang en` selects English, and Ctrl-C cancels.
Physical `device --port ...` and `--upload ...` commands control/write the board and
must only be run under appropriate owner authorization.

## Firmware build and installation instructions

Read [HARDWARE.md](HARDWARE.md) first. This build targets the original one-USB ILI9341
reference; the owner's actual PCB/controller/pins are not yet electrically verified.
Python 3.10+ and pinned PlatformIO Core 6.2.0 / Espressif32 8.0.0 / Arduino 3.3.12:

```sh
python3 -m venv .dev-tools/platformio
.dev-tools/platformio/bin/python -m pip install -r research/platformio-requirements.txt
.dev-tools/platformio/bin/python -m platformio run -d firmware -e cyd_optimized_input_aligned
```

Windows equivalents use `py -3 -m venv` and `.dev-tools\platformio\Scripts\python.exe`.
The build **does not flash** the device. The owner has now authorized flashing.
After matching the board profile and establishing reliable USB access, the installation command is:

```sh
.dev-tools/platformio/bin/python -m platformio run -d firmware -e cyd_optimized_input_aligned --target upload --upload-port /dev/ttyUSB0
```

Replace the port with the selected physical port (`COM...` on Windows). PlatformIO's
upload procedure can also rewrite bootloader/partition images. The physical tests in
this investigation instead backed up the complete flash and used application-only
esptool writes at `0x10000`, with verified matching partition layout. For public releases use the concrete
backup-first source instructions in [INSTALL.md](INSTALL.md#firmware-and-hardware).
The historical private Windows helper is not included in public desktop packages. No SD
format is part of setup. Close the app/serial monitor before installation.
Afterward visually qualify colored
bars/corners/orientation, the waiting screen, SD loading and commands before trusting
playback; a build or draw callback cannot prove visible TFT output.

## Automated checks

```sh
npm run check
npm run test
npm run format:check
npm run build
cargo test --locked --workspace
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets -- -D warnings
python3 tests/windows_package_test.py -v
```

Native engine tests need clang++, OpenSSL development files, FFmpeg/FFprobe and the
PlatformIO-installed pinned JPEGDEC/ArduinoJson libraries:

```sh
python3 research/build_native.py --sanitize
MVB_SIMULATOR="$PWD/.verification/native/mvb-simulator" cargo test --locked -p muse-video-core --test device_engine -- --ignored --test-threads=1
MVB_SIMULATOR="$PWD/.verification/native/mvb-simulator" cargo test --locked -p muse-video-desktop --test workflow -- --ignored --test-threads=1
```

These explicitly run the native-dependent tests ordinarily ignored by Cargo.
Host simulators execute the shared C++ playback/upload engine and real JPEGDEC; they do
not emulate ESP32 CPU/SPI/TFT, real card durability or hardware sensor measurements.

Windows-package tests use Python stdlib; PowerShell parser/adapter/workflow tests
require `pwsh` on PATH or `MVB_TEST_PWSH=/absolute/path/to/pwsh`. On Linux they run
under PowerShell 7.6.1 with physical adapters replaced only in isolated test copies.
These host tests do not prove PowerShell 5.1/COM/esptool execution on Windows. Package
rebuilding commands and pinned input digests are in [WINDOWS_TESTING.md](WINDOWS_TESTING.md).

For the Chromium UI integration test, use a **new isolated test root** on every run:

```sh
mkdir -p .verification/ui-run-001
ffmpeg -hide_banner -loglevel error -f lavfi -i 'testsrc2=size=320x240:rate=12' -t 20 -c:v libx264 -threads 1 .verification/ui-run-001/input.mp4
MVB_TEST_ROOT="$PWD/.verification/ui-run-001" MVB_SIMULATOR="$PWD/.verification/native/mvb-simulator" cargo run --locked -p muse-video-desktop --example desktop_harness
```

In separate terminals start `npm run dev`, install test Chromium with
`npx playwright install chromium`, then run `npx playwright test`. An existing compatible
Chromium may instead be selected with `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/absolute/path`.
The harness is localhost-only and separately invoked, uses the real Rust backend/native
engine, and substitutes only native pickers/host filesystem qualification for isolated
files. It is not in the product. Close it when finished. Screenshots/results are under
`.verification/phase3/`. It verifies browser integration, not native-dialog or hardware
behavior. Windows requires separately qualifying this host harness/setup.

## Troubleshooting and retained data

Missing tools: set valid FFmpeg/FFprobe paths and retry selection. Unsupported tool:
install a build with required features. Frame too large: choose an explicit lower quality.
Card unavailable/unsupported: mount and inspect the actual FAT32 volume; do not format
from the app. Full card: choose sufficient space; unrelated media is preserved. Damaged
output: reconvert/re-export and inspect diagnostics. Preview mismatch: use identical source,
options and tool version to produce the matching content hash.

A stale connection disables live controls/measurements and labels the last frame; reconnect
queries current state. A device restart invalidates its old session. Temperature is
unavailable on the classic ESP32, not zero. CPU utilization can be unavailable before a
valid runtime sample. Blank/white physical TFT requires controller/wiring/orientation
qualification; do not cycle board profiles blindly. Interrupted export or a
transport-failed upload retains recognizable partials. Explicit upload cancellation
removes only the confirmed upload's incomplete files; it does not delete unrelated
media or recovery to recover space.

Per-user settings/cache are resolved by Tauri (`dev.muse.videobridge`) unless the checked
portable marker selects adjacent `Data/`. Windows WebView2 data uses that selected cache.
Unrecognized
settings are retained/backed up before replacement; caches can grow because automatic
expiry/deletion is intentionally absent. Inspect app-owned files before any manual cleanup.
See [PHASE_3_REPORT.md](PHASE_3_REPORT.md) for untested cases and dependency audit results.
