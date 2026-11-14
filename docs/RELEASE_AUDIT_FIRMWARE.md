# Firmware, video pipeline and transport release audit

Audit date: 2026-11-14. This report records new software checks separately from
the previously recorded hardware qualification. The audit used CachyOS Linux
x86_64 in the existing VM. No USB serial device was available: there were no
`ttyUSB`/`ttyACM` devices or serial-by-id links, and USB enumeration contained
only VM hubs and a mouse. No device was flashed, no microSD was removed, and
no new physical USB or TFT smoke test was performed.

## Implementation inspected

- Rust core media jobs, FFmpeg/FFprobe process lifecycle, indexed `.mvb`
  container and JPEG parsing, storage/export, serial framing and upload.
- Desktop actor connection ownership, upload cancellation, telemetry polling,
  configuration, portable/per-user data paths, and settings preservation.
- ESP32 storage/UART/SPI/runtime adapters and the portable upload/playback
  engine; microSD journals and publication, seek and playback controls.
- Baseline JPEG validation, JPEGDEC/ROM/TJpgDec/Espressif adapters, bounded
  compressed read-ahead, first-block warmup, pixel leases, FIFO DMA ownership,
  clipped rendering, rational media timing and measured telemetry.
- Production PlatformIO inheritance and pinned firmware libraries, native
  simulator/profile programs, Python host/SDK test suites, dependency provenance,
  and the architecture, build, video, protocol and prior hardware reports.

The selected production environment remains `cyd_optimized_input_aligned`.
No decoder, DMA, playback clock, transfer implementation or production flag was
changed for this audit. A stale test expected the older `cyd_optimized` default;
the assertion now verifies the current environment, its inheritance, compressed
record alignment, all existing qualified flags and exclusion of rejected research
features. The initial failed run is retained rather than counted as passing.

## Data integrity and workflow review

Conversion launches trusted FFmpeg executables with argument arrays, a local
file/pipe protocol allowlist, cancellation/watchdog handling and bounded
diagnostics. Audio/subtitles/data are excluded. The container is checked,
JPEG frames are decoded for verification, and its SHA-256 is computed before
publication. Output jobs are created exclusively; failures retain recognizable
partials and a failure record. Source/preview access remains backend-owned.

The single serial actor opens only the selected port, releases RTS/DTR, performs
the handshake, and requests real status. Wire frames are bounded COBS messages
with CRC32, session/sequence checks and finite retries. Upload progress advances
only after a matching accepted-offset ACK. Recovery validates journal identity
and checkpoint CRC. Final completion requires matching transfer ID, exact size,
accepted/checkpointed/verified bytes and SHA-256; firmware checks the container
and frame profile before selecting published media. Explicit cancellation
discards only an identified, owned incomplete transfer; connection errors retain
checkpoints. Existing files and unrelated legacy recovery are preserved.

Simulator tests exercise the shared desktop/core/firmware engine using real
FFmpeg conversion and host storage. They establish software behavior for
connect/disconnect/reconnect, transfer, retries/lost ACK, cancellation/recovery,
integrity, playback, transform/FPS/seek commands and telemetry. They do not
establish physical USB, microSD, SPI or Windows behavior.

## New verification

| Check | Actual result |
| --- | --- |
| Sanitized native build | Passed with ASan/UBSan: protocol/container bounds and CRC, coalesced frame records, 500 transform golden cases, rational timing/pause/seek, simulator and profile build. Leak detection was disabled; no passing leak check is claimed. |
| Complete Rust workspace including ignored simulator cases | **59 passed, 0 failed, 0 ignored**: 15 core library, 26 transport/native integration, 11 video/storage, 3 desktop library and 4 desktop workflow tests. |
| Rust formatting | `cargo fmt --all -- --check` passed. |
| Rust static analysis | `cargo clippy --locked --workspace --all-targets -- -D warnings` passed. |
| Python source compilation | `python3 -m compileall -q research tests packaging firmware/patch_jpegdec.py` passed. |
| Explicit read-ahead sanitizer run | Five tests passed, including both normally skipped ThreadSanitizer worker/actual-codec-engine tests and three ASan/UBSan cases. |
| Initial complete Python discovery | 119 cases ran; one stale production-environment assertion failed, 13 were skipped (two optional TSan and 11 PowerShell-dependent cases). This is not a passing result. |
| Final complete Python discovery | **119 passed, 0 failed, 0 skipped**, with `MVB_RUN_TSAN=1` and the installed PowerShell on PATH. Firmware host/SDK guards, DMA/decoder/prefetch/transform suites and Windows package/helper tests all executed. |
| Selected production ESP32 build | PlatformIO `cyd_optimized_input_aligned` passed. Static RAM 42,332/327,680 bytes; flash 653,940/1,310,720 bytes. These are build totals, not runtime allocation or performance measurements. |

