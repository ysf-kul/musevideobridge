# Phase 2 report — Muse Video Bridge

Date: 2026-11-11. Scope: video engine, mounted-card export, ESP32 firmware,
playback/transforms, serial controls/upload, telemetry and host verification.
**Phase 3 desktop UI integration has not started and requires approval.**

## Result and verification boundary

The MP4 converter, binary container reader/writer, verified SD-folder exporter,
serial client and C++ playback/upload engine are implemented. The firmware **compiles
for classic ESP32** using the original one-USB CYD reference profile. The shared
engine executes real JPEGDEC playback in an explicitly labeled Linux host simulator;
the Linux serialport adapter also passes through a pseudo-terminal.

**No physical firmware installation or TFT playback occurred.** The existing owner
permission permits read-only identification, not flashing, SD writes or device
configuration changes. `/dev/ttyUSB0` remains `root:uucp` mode 0660 and inaccessible
to this session. No device command, reset, flash erase/write, card format/write or
permission change was performed in Phase 2. Tests write only generated local files
and isolated host test directories. Actual board revision, controller, free heap,
flash/PSRAM, card filesystem and achieved FPS remain physically unverified.

All three mandatory Muse documents were read and preserved byte-for-byte. Phase 1
research/report remains historical; current protocol differences are documented in
[PHASE_2_PROTOCOL.md](PHASE_2_PROTOCOL.md). The repository still has no initialized Git
history. No complete GUI, fake telemetry, production compatibility guarantee or
macOS support has been added.

## Implemented files

| File or directory | Implemented responsibility |
| --- | --- |
| `Cargo.toml`, `Cargo.lock`, crate manifests | Rust workspace, pinned dependencies, minimum Rust 1.90 |
| `crates/muse-video-core/src/lib.rs` | Structured stable error codes and shared cancellation token |
| `container.rs` | Checked 96-byte header, rational timestamps, streamed index writer, bounded random/sequential record access and CRC verification |
| `jpeg.rs` | Bounded JPEG stream framing; baseline dimensions, components, quantization/Huffman tables and entropy marker checks |
| `media.rs` | MP4 validation/probe, normalization, FFmpeg conversion, progress/cancel/watchdog, full output decode verification, digest/manifest publication |
| `storage.rs` | Linux/Windows destination adapters, filesystem/capacity checks, verified no-clobber export, retained partials and backed-up selection |
| `protocol.rs` | Bounded version-1 COBS/CRC frame codec |
| `serial.rs` | Selected-port access, finite read timeout/retries, fresh-session handshake, events, reliable upload/resume and explicit simulator transport |
| `localization.rs`, `localization/*.json` | Repository-owned Turkish/English catalogs, Turkish default, localized errors and key-parity test |
| `crates/muse-video-cli/src/main.rs` | Real probe/convert/verify/frame/destination/export/ports/device CLI; JSON results/progress and Ctrl-C cancellation |
| `crates/muse-video-core/tests/video_storage.rs` | Real FFmpeg, metadata/aspect/VFR/rotation, corruption, cancellation, storage and size-bound tests |
| `crates/muse-video-core/tests/device_engine.rs` | Shared C++ engine integration, Linux PTY, real decode/playback/control, retries/resume/hash failure and corrupt-table rejection |
| `firmware/platformio.ini` | Original ILI9341 reference GPIOs, conservative SPI clocks, pinned platform/libraries |
| `firmware/include/wire.hpp` | Fixed-buffer framing, CRC, resynchronization and incomplete-frame timeout |
| `video.hpp`, `jpeg_validation.hpp` | Bounded file/container/JPEG validation and 64-entry index cache |
| `timing.hpp` | Rational media clock, pause/seek, target-rate selection, late/superseded-frame accounting |
| `render.hpp` | Tiled nearest-neighbor scaling, clipping, four quarter-turns and clearing vacated image regions |
| `engine.hpp` | Actual JPEGDEC playback, validated controls, incremental hash/index/upload validation and recoverable upload state machine |
| `messages.hpp`, `theme.hpp` | Keyed English device copy and centralized TFT reductions of existing Muse dark color roles/spacing |
| `jpeg_portable.hpp`, `firmware/patch_jpegdec.py` | Safe unaligned loads and hash/version-checked corrections to project-managed JPEGDEC sources |
| `firmware/src/main.cpp` | ESP32 TFT/SD/SHA adapters, waiting screen, FreeRTOS serial queues/task, engine loop and genuine nullable telemetry |
| `firmware/native/core_tests.cpp`, `simulator.cpp` | Sanitized native protocol/render/timing checks and host adapters using the same playback/upload engine |
| `research/build_native.py`, `platformio-requirements.txt` | Reproducible native build and pinned PlatformIO Python tool environment |
| `README.md`, this report, `docs/PHASE_2_PROTOCOL.md` | Setup/run commands, current implementation contract, acceptance results and remaining work |

