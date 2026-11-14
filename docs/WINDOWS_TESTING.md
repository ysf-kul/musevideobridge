# Windows USB transfer test packages

The owner requested one portable-app/flasher package for direct Windows testing.
Use the Performance and USB combined ZIP under `dist-packages/`. Earlier ZIPs are
retained as historical artifacts; use the new package for the current implementation.

| Artifact                                                                                                                                      | Use / current verification                                                                                                                                                                                                                  |
| --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Muse-Video-Bridge-Windows-Performance-USB-Test-2026-11-14.zip (local-only evidence: `../dist-packages/Muse-Video-Bridge-Windows-Performance-USB-Test-2026-11-14.zip`) | Windows x64 portable app, FFmpeg/FFprobe/DLLs, static-CRT diagnostic CLI, selected CYD firmware, application-only flasher and checked benchmark media; Windows cross-builds verified, Windows runtime unverified |
| Muse-Video-Bridge-Windows-x64-Portable-2026-11-14.zip (local-only evidence: `../dist-packages/Muse-Video-Bridge-Windows-x64-Portable-2026-11-14.zip`) | Standalone portable application with bundled conversion tools; use when the selected firmware is already installed |
| Muse-Video-Bridge-CYD-Windows-Flasher-2026-11-14.zip (local-only evidence: `../dist-packages/Muse-Video-Bridge-CYD-Windows-Flasher-2026-11-14.zip`) | Standalone backup-first flasher with the same selected application image |

The packages contain English usage documentation, provenance/license/source references and
SHA-256 manifests. Packages are unsigned private test builds. WebView2 Runtime remains
required on Windows 10/11 x64. No installer/build tools/admin session is required to
run the portable app; driver installation is an OS-level step if CH340 is missing.

The selected `cyd_optimized_input_aligned` firmware image is 665,888 bytes with
SHA-256 `3de1a18fdd0eb993c6fb53a311317ebe62e2b82b07ec634f030d21496483343f`.
That application image was flashed and verified on the Linux-connected CYD.
The portable executable and CLI hashes, imports and host-test results are recorded
in [Windows build evidence](evidence/windows-build-2026-11-14.json). Final ZIP
hashes are external in `dist-packages/Muse-Windows-2026-11-14-SHA256SUMS.txt`.

## Run directly on Windows

1. Release the CYD from VM passthrough and connect it directly to Windows.
2. Extract the combined ZIP completely to a writable folder and read
   `README-FIRST.md`. Close the app/serial monitor and run the root launcher
   `Flash-Performance-English.cmd`. The combined kit's
   `Flash-Firmware-English.cmd` performs the same application-only update.
3. Select its actual listed COM port, confirm the one-USB resistive ILI9341 reference
   target and type `FLASH`. The helper checks hashes/chip/4 MB, reads a complete
   **4 MB flash backup**, compares the existing partition table byte-for-byte,
   then writes/verifies only `firmware.bin` at app0 / 0x10000. A failed backup or
   layout mismatch stops before writing. Keep the complete backup privately.
   Application-only mode rejects `-SkipBackup`. No automatic full-flash fallback
   occurs. Bootloader, partition table, boot selection, NVS, app1 and filesystem
   partitions are preserved; the microSD card is not formatted.
4. Confirm the running firmware and physical English waiting screen. Boot selection
   remains unchanged, so a matching layout alone does not establish that app0 is
   selected. The helper README provides complete-backup restoration instructions.
5. Run the root launcher `Start-App.cmd`. The app starts in Turkish; select
   English if desired. Its executable is in `Portable-App/`.
6. Keep the FAT32 microSD card **inside the CYD**, select an MP4, and convert with
   the new first-launch settings: qscale **8**, encoding **25 FPS**, preserve aspect,
   optimized Huffman coding and video-only output. Existing saved settings remain
   unchanged. Explicit 30 FPS and higher-quality choices remain available.
7. Connect the actual listed COM port and choose **Send over USB**. Wait for
   acknowledged progress and final device verification. Record the exact error
   code/details if it fails; do not replace this test with copying through a PC card reader.
8. Confirm transferred size/hash and visible playback, then test cancellation and
   same-file resume after an interrupted transfer. Test playback/seek, X/Y/size/scale,
   four rotations/FPS and real/null telemetry against the physical screen.