The fresh production image is 665,808 bytes, SHA-256
`6a6db4b5362d34d1bc21c855a9ecaf501c9b28a5ade142a77a698dc9234221e9`.
It differs from the prior frozen hardware-qualified image and is **build-verified
only**. No previous FPS/image-integrity result is attributed to this fresh binary.
The prior frozen image is preserved privately, with its exact original SHA-256.

Firmware compilation still reports existing dependency warnings: unavailable
TFT_eSPI touch functions because `TOUCH_CS` is unset, an unused TFT_eSPI variable,
an ESP SDK assertion declaration, and Arduino `String` maybe-uninitialized
diagnostics. They were not hidden or removed. The application does not advertise
touch controls. A separate Rust advisory scanner was not installed; Clippy and
the executable tests above ran successfully.

## Previous hardware results, not rerun for this audit

[Final firmware endurance evidence](evidence/final-firmware-playback-endurance.json)
records full **320×240**, **29.799234 FPS**, 8,951 completed draws over
**300.376844 active seconds**, 25 separate complete 76,800-pixel region checks
(1,920,000 pixels total), **zero mismatches**, and **zero DMA ownership errors**.
This workload used 25 repeated 12-second clips with restart/readback gaps; it was
five active minutes, not uninterrupted playback of a single long video. The
frozen image SHA-256 is
`3de1a18fdd0eb993c6fb53a311317ebe62e2b82b07ec634f030d21496483343f`.
The configured/observed TFT pixel clock was 40 MHz, command and SD clocks were
20 MHz, and the transfer UART remained 115200 baud.

Other earlier clean runs around 29.97 FPS used separate configurations/native
sizes; they are not substituted for this full-screen endurance result. The
[hardware report](REAL_HARDWARE_PERFORMANCE_REPORT.md) and
[native desktop USB report](NATIVE_DESKTOP_USB_E2E.md) retain the previously
verified real Linux desktop → USB → ESP32 → installed microSD → playback workflow,
including final 2,444,120-byte size/SHA-256 acceptance, cancellation/resend and
live controls. All physical results remain previous measurements.

## Limitations and reproducible commands

Windows runtime/physical USB and additional Linux distributions are unverified
by these checks. FAT flush/rename cannot guarantee recovery from sudden power
loss. The `.mvb` format is experimental; audio, arbitrary rotation, touch UI and
calibrated classic-ESP32 temperature remain unavailable. Transforms can reduce
FPS. Simulator codec substitutes cannot prove the opaque Espressif decoder's
physical output behavior; the separate prior hardware evidence defines that scope.

The exact old matching ELF/object relink archive was not retained. Arduino core
and SDK packages identify LGPL-2.1-or-later components. Therefore the public release
uses firmware source/build instructions rather than distributing an incomplete
firmware binary/relink kit. Existing private test packages are not public release
assets. Firmware source and dependency provenance remain in the source repository.

```sh
ASAN_OPTIONS=detect_leaks=0 python3 research/build_native.py --sanitize
ASAN_OPTIONS=detect_leaks=0 MVB_SIMULATOR="$PWD/.verification/native/mvb-simulator" cargo test --locked --workspace -- --include-ignored --test-threads=1
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets -- -D warnings
MVB_RUN_TSAN=1 python3 -m unittest discover -s tests -p '*_test.py' -v
.dev-tools/platformio/bin/python -m platformio run -d firmware -e cyd_optimized_input_aligned
```

The Python command requires PowerShell on PATH for its helper tests. TSan requires
a compatible host; the explicit TSan cases passed on this host. Detailed new logs
are retained in ignored `.verification/release-audit-*.log` files. The three
authoritative Muse documents retain their original SHA-256 values.