Paths in the table without a directory continue under the preceding source directory.
Generated binaries/dependencies/test files remain ignored under `target`, `.pio`,
`.verification` and `.dev-tools`. No source was copied wholesale from the older
application. Its previous JPEGDEC portability findings informed narrow reviewed fixes;
there is no build/runtime dependency on that application's virtual environment.

## Video processing and container

The desktop core accepts an existing local `.mp4`, confirms an MP4 demuxer/video stream
through FFprobe and exposes duration, raw resolution, average/nominal FPS, SAR/DAR,
rotation, codec and interlace metadata. Missing timing metadata remains nullable.
Inputs above the configured resolution/resource limit and HDR transfer functions without
qualified tone mapping are rejected. Unsupported/corrupt files and missing/incompatible
tools fail with stable error codes and bounded diagnostics. Local process arguments are
passed without a shell; file/pipe protocol whitelist prevents network media fetching.

Conversion normalizes square pixels and source rotation, supports contain/crop,
deinterlaces flagged content, resamples to rational CFR and encodes independent 8-bit
baseline YCbCr 4:2:0 JPEGs. Default is **10 FPS, qscale 8**, maximum active dimensions
320×240. User FPS/qscale remain unchanged. Qscale **2…31, lower is higher quality**;
FPS 1…30 is an input bound, not a physical performance promise.

JPEG bytes stream through a buffered pipe into the experimental `.mvb` writer. Records
and offsets/PTS/length/CRC index are produced without individual JPEG files or a whole
video in RAM. Header/index/frame/padding bounds match [CONTAINER.md](CONTAINER.md).
Maximum frame is **49,152 bytes**; maximum file **2,147,483,647 bytes**, intentionally
below FAT32's 4 GiB−1 boundary for conservative embedded seek APIs. Index cache is 1,536
bytes; count is bounded to 1,000,000 as well as file arithmetic. Total duration is computed
from encoded frame count/rate, independently of uncertain source-duration estimates.

Output size estimation reports frame count when source duration is known and a worst-case
bound based on the compressed-frame cap. It is explicitly not a promised compression
ratio. FFmpeg progress, validation progress and copy/upload byte progress are separate.
All output frames are CRC/structure checked and decoded with FFmpeg before publication.
SHA-256 and the exact options/source metadata/tool version are written to `manifest.json`.
JPEG table selectors/canonical counts/entropy marker boundaries are validated before
entering JPEGDEC, including rejection of malformed tables with valid outer CRCs.

Job directory is exclusively created. Cancellation kills the owned FFmpeg process through
a watchdog, including when stdout has closed before process exit; stderr is drained with
an 8 KiB diagnostic tail. Failed/cancelled output retains `.part` and `failure.json`; retry
uses a new directory. Publication is no-clobber. No original MP4 is modified. Converted
media and manifest publication are separate renames; a rare interruption between them
can leave complete media without published manifest, which must be treated as an
incomplete job by Phase 3. There is no hidden cleanup of old jobs.

The format remains **experimental**, preserving the approved benchmark gate. Native
correctness is not evidence that it outperforms MJPEG AVI on actual SD hardware.
Identical-payload AVI/custom comparisons, long-file startup/seek and format freezing remain.

## SD export and serial transfer

Direct mounted-card export is primary. Linux uses mount information/statvfs and `lsblk`
FAT32 FSVER confirmation; Windows has a volume/free-space API adapter. Ambiguous/unsupported
filesystems are rejected. No capacity is hardcoded to the installed 4 GB card. Space
checks include metadata margin and file-profile limits. Explicit host-only test-directory
mode is available in the CLI, not intended for product UI exposure.

