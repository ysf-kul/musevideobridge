# Phase 1 completion report — Muse Video Bridge

Date: 2026-11-11. Scope: discovery, research, realistic architecture and documentation.
**Phase 2 has not started and requires approval.**

## Main recommendation

Build a small **Tauri 2 / Svelte 5 / TypeScript desktop with a Rust core**, and a
separate **C++ Arduino-ESP32 firmware** using explicit board adapters, JPEGDEC and
a qualified TFT_eSPI controller profile. Convert local MP4 to independent baseline
JPEG frames on the desktop, store them sequentially on SD, decode/draw them on ESP32.
Use direct mounted-SD export first and USB Serial for control/monitoring. Serial file
upload is a slower optional continuation. No MP4/H.264 decoding on the ESP32.

Recommend an **indexed custom JPEG container provisionally**, not as a finalized
format. Its bounded records, absolute offsets/PTS and integrity checks simplify this
firmware's scope. Standard MJPEG AVI is a credible alternative with mature tooling;
the final choice requires identical-payload actual SD/ESP32 benchmarks. File size alone
does not justify custom format, and decoder speed is identical for identical JPEGs.

## Changes delivered

The previously empty foundation now contains a README, 12 linked technical documents,
source/evidence records, a machine-readable reference hardware profile, Turkish/English
seed catalogs with an explicit Turkish-default policy, an isolated host-only encoding
research script and ignore rules. No application/firmware was copied or implemented.
All three authoritative Muse files are preserved unchanged. No Git history was created.

| Requested Phase 1 output | Delivered reference |
| --- | --- |
| Complete hardware research | [HARDWARE.md](HARDWARE.md), inspected original drawings and independent compatible configs |
| Specifications and pins | Source-checked reference table/profile; explicit installed-unit verification gaps |
| Encoding/container recommendation | [VIDEO_PIPELINE.md](VIDEO_PIPELINE.md), [CONTAINER.md](CONTAINER.md), measured host payload/AVI sizes |
| Conversion architecture | Probe/filter/stream/index/validate/publish pipeline with bounded memory/error handling |
| USB Serial proposal | [SERIAL_PROTOCOL.md](SERIAL_PROTOCOL.md): COBS/CRC, session/sequence, commands/events, transfer/recovery |
| Firmware architecture | [ARCHITECTURE.md](ARCHITECTURE.md): storage/display ownership, scheduler, render/measurement adapters |
| Desktop architecture | Same document: Rust processing/serial/storage boundaries and one focused Svelte workflow |
| Memory/performance estimates | [PERFORMANCE_AND_TELEMETRY.md](PERFORMANCE_AND_TELEMETRY.md) |
| Known limitations | Hardware/transform/temperature/FPS/card/preview/platform limits explicitly documented |
| Three-phase roadmap | [ROADMAP.md](ROADMAP.md), benchmark and acceptance gates |
| Project documentation | README links all specifications, design/localization/dependencies/sources/verification |

## Current hardware result

The owner confirms **ESP32-2432S028R, one USB connector, 2.8-inch resistive display,
4 GB installed microSD; PCB revision unknown**. Linux enumerated CH340-family USB
VID:PID `1a86:7523` at `/dev/ttyUSB0`. This does not establish exact bridge subtype,
MCU revision, flash size or LCD controller.

Original MCU/LCM schematic images were inspected at full resolution and cross-checked
against compatible configurations. Reference LCD pins are SCK14/MOSI13/MISO12/CS15/
DC2/BL21, reset on EN. SD uses SCK18/MOSI23/MISO19/CS5. XPT2046 uses CLK25/MOSI32/
MISO39/CS33/IRQ36 on separate physical wires. These are **reference-design verified,
not electrically verified on the current unit**. LCD and SD consume the two available
general-purpose SPI hosts; touch requires a deliberate adapter rather than a third
independent hardware-SPI assumption.

One-USB points toward the original **ILI9341** candidate. The explicit CYD2USB
configuration uses **ST7789**, so it must not become the default based on the older
local project. Exact controller/revision and visible color/orientation remain unverified.
Original 25Q32 flash corresponds to 4 MiB; classic ESP32 SRAM is 520 KiB shared
between data/instructions; actual free heap and PSRAM were not measured.