The app starts Turkish; English and theme persist. `portable-mode.json` stores settings
in `Data/config`, jobs in `Data/cache`, WebView2 profile in `Data/cache/webview`. Moving
the folder preserves those files. Explicit tool paths still override bundled defaults.
USB upload is the intended normal workflow and needs the installed compatible
firmware and working SD card. It pauses playback while transferring and shows
confirmed bytes before final integrity verification. Direct mounted-card export
remains a secondary alternative. Neither the desktop nor flasher formats the card.

The optional `tools/muse-video-cli.exe` uses the same transfer engine for repeatable
diagnostics and benchmarks. Its current x64 static-CRT cross-build imports Windows
system DLLs without a separate VCRUNTIME140/UCRT runtime dependency. The desktop
retains its established Tauri static-vcruntime/system-UCRT configuration and WebView2
requirement. Neither cross-build proves Windows execution. Included prepared videos
are host-verified benchmark candidates; their presence does not establish real
device FPS or perceived screen quality. See
[the USB/playback investigation](USB_TRANSFER_AND_PLAYBACK_REPORT.md).

## Evidence boundaries

The target is the owner-confirmed one-USB ESP32-2432S028R. ROM identification
reported ESP32-D0WD-V3 revision 3.1, a 40 MHz crystal and 4 MB flash. Initial VM
transport and permission failures were recorded before device access became
available. That earlier investigation's 64 KiB read was never a complete backup.

Current Linux/device measurements and exact test outcomes are recorded separately
in [the real hardware report](REAL_HARDWARE_PERFORMANCE_REPORT.md) and its
linked evidence. They do not establish Windows app, Windows UART driver or Windows
flasher behavior. No Windows runtime test has been performed in this Linux VM.
Visible image quality, screen stability and perceived smoothness require a physical
screen observation; firmware frame counters alone cannot establish them. Private
flash backups and unique device identifiers are excluded from distribution.

## Rebuild the Windows packages

On Windows with the build prerequisites, `npm run tauri -- build --no-bundle --config
src-tauri/tauri.windows.conf.json` produces a release asset-embedded executable.
That native-Windows command is documented but has not been run here. This environment
instead cross-built with cargo-xwin 0.23.1 (MIT, build-only), installed MSVC x64 Rust
target, Windows SDK, clang-cl and lld-link:

```sh
npm ci
npm run build
rustup target add x86_64-pc-windows-msvc
TAURI_CONFIG='{"bundle":{"targets":["nsis"]}}' cargo xwin build --locked -p muse-video-bridge --release --target x86_64-pc-windows-msvc --features custom-protocol -j2
```

Do not force `RUSTFLAGS=-C target-feature=+crt-static` for this configuration. That
attempt failed with unresolved UCRT symbols; the normal target configuration linked
successfully using the system Windows UCRT and Tauri static-vcruntime integration.
The SDK emitted missing external PDB-debug-info LNK4099 warnings; they did not stop
the build. PE inspection confirmed AMD64/Windows GUI and Windows system/UCRT imports,
without a separate vcruntime DLL. This inspection does not prove Windows startup.

`packaging/build_windows.py` uses explicit local inputs, checks pinned upstream
release SHA-256, copies only needed binaries and produces full checksum coverage.
Download the upstream archives/source/license from the URLs in that source and
`packaging/windows/THIRD_PARTY.md`; the `latest` FFmpeg asset is mutable, so pin the
recorded digest and obtain the matching snapshot rather than ignoring mismatches.

```sh
python3 packaging/build_windows.py \
  --exe target/x86_64-pc-windows-msvc/release/muse-video-bridge.exe \
  --ffmpeg /path/ffmpeg-windows.zip --esptool /path/esptool-windows.zip \
  --ffmpeg-source /path/ffmpeg-source.tar.gz --esptool-source /path/esptool-source.tar.gz \
  --gpl3 /path/COPYING.GPLv3 \
  --boot-app0 /path/framework-arduinoespressif32/tools/partitions/boot_app0.bin \
  --firmware firmware/.pio/build/cyd_optimized_input_aligned \
  --firmware-environment cyd_optimized_input_aligned --firmware-version 0.2.1 \
  --application-uart-baud 115200 \
  --output /path/new-package-output \
  --cli target/x86_64-pc-windows-msvc/release/muse-video-cli.exe \
  --revision windows-performance-usb-2026-11-14
MVB_TEST_PWSH=/path/to/pwsh python3 tests/windows_package_test.py -v
```