Media copies to an exclusively created `.part`, syncs, rereads SHA-256 and validates every
record before no-clobber publication to `muse-video/<digest>.mvb`. Same digest can be reused;
a different existing file is preserved. Recognized prior `active.json` is backed up before
selection replacement. Unrecognized files/symlinks are rejected and retained. Inputs,
unrelated card files and other completed videos are not deleted. Interrupted writes leave
identifiable staging. Filesystem flush/rename and selection backup reduce risk but do not
make FAT storage transactionally safe against sudden removal/power loss. OS safe eject is
still required. A real mounted FAT32 card was not available for runtime tests.

Optional serial upload implements nonzero transfer IDs, envelope sequence/CRC, ≤1024-byte
chunks, ACKs, duplicate verification, bounded retries, accepted/durable offsets, progress,
cancellation and checkpoint-based restart recovery. It pauses playback to preserve SD
ownership. File/journal/hash work is incremental; commands/status remain responsive.
A resumed prefix is validated before chunks are accepted. Hash, index, frame CRC and actual
JPEGDEC decoding all precede publication. Existing matching media is verified/reused and
its duplicate staging removed. Interrupted partials remain outside the playable library.

The 96-byte checkpoint journal appends at 64 KiB progress boundaries, is bounded, and
is included in capacity estimates. Explicit discard only touches the identified app-owned
transfer, requires confirmation in the command, and is rejected while receiving/finalizing.
A six-second loss of contact checkpoints/stops an active transfer. Damaged/ambiguous journal
recovery fails closed and requires manual inspection; no automatic formatting/deletion.
See [PHASE_2_PROTOCOL.md](PHASE_2_PROTOCOL.md) for exact fields and state transitions.

Only **115200 baud** is enabled. At 8N1 the theoretical payload-free upper bound is
11,520 bytes/s before framing/ACKs/SD work; direct card export is faster. Higher UART rates
are deferred until physical testing. The Linux PTY validates port/framing behavior without
proving CH340 reset behavior, real UART throughput or cable stability.

## Firmware, hardware profile and rendering

| Component | Build configuration; installed-unit verification remains open |
| --- | --- |
| MCU | Classic ESP32, 240 MHz configuration, 4 MB flash profile, no PSRAM dependency |
| TFT | Original ILI9341 candidate, 240×320 panel rotated to 320×240 landscape |
| TFT GPIOs | SCK14, MOSI13, MISO12, CS15, DC2, BL21, reset on board EN |
| TFT SPI | HSPI, conservative **20 MHz** write / 10 MHz read |
| SD GPIOs | VSPI SCK18, MOSI23, MISO19, CS5; initial **10 MHz** |
| Resistive touch | Separate original wiring; CS33 held high/deselected; no touch UI/driver implemented |

These are Phase 1 reference-design pins, **not electrically verified pins on the owner's
unit**. The dual-USB ST7789 configuration is not used. Conservative clocks preserve a
realistic starting point; they must be qualified along with orientation/RGB order. TFT
and SD use different physical SPI hosts; touch is not incorrectly attached to a third
hardware SPI bus or TFT_eSPI's TFT touch wiring.

The Arduino loop owns SD files, JPEGDEC and the display. A separate core-0 serial task
parses/transmits fixed buffers and passes commands through bounded FreeRTOS queues.
Replies/ACKs use a separate higher-priority queue from lossy frame/telemetry events.
Playback/hash/index/upload work advances between command dispatches. Frame decode/draw
is still synchronous within one frame; seek/control latency can include one frame's work
and one driver SD call. Hardware benchmarking must establish those bounds, not claim
zero-latency operation or nonblocking SPI hardware.

Boot without valid media draws the actual keyed English waiting screen:

```text
Muse Video Bridge
Waiting for video...
Insert SD card or send a video from your computer.
```

It also draws card/connection/error text. Recognized active selection or a compatible
library file triggers incremental validation then automatic playback. Read/missing-media
errors stop safely and return to the waiting/error screen. Periodic library checks and
read-failure mount invalidation support reinsertion; Arduino SD may cache card presence,
so paused-card hot removal remains a physical qualification gap. The native waiting
adapter emits JSON and PPM, not a claim of a rendered physical TFT test.