The authorized ROM-only flash-ID command failed **before port open** with errno 13,
permission denied. This session lacks the tty's `uucp` access and non-interactive
sudo. No hardware command/reset reached the board, no RAM stub loaded, no firmware
flashed, no flash erased/modified, no SD written, and no configuration/permissions
changed. Physical chip/flash identification is honestly incomplete.

## Video, transforms and performance findings

Proposed encoding: 8-bit baseline Huffman YCbCr 4:2:0 JPEG, active image fitted within
320×240, rational CFR, initial 10 FPS/qscale 8 and per-frame 48 KiB bound. Preserve
aspect through square-pixel normalization; firmware draws black outside the image.
Reject unsupported input or disclose an effective lower quality; never silently drop
oversized frames. The draft container stores sequential records plus a paged fixed-size
offset/PTS index, without thousands of card files or whole-video RAM allocation.

Live X/Y, width/height/scale and four quarter-turns are designed as firmware transforms.
Native JPEG reductions/tile rendering provide a low-memory path; one optional RGB565
source frame is 153,600 bytes. Two frames cost 307,200 bytes and are not the baseline.
General scaling/90° transposition may cost FPS and need measured batching/sampling.
Arbitrary-angle rotation is **optional, experimental and unavailable until justified**.
No supported calibrated CPU-temperature path is qualified for classic ESP32; show
unavailable. CPU load needs real FreeRTOS runtime accounting or also stays unavailable.

At 40 MHz, full-screen RGB565 SPI payload alone costs 30.72 ms, before read/decode/
draw overhead. Target 8/10/12/15 FPS for benchmarks; no guaranteed 15/30 FPS claim.
Requested FPS caps presentation on the encoded media timeline; lower caps intentionally
skip source frames, and late-work drops are separate. Pause/resume/seek use an absolute
monotonic clock and index. Desktop preview uses matching prepared JPEGs and the last
device-reported frame/transform, with explicit transport freshness rather than a false
promise of simultaneous TFT capture.

## Checks actually run

The host experiment created two synthetic H.264 MP4 inputs and 12 AVI conversions at
10/15 FPS and q=4/8/12. **600 JPEG packet headers** were verified as 8-bit baseline
4:2:0 with fixed case dimensions; all 12 AVI outputs decoded through FFmpeg on CachyOS.
Pattern frames averaged ~4.9–8.3 KB; high-detail/noise frames ~39.4–65.4 KB, with a
maximum **66,031 bytes**. All 100 q=4 noise frames exceeded the proposed 48 KiB cap.
AVI size was measured; draft-container size was only modeled from those exact packets.
No custom-format parser, device decoder or SD speed benchmark is claimed as tested.

JSON/catalog/reference consistency, local documentation links, research-script syntax,
host evidence integrity and unchanged mandatory-document hashes are recorded in
[VERIFICATION.md](VERIFICATION.md). The hardware identification failure is retained.
No app unit/E2E/UI build suite exists yet, and no firmware was compiled in this phase.

## Muse design, platforms and limitations

The design plan adopts existing Muse Material 3 Expressive roles/tokens, Manrope local
fonts, light/dark/system themes, one clear conversion/export workflow, accessible
controls and responsive reflow. Seed catalogs establish Turkish default/English option;
the complete typed runtime localization service belongs to Phase 2. No UI/theme/
screen-reader/manual desktop acceptance has occurred.

Only host research ran on **CachyOS Linux in the available VM environment**. The app
is neither build-verified nor runtime-tested on Linux or Windows. Windows remains
untested; macOS is outside scope. Exact current board/controller/pins/flash/free RAM/
card filesystem/FPS remain physically unverified. Source-backed reference research
and failed permission-limited inspection cannot close those gaps.

The Phase 1 research command is `python3 research/benchmark_encoding.py` (FFmpeg/
FFprobe on PATH); it produces isolated generated evidence, not a runnable app.

## Approval boundary

The project foundation and reviewable recommendations are ready. Await the owner's
approval before Phase 2 application/firmware implementation. Existing read-only
identification authorization does **not** authorize firmware flashing, flash/SD
changes, formatting or device-configuration writes. Physical installation and tests
need their own authorized scope; the roadmap preserves that boundary.