The boot-app0 path must come from the installed PlatformIO framework. Offsets are
0x1000/0x8000/0xe000/0x10000, DIO/40 MHz/4 MB, 115200 baud from the actual environment.
Project source and esptool/FFmpeg upstream source archives are supplied; public release
still requires a complete dependency attribution/corresponding-source/relink review.

Build the optional CLI separately with a static CRT; keep the desktop build's
normal configuration above. The combined-kit assembler requires `llvm-readobj`
on PATH for PE/import inspection and new staging/output paths:

```sh
RUSTFLAGS='-C target-feature=+crt-static' cargo xwin build --locked -p muse-video-cli --release --target x86_64-pc-windows-msvc -j2
python3 packaging/build_windows_test_kit.py \
  --portable /path/current-portable.zip \
  --flasher /path/current-flasher.zip \
  --cli target/x86_64-pc-windows-msvc/release/muse-video-cli.exe \
  --media /path/encoding-evidence \
  --extra-media /path/formats-30fps \
  --extra-media /path/windows-qualified-media \
  --guide packaging/windows/USB_TEST_README.md \
  --stage /path/new-stage \
  --output /path/new-package-output \
  --encoding-fps 25 --encoding-qscale 8 --encoding-scaling bicubic \
  --playback-decoder new_jpeg --playback-render-mode dma \
  --playback-tft-spi-hz 40000000 --playback-command-spi-hz 20000000 \
  --playback-big-endian --playback-prefetch --playback-target-fps 25 \
  --revision windows-performance-usb-2026-11-14 \
  --name Muse-Video-Bridge-Windows-Performance-USB-Test-2026-11-14
```

The media input contains `encoding-source/` and `encoding-detailed/` from the
benchmark tool. The kit includes four host-verified candidates: q8/10 FPS/bicubic,
q8/15 FPS/area, q4/15 FPS/Lanczos and q10/20 FPS/area. It refreshes source/docs,
checksums and provenance. The optional additional comparison includes seven
video-only JPEG MVB candidates encoded at 30 FPS, with qscale 4/6/8/10 and prepared
sizes down to 240×180. The additional qualified-media directory supplies the
full-screen q8/25-FPS Balanced output and native 304×224/30-FPS Maximum Smoothness
candidate. Raw research streams are not playable production uploads
and are excluded. Checksums do not qualify Windows runtime or visible TFT quality.

## Acceptance evidence still needed

The current Windows x64 desktop was rebuilt with embedded production assets;
the diagnostic CLI was rebuilt with a static CRT. The frontend production build,
16 package/PowerShell host tests, four diagnostic-helper host tests and one browser
workflow passed. The browser workflow uses a simulator and is separate from real
device evidence. The Windows SDK emitted missing-debug-PDB linker warnings;
the Windows builds passed. Source and binary hashes are recorded in each package.

Package checks cover firmware image hashes and offsets, nested checksum lists,
application-only launchers, CLI imports, checked media and portable settings.
Diagnostic helper tests exercise argument escaping, simultaneous stdout/stderr
draining, retained failure logs and exit status. These are Linux host tests of
PowerShell and Windows cross-builds, not Windows runtime verification.

Record Windows version, app/firmware version, actual waiting-screen text/color/
orientation, SD filesystem/capacity, confirmed controls, measured FPS/drops/read/decode/
draw/heap, reconnect and error behavior. Try 8/10/12/15 FPS and native/scaled/four-turn
sizes. Temperature must remain unavailable. Check both languages/themes, keyboard
focus and narrow windows. Share diagnostic output with unique IDs/private backups omitted.
Until direct Windows tests run, Windows app execution, Windows flashing and Windows
USB end-to-end behavior remain unverified. Use `Run-Device-Tests.cmd` to record
owner observations separately from supplemental CLI metrics; see
[Windows device tests](WINDOWS_DEVICE_TESTS.md).