Sequential indexed records are CRC checked into one compressed buffer, JPEGDEC produces
RGB565 tiles, and the renderer clips/scales/transposes tiles into a 320-pixel output row.
No full RGB source/destination framebuffer or PSRAM is required. Implemented controls:
position, width/height, scale, aspect lock, rotations **0/90/180/270**, target FPS,
pause/resume/stop/restart and seek. Bounds reject invalid integers/overflowing rotations,
unsupported angles and aspect-inconsistent rectangles. Changed settings take effect on
subsequent draw/redraw without reconverting MP4. Downscaling is nearest-neighbor, not
high-quality resampling. Aspect-locked quarter-turns swap the rectangle around its center.

Arbitrary rotation is **unavailable**. Requested FPS caps selection on the encoded media
timeline. Lower target FPS intentionally skips source frames; eligible frames missed due
to late work count separately as drops. Two-second actual FPS counts completed timed draws,
not target FPS, seek redraws or fake progression. It is null before its first window.
The native integration test caught and verified the fix for a stale startup timestamp
that previously skipped immediately to the final frame.

## Genuine telemetry and memory

| Measurement | Implemented source / unavailable behavior |
| --- | --- |
| Free/minimum heap, largest block, allocator total | `heap_caps_get_*`, internal 8-bit capability; allocator pool is distinguished from SoC physical SRAM |
| SoC RAM and PSRAM | Classic SRAM specification 532,480 bytes is labeled separately; `ESP.getPsramSize/getFreePsram`, no PSRAM dependency |
| CPU frequency / flash capacity | Arduino ESP detection APIs; not physical measurements made during this phase |
| CPU utilization | Supported `uxTaskGetSystemState` idle-runtime deltas, mean across both cores, at least 500 ms sample interval; first/invalid/missing snapshot returns null |
| CPU temperature | Null with `unsupported_calibrated_classic_esp32_sensor`; no deprecated raw reading fabricated as Celsius |
| SD capacity/free/card bytes | SD library queries, capacity cached for ≤10 seconds; absent card returns null |
| SD read timing/throughput | Compressed payload bytes divided by measured read/CRC/index access duration; this is observed playback-path throughput, not a standalone benchmark |
| Wi-Fi/RSSI | Read-only `esp_wifi_get_mode` / `esp_wifi_sta_get_ap_info`; disabled/disconnected/AP/connected state, RSSI only with an associated AP; no Wi-Fi initialization or credentials |
| Uptime | `esp_timer_get_time`, explicitly microseconds |
| Playback | Last successfully drawn frame/PTS, media clock, actual FPS, dropped/rate-skipped/late frames, errors and read/decode/draw microseconds |

The pinned Arduino SDK has trace/runtime stats and ESP timer accounting enabled; this
was verified in its configuration and the CPU-load path compiled. Counter differences
are unsigned to handle one wrap within ordinary telemetry windows; short/inconsistent
windows return unavailable. CPU-load accuracy and idle task accounting need real hardware
validation. Native adapters label `simulator: true`, identify host monotonic time and set
ESP32 RAM/CPU/temperature/PSRAM/flash values null. They do not fabricate hardware values.

Final firmware build reports **36,884 bytes static RAM / 327,680** and **627,214 bytes
application flash / 1,310,720**. Debug ELF type metadata reports **74,352 bytes** for the
dynamically allocated Engine (including 49,152-byte compressed frame, 17,884-byte JPEGDEC,
index cache, receipts and renderer). Ten fixed queue entries add about **20,640 bytes**
plus queue bookkeeping; serial task requests 8,192 stack bytes and the Arduino loop also
needs its stack. SDK/SD/JSON allocations and fragmentation are additional. Static RAM
usage is therefore **not total runtime RAM or measured free heap**. The real board has
not reported these metrics. A full 320×240 RGB565 frame would add 153,600 bytes; two cost
307,200 and are intentionally avoided.

At configured 20 MHz, full-screen RGB565 SPI payload alone costs **61.44 ms** before
command overhead, SD read, CRC, JPEG decode and transformations. Tiled row transactions
and 90° turns may cost considerably more. Default requested 10 FPS is a trial setting;
no sustained 10/15/30 FPS claim is made. DMA/batched drawing, JPEG-native reductions,
higher clock rates and double buffering are future measured optimizations, not hidden
implemented features.

## Dependencies and reproducibility

| Dependency | Version / purpose / license / scope assessment |
| --- | --- |
| FFmpeg/FFprobe | External host executables; tested 9.0.2; essential media processing; build-dependent LGPL/GPL/nonfree licensing, distribution decision deferred |
| serde / serde_json | 1.0.229 / 1.0.154; DTO/catalog/protocol serialization; MIT/Apache-2.0; pinned, no network runtime |
| sha2 / crc32fast | 0.10.9 / 1.5.4; whole-file identity and bounded corruption checks; MIT/Apache-2.0 |
| serialport | 4.10.1; Windows/Linux selected-port transport; MPL-2.0; blocking thread-compatible API, Linux libudev dependency |
| libc / windows-sys | 0.2.191 / 0.61.2; isolated OS filesystem/PTY adapters; MIT/Apache-2.0; target-specific compilation |
| ctrlc | 3.5.1; CLI owned-job cancellation; MIT/Apache-2.0 |
| tempfile | 3.27.0; isolated tests only; MIT/Apache-2.0 |
| PlatformIO | Core 6.2.0, Espressif32 platform 8.0.0; build tooling only; Apache-2.0; Python dependencies frozen in repository |
| Arduino-ESP32 / SDK | 3.3.12 via pinned platform; hardware adapters; Arduino LGPL-2.1, SDK components' own licenses retained |
| TFT_eSPI | 2.5.43; qualified reference-controller candidate; FreeBSD license; only small fonts enabled, no full-frame sprite |
| JPEGDEC | 1.8.5; actual bounded JPEG decode; Apache-2.0; narrow checked portability corrections, no broad library fork |
| ArduinoJson | 7.4.2; firmware/native control DTOs; MIT; bounded envelope/nesting, JSON memory allocated for one command/event |
| OpenSSL | Host-native SHA adapter only; system dependency, not firmware/Rust runtime; current OpenSSL Apache-2.0 licensing |
| Tauri/Svelte/TypeScript | Approved future UI stack; deliberately not added in Phase 2 |

Version locks and license notices are retained; no runtime AI, provider SDK, account or
cloud conversion dependency exists. JPEGDEC patch checks original source SHA and exact
replacement counts, preserves upstream notices and modifies only downloaded project-managed
sources. It fixes negative Huffman shifts, overlapping copies and a signed negative shift;
a memcpy-based forced-include handles unaligned word access. Input prevalidation adds a
separate guard around decoder assumptions. Native ASan/UBSan tests pass; this is not a
proof against every malformed JPEG or an ESP32 memory-safety test.

No release binary is packaged here. The host debug CLI is approximately 14 MiB on this
system (debug information included), not a release-size commitment. PlatformIO tools/SDKs
and FFmpeg are development/runtime prerequisites, not silently bundled UI dependencies.
The project's own distribution license remains unselected; packaging/attributions and
an appropriately licensed FFmpeg distribution must be resolved before release.

## Tests actually run

Environment: available CachyOS Linux VM, Rust/Cargo 1.99.1, Python 3.14.9, clang 23.1.4,
FFmpeg/FFprobe 9.0.2. PlatformIO Core 6.2.0 / Arduino 3.3.12 / Espressif32 8.0.0.

| Check | Actual result |
| --- | --- |
| `cargo build --locked --workspace` | Passed; Linux host CLI/core build |
| `cargo test --locked --workspace` | **7 unit + 9 real-FFmpeg/storage integration tests passed**; six native-engine tests explicitly ignored here pending separate simulator build |
| Native-engine explicit command in README | **All 6 passed** under the ASan/UBSan simulator; including actual Linux serialport through a PTY |
| `python3 research/build_native.py --sanitize` | Passed native core executable and simulator compilation; **320 transformation cases**, four dirty-region cases, CRC/framing bounds, rational/pause/seek/drop timing |
| `cargo fmt --all -- --check` | Passed |
| `cargo clippy --locked --workspace --all-targets -- -D warnings` | Passed |
| `clang-format --dry-run --Werror` on own C++ sources | Passed |
| PlatformIO `cyd_ili9341` firmware build | Passed; static/application sizes above; no flash operation |
| Local documentation/catalog/hash checks | Passed; mandatory document hashes unchanged |

There are **22 Rust tests executed across the normal and explicit integration commands**,
in addition to native core assertions. Coverage includes actual MP4 metadata/duration,
landscape/portrait/non-square SAR, contain/crop, rotation/VFR normalization, option retention,
full frame indexing/CRC/decode, corrupt/truncated/mutated headers/index/payload, >2 GiB sparse
file rejection, malformed Huffman tables, no-clobber and unrelated-file preservation,
symlink rejection, capacity boundaries, real conversion cancellation/retry, closed-stdout
watchdog timeout/cancel, invalid/stale/conflicting commands, waiting state, true frame
progression, pause/seek/quarter-turn/scale/FPS, null telemetry, chunk duplicates/conflicts,
dropped ACK retry, cancel/reboot resume, re-upload reuse and whole-file digest mismatch.
Incremental hash/index validation is exercised while polling status rather than blocking it.

A separate CLI conversion of the Phase 1 pattern produced **40 frames at 10 FPS,
320×180, 239,360 bytes, qscale 8**. High-detail noise at selected qscale 4 failed with
`frame_too_large`, retained incomplete output and did not silently lower quality.
Generated research artifacts remain under `.verification`, not user data/card storage.

Early test/lint failures were corrected before these final passes: an unnecessary Rust
wrapper/formatting issue, the startup timestamp underflow, lower-FPS eligible-frame
selection, resume-before-prefix-validation, closed-stdout watchdog locking, extreme-angle
integer overflow and FFmpeg's valid shared quantization table. A strict selection-file check also required the const JSON object type and was verified by re-upload tests. Rotation fixtures use the
current FFmpeg display-rotation option rather than ineffective legacy metadata tagging.
No failed physical test is disguised as a build result.

The firmware build still emits upstream TFT_eSPI's expected missing-TOUCH_CS warning
(touch wiring is intentionally separate/deselected); a full dependency build also emitted
an upstream sprite signedness warning. Own code has been formatted and native builds use
warnings as errors. No Windows build/runtime, real TFT render, SD throughput/endurance,
physical UART/reset, removal/power-loss, runtime heap/CPU telemetry or thermal test ran.
No full GUI/theme/keyboard/screen-reader acceptance ran because the desktop UI is deferred.

## Muse design and remaining work

Device copy is keyed English as explicitly requested. Centralized TFT colors reduce the
existing Muse Downloader dark roles (`surface #1a221d`, `onSurface #e3e9e0`) to RGB565;
spacing is centralized. No alternate brand/logo was invented. Desktop catalog key parity
and Turkish default are tested. Full Material 3 Expressive light/dark themes, fonts,
responsive controls, accessibility and preview reconciliation belong to Phase 3.

Before physical release qualification:

1. Confirm the actual PCB/controller/flash and qualify reference GPIOs, LCD orientation,
   RGB order, 20/10 MHz clocks and real SD filesystem with separately authorized hardware work.
2. Install firmware only after explicit flashing permission; test waiting/playback,
   insertion/removal, controls, UART/DTR behavior, real heap/CPU measurements and error recovery.
3. Benchmark sustained 8/10/12/15 FPS at native/scaled/quarter-turn sizes; measure read/decode/draw,
   drops, seek/startup and memory. Compare identical AVI/custom payloads before freezing `.mvb`.
4. Qualify FAT32 direct writes, safe replacement, interrupted power/card writes and resume at
   large file sizes. Expand journal recovery only with conservative measured guarantees.
5. Phase 3: Tauri 2/Svelte 5/TypeScript UI, local dialogs, confirmed-frame preview, one simple
   workflow, requested/applied state, persistent tr/en/theme settings, coalesced controls,
   disconnect freshness, bundled-tool strategy and Windows/Linux packaging/runtime tests.

The core, compiling firmware and available host tests are ready for review. **Await
approval before Phase 3.** Existing read-only device authorization remains unchanged.
Run commands are in [README.md](../README.md); no automatic firmware upload is part of setup.
