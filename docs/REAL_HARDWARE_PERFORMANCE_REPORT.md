# Real ESP32 playback and USB transfer investigation — 2026-11-13–14

**The final selected 320×240 firmware sustained 29.79923446 actual FPS over
300.376844 active seconds at a stable 40-MHz pixel clock. All 25 repeated clips
and 25 complete-region TFT-memory checks passed. An earlier direct-output image
sustained 29.87030423 FPS; that result is kept separate from the final binary.
Full-screen exact 30 actual FPS was not reached. The owner reports clean, smooth
video and accepted this result. The native Linux application transferred, verified
and automatically played the final 25-FPS video over USB with the card installed.
Windows runtime is untested; final archive verification is recorded separately.**

The connected device is the owner's ESP32-2432S028R, accessed through `/dev/ttyUSB0`
in the Linux VM. The original playback baselines were measured again: 2.8593 FPS
with row rendering and 4.4718 FPS with native blocks on the original 320×180 media.
The final image presents 8951 of 9000 full-screen 30-FPS frames in five active
minutes, with 49 drops and zero rate skips. Compressed read-ahead, Espressif block
decoding, one-block warmup and exclusive direct DMA strips overlap SD, CPU and
TFT work without a full-frame RGB buffer. Actual pixels/commands/SD clocks are
40/20/20 MHz; corrupt actual80-MHz output is rejected.

Earlier research reached 29.8703 full-screen FPS over five active minutes and
29.96691403 FPS in a clean pre-ROI full-screen run. Centered native 304×224 sustained
29.96488282 FPS, with a best run of29.99862506 FPS. Those are measured near30
results, not claims of exact30 or full-screen equivalence at the smaller size.
The native Linux desktop USB workflow transferred, verified and played a
2,444,120-byte default video on the final firmware, following a confirmed cancellation
and clean resend from zero. The earlier 3,327,948-byte acceptance remains separate.
Audio was already excluded, so no removal saving is claimed.

## 1. Project state found after re-inspection

The repository uses Tauri 2, Svelte 5/TypeScript and a shared Rust media/serial core,
with separate C++ Arduino-ESP32 firmware. All three mandatory Muse documents were
read completely and remain unchanged. Architecture and all three phase reports,
the converter/container, desktop serial actor, SD playback, JPEGDEC, TFT adapter,
USB transfer and existing test/evidence files were inspected. The technology stack
was retained.

The desktop converts an MP4 into independent baseline 4:2:0 JPEG records in an
indexed `.mvb` container. ESP32 initially used one compressed-frame buffer and
decoder tiles; the optimized implementation adds one bounded compressed read-ahead slot.
USB already had COBS framing, envelope CRC32, session/sequence IDs,
1024-byte stop-and-wait chunks, acknowledgement-based progress and checkpoint
recovery. Its implementation defects, rather than an absent protocol, were addressed.

The application starts in Turkish. English and light/dark/system themes remain
persistent options. Existing Muse Material 3 tokens and controls are reused.
Communication and this report are English. The source MP4 and unrelated SD media,
recovery data and flash settings are preserved.

ROM/runtime evidence identifies a classic ESP32-D0WD-V3 revision 3.1, 4 MB flash,
240 MHz runtime CPU and no usable PSRAM. The reference wiring actually used is TFT
HSPI SCK14/MOSI13/MISO12/CS15/DC2/BL21 and SD VSPI SCK18/MOSI23/MISO19/CS5.
The display is configured for ILI9341, 320×240 landscape; controller-readback and
clock-divider diagnostics were added rather than inferring identity from a working
library alone. Electrical pin tracing and visible-quality observation are separate
from firmware counters.

## 2. Original playback bottleneck

The same existing 320×180 prepared video was played centered at X0/Y30, with 10
encoded FPS and 30 requested FPS. Its original MP4 source rate/qscale were not
reconstructed from assumptions. The measured row path achieved **2.859292773 FPS**
(24 completed presentations / 8.393684 seconds); native block rendering achieved
**4.471765625 FPS** (38 / 8.497762 seconds).

| Baseline | Frame-fetch average | Decode average | Render average | Sum of measured stages | Whole-run time per completed frame |
| --- | ---: | ---: | ---: | ---: | ---: |
| Row renderer | 40.570 ms | 88.413 ms | 193.912 ms | 322.895 ms | 349.737 ms |
| Native block renderer | 38.989 ms | 87.907 ms | 76.054 ms | 202.950 ms | 223.625 ms |

Rendering was the largest row-path cost. Block submission reduced that cost by
about 60.8%; decode and frame fetching remained substantial. Baseline `average_read_us`
includes SD access, container/header work, CRC and JPEG structure validation. It
must not be described as an isolated SD-bus measurement. The remaining whole-run
time includes scheduling/control/telemetry effects and cannot be assigned to a
single component from those baseline counters.

Evidence: row baseline (local-only evidence: `../.verification/session-2026-11-13/hardware/baseline-row.json`)
and block baseline (local-only evidence: `../.verification/session-2026-11-13/hardware/baseline-block.json`).

## 3. Significant optimizations and experiments

| Change or experiment | Purpose and measured outcome |
| --- | --- |
| Native-size, unrotated JPEG blocks | Submit decoder tiles directly instead of per-row resampling/copying; physical original-media baseline comparison above and 500 host transform golden cases |
| Continuous TFT transaction and byte-order options | Continuous transactions are reported active; LE/BE compared at 20 MHz. BE alone did not improve end-to-end FPS: MCU8 blocks measured 6.4320 LE versus 6.3732 BE |
| Direct SPI divider update | A clock API invoked while owning a transaction deadlocked an early profiling run; corrected to update the owned bus without reacquiring its lock |
| TFT 20/40/80 MHz | Register-observed clocks matched each request. Early MCU8 BE block counters were 6.3732/7.9843/9.1323 FPS at SD10; later corrected readback rejects actual80 MHz because sampled output corrupts. These early80 counters are not clean playback results |
| Real DMA path | ESP-IDF SPI DMA initialized successfully, with two 2048-pixel internal DMA-capable tile buffers (8192 bytes total). Final completion is checked. SD10/MCU8 DMA40/80 achieved 9.6896/10.2433 FPS |
| Compiler speed profile | `cyd_performance` applies `-O3`. Completed full-screen matrix uses this build. Original 320×180 media differs from the new 320×240 clip, so their total improvement cannot be assigned solely to the compiler |
| MCU output batching | All seven display strategies were tested at MCU1/4/8. DMA80 improved from 7.5383 to 9.7938 to 10.2433 FPS. Larger batches remain separate experiments |
| SD clock candidates | Observed SD10→20 reduced typical read/seek time and raised DMA80 from 10.2433 to 11.0269 FPS. Requested SD40 remained observed20 and achieved 10.9917 FPS; no actual SD40 throughput claim |
| Inline-word JPEG reader | Safe byte assembly replaces tiny entropy-refill `memcpy` calls; actual Xtensa O3 disassembly and sanitizer tests verify removal. Physical 2048-pixel-tile trial showed no material decode gain |
| ROM CRC and redundant-seek fast path | CRC fell from about 1.71 to 0.58 ms. The combined word/CRC/seek trial reached 10.8819 FPS with DMA80/SD20, below the earlier 11.0269 short sample; no overall speedup is claimed |
| Explicit read-only stdio buffering | Reusable 8192-byte buffering improved a physical 153600-byte sequential read from about 447 to 134 ms. Frame reads still suffered seek/refill overhead: roughly 34 ms combined read/seek; DMA80 counter result was 11.3492 FPS before panel-readback qualification |
| POSIX read/lseek fast path | Bypasses Newlib refill/seek behavior on read-only files while preserving checked durable stdio writes. Typical frame SD read/seek fell to about 9.6/2.3 ms; DMA40/MCU20 reached 16.4037 counter FPS, subsequently confirmed by corrected-clock/pixel-sample trials |
| Full-width JPEG strips | 5120-pixel decoder output permits a 320×16 strip (20 MCUs), with two same-sized DMA buffers. Reduces native window count from tile groups to full-width strips; separate ROI hint now opens one address window per native frame |
| Coalesced checked frame record | Header, payload and padding are read together, with JPEG decoding at a buffer offset rather than a full-payload copy. CRC/container/JPEG validation remains active |
| Independent command/pixel SPI clocks | Explicit 10/20/40/80 MHz command experiments with 40/80 MHz pixels. A host-ownership/clock-restoration bug was found and fixed. Corrected actual 40 passes sampled GRAM checks; actual80 corrupts samples at every tested command rate and is rejected |
| Direct ROM TJpgDec | Reusable 6 KB workspace plus RGB565 strip; measured decode/color about 63 ms, including about 9.2 ms adapter conversion. DMA40 reached 11.7804 FPS before later readback qualification; slower than JPEGDEC and not selected |
| Software TJpgDec FASTDECODE2 | Pinned, ABI-separated software decoder with 10 KB scratch and 10 KB RGB565 strip. Physical full-clip DMA40 result 13.5119 FPS, zero 90 sampled-pixel mismatches, about 53.907 ms decode including 5.945 ms nested strip copy; slower than JPEGDEC |
| Espressif esp_new_jpeg block decoder | Pinned classic-ESP32 prebuilt codec with bounded aligned full-width RGB565 output. Before read-ahead, same full 320×240/q8 content reached 20.3679 FPS with DMA40/cmd20 and zero 144 sampled-pixel mismatches; read-ahead result follows below |
| Verified no-PSRAM compiler profile | Removes PSRAM cache workarounds only on this physically verified no-PSRAM CYD. JPEGDEC DMA40/cmd20 reached 18.9545 FPS and NewJpeg reached 20.0428 FPS at SD20, both with zero sampled mismatches; NewJpeg did not beat its earlier 20.3679 result |
| Bounded compressed SD read-ahead | Second compressed record slot and a 4096-byte worker stack, joined before every command/storage boundary. NewJpeg DMA40/cmd20 at actual SD20/UART2M reached 26.1697 FPS, 286 hits/28 misses, zero 189 sampled mismatches; mean foreground fetch wait 1.157 ms. Background read timings overlap decode/display and must not be added to foreground service |
| Isolated pipeline probes and profiling | SD read/seek, CRC, JPEG validation/open/decode, container preparation, transform, display and nested copy/DMA-wait metrics; probe results are separated from actual video FPS |
| Prepared resolution/quality/scaler matrix | Seven initial 30-FPS JPEG profiles plus optimized-Huffman/chroma/native-size follow-ups. Full q8, full q10 area and native 304×224 have physical playback results; some host-only profiles remain unqualified. q10 area reduced objective quality without improving FPS and is rejected |
| Sequential RGB565 research | Full 320×240 raw frame is 153600 bytes; four raw resolutions generated/full-decoded and sized on the host. On-device existing-file read and RGB565 stripe probes below are useful component measurements, not production raw-video playback |
| Identical-payload MJPEG AVI comparison | AVI mux/demux proves exact JPEG payload identity; file overhead alone is not an ESP32 speed comparison |
| Chroma comparison | 4:2:2/4:4:4 host candidates measured; firmware's validated 4:2:0 safety profile remains explicit |
| Normal-path transform separation | Native prepared size/zero rotation uses the block path; live position/size/four rotations retain clipped fallback rendering and vacated-area clearing |
| Reusable bounded memory | Existing compressed buffer/index cache/decoder strips retained; no per-frame application allocations or assumed full-frame double buffer. Five-minute active runs held end heap constant at both qualified sizes |
| Busy-loop pacing | Same-build 50/250/1000-ms intervals raised full-screen actual FPS to 29.64–29.72 in short tests. The one-second interval sustained 29.6836 FPS with copying; later direct DMA sustained 29.8703 FPS over five active minutes, with clean checks |
| Exclusive direct SDK output | Three 16-byte-aligned strips and FIFO DMA completion remove both decoder strip copies and TFT staging copies. Six clean same-build direct trials reached 29.8316–29.8860 FPS; direct five-minute endurance reached 29.8703 FPS. Exact final-image endurance reached 29.7992 FPS; separate build results are retained |
| Automatic fast defaults and input alignment | Cold boot selects NewJPEG/direct3/prefetch/warmup1/40-MHz pixels/20-MHz commands. Fixed/current/prefetch compressed records were 0mod16 in the aligned build. Three-run alignment differences are small and do not establish a universal causal speedup |
| Startup border clear | Clears only outside the prepared video; full 320×240 performs no separate clear. Clock anchor and completed-display denominator remain unchanged. Final exact-image endurance qualifies the resulting build |
| SD first-record preload | Three off/on pairs and a sampling-disabled follow-up measured active and preparation-inclusive timing. Gains were inconsistent, so preload is rejected and absent from production |
| Video-only aligned containment | Existing audio exclusion preserved. Default 16:9 output pads 320×180 to 320×184 without changing active pixels; physical USB verification and 75-frame playback reached 24.9950 actual FPS at target 25 with clean region checks |

The first profiled clock experiment timed out and is retained as a failed experiment:
profiled-blocks-20.json (local-only evidence: `../.verification/session-2026-11-13/hardware/profiled-blocks-20.json`).
An initial faster-baud discovery policy also failed on real hardware: two 1.5-second
attempts at each candidate produced six total ACK timeouts, 2131 malformed frames
and 17181 received bytes across the entire attempt. These cumulative counters do
not identify the first candidate's traffic. This is a failed connection test, not
an FPS or higher-baud success result.

The later SD20 DMA40 run was slower than the otherwise equivalent requested-SD40 /
observed-SD20 run (9.0452 versus 10.4174 FPS). Its measured SD read/seek rose to
36.697/16.689 ms, versus 27.023/11.938 ms. This variance is retained instead of
attributing it to the unachieved 40 MHz clock.

The optional IDF SD-DMA path and actual SD40 clock were physically tested and did
not improve complete JPEG playback. Five-minute active full/native endurance,
optimized-Huffman compatibility and a lower-quality area candidate are completed
below. Direct-DMA and exact selected-image endurance completed; remaining work
concerns final native desktop acceptance and packaging. The retained successful
large-file transfers use 115200; faster UART trials are not accepted defaults.
Raw RGB565 has real component throughput/transfer measurements but no complete
production raw-video playback result. Do not promote a component or host result
to a complete physical player benchmark.

## 4. Benchmark tables and measurement rules

Source FPS describes the MP4. Encoded FPS describes the prepared file. Target FPS
is the device's requested cap. Actual FPS below counts completed presentations,
including real elapsed playback time. Seek/redraw/probe frames do not count.

The original-media baseline is kept separate because its content, active geometry
and encoded rate differ from the optimization clip:

| Original configuration | Resolution | Format | Quality | Encoded FPS | Target FPS | Actual FPS | Frame fetch ms* | Decode ms | Render ms | Stage sum ms* |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Row, 20 MHz TFT | 320×180 | MVB/JPEG 4:2:0 | Existing; unknown qscale | 10 | 30 | 2.8593 | 40.570 | 88.413 | 193.912 | 322.895 |
| Native blocks, 20 MHz TFT | 320×180 | MVB/JPEG 4:2:0 | Same exact baseline file | 10 | 30 | 4.4718 | 38.989 | 87.907 | 76.054 | 202.950 |

*Baseline frame fetch includes SD, parsing, CRC and JPEG validation. The stage sum
is not the whole-run time per completed frame, which is given in section 2.

All following physical matrix rows use the **same separate** 320×240 qscale 8
bicubic baseline-4:2:0 MVB: **30 source FPS, 30 encoded FPS, 30 target FPS**, native
320×240 at X0/Y0 with zero rotation, 360 frames / 12 seconds, **3327948 bytes**,
SHA-256 `5cef59ed0bd6410d54d93932bd450402676b71fe563e9c8d50bbc47f89f1807e`.
The initial matrices used roughly 5–8-second windows. Later codec comparisons
cover the complete 12-second clip. Neither replaces extended endurance or a
physical observer's visual-quality rating. Older rows without GRAM verification
are historical transfer/presentation counters. In particular, their 80 MHz rows
are **not accepted clean-output FPS results** after later readback found corruption.

Initial `-O3` matrix, SD requested/observed **10/10 MHz**:

| Configuration | Resolution | Format | Qscale | Target FPS | Actual FPS | SD read ms | Decode ms | Render ms† | Total frame ms‡ |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Legacy 20 MHz LE, MCU1 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 5.2968 | 31.252 | 43.321 | 87.355 | 184.019 |
| Legacy 20 MHz LE, MCU4 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 6.1032 | 31.077 | 37.322 | 71.553 | 159.324 |
| Legacy 20 MHz LE, MCU8 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 6.2472 | 31.149 | 36.422 | 69.018 | 155.522 |
| Blocks 20 MHz LE, MCU1 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 6.1032 | 31.207 | 38.681 | 68.185 | 159.366 |
| Blocks 20 MHz LE, MCU4 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 6.3939 | 31.229 | 36.140 | 65.525 | 151.983 |
| Blocks 20 MHz LE, MCU8 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 6.4320 | 31.291 | 35.687 | 65.119 | 150.832 |
| Blocks 20 MHz BE, MCU1 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 6.0580 | 31.156 | 40.703 | 67.490 | 160.662 |
| Blocks 20 MHz BE, MCU4 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 6.3317 | 31.256 | 38.082 | 65.153 | 153.595 |
| Blocks 20 MHz BE, MCU8 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 6.3732 | 31.316 | 37.665 | 64.833 | 152.525 |
| Blocks 40 MHz BE, MCU1 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 7.5118 | 31.279 | 40.678 | 35.982 | 129.086 |
| Blocks 40 MHz BE, MCU4 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 7.9307 | 30.476 | 38.123 | 34.173 | 122.167 |
| Blocks 40 MHz BE, MCU8 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 7.9843 | 31.070 | 37.682 | 33.919 | 121.129 |
| Blocks 80 MHz BE, MCU1 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 8.5406 | 31.279 | 40.606 | 20.372 | 113.237 |
| Blocks 80 MHz BE, MCU4 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 9.0444 | 31.205 | 38.067 | 18.787 | 106.701 |
| Blocks 80 MHz BE, MCU8 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 9.1323 | 31.215 | 37.623 | 18.576 | 105.700 |
| DMA 40 MHz BE, MCU1 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 7.5194 | 31.281 | 52.928 | 22.259 | 128.840 |
| DMA 40 MHz BE, MCU4 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 9.2062 | 34.928 | 41.922 | 7.134 | 104.602 |
| DMA 40 MHz BE, MCU8 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 9.6896 | 31.215 | 39.828 | 9.819 | 99.377 |
| DMA 80 MHz BE, MCU1 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 7.5383 | 31.211 | 52.854 | 21.840 | 128.331 |
| DMA 80 MHz BE, MCU4 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 9.7938 | 30.998 | 41.840 | 6.846 | 98.585 |
| DMA 80 MHz BE, MCU8 | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 | 10.2433 | 31.071 | 40.005 | 4.834 | 94.319 |

Follow-up SD/CPU-path comparisons, all **BE / MCU8 / same 320×240 q8 content**:

| Configuration | SD requested/observed MHz | Actual FPS | SD read ms | SD seek ms | Decode ms | Render ms† | Total frame ms‡ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| O3, Blocks 40 MHz | 20/20 | 8.4708 | 26.984 | 11.798 | 37.634 | 33.915 | 114.273 |
| O3, Blocks 80 MHz | 20/20 | 9.7585 | 27.011 | 12.024 | 37.602 | 18.563 | 99.117 |
| O3, DMA 40 MHz | 20/20 | 9.0452 | 36.697 | 16.689 | 39.774 | 9.785 | 107.060 |
| O3, DMA 80 MHz | 20/20 | 11.0269 | 26.759 | 11.908 | 39.994 | 4.831 | 87.601 |
| O3, Blocks 40 MHz | 40/20 | 8.4669 | 26.981 | 11.830 | 37.617 | 33.916 | 114.284 |
| O3, Blocks 80 MHz | 40/20 | 9.7636 | 26.978 | 11.994 | 37.590 | 18.562 | 99.040 |
| O3, DMA 40 MHz | 40/20 | 10.4174 | 27.023 | 11.938 | 39.805 | 9.780 | 92.670 |
| O3, DMA 80 MHz | 40/20 | 10.9917 | 26.882 | 11.925 | 40.009 | 4.830 | 87.766 |
| Word/CRC/seek trial, Blocks 40 MHz | 20/20 | 8.4726 | 27.989 | 11.750 | 37.982 | 33.765 | 114.040 |
| Word/CRC/seek trial, Blocks 80 MHz | 20/20 | 9.8083 | 27.912 | 11.898 | 37.920 | 18.404 | 98.640 |
| Word/CRC/seek trial, DMA 40 MHz | 20/20 | 10.3589 | 28.056 | 11.902 | 39.807 | 10.170 | 93.262 |
| Word/CRC/seek trial, DMA 80 MHz | 20/20 | 10.8819 | 28.065 | 11.842 | 40.101 | 5.413 | 88.742 |

†Optimized render is instrumented TFT submission/wait wall time, excluding the
separately measured transform/callback overhead. DMA can continue while the CPU
decodes the next tile; this value is not the complete SPI wire-transfer duration.
Decode excludes display callbacks and JPEG open, and includes fused color conversion.
SD read/seek timings include stdio, driver operations and buffer copies; they do not
isolate electrical card latency from the software driver.

‡Total frame is measured around the complete frame-fetch/decode/render operation,
including parsing/CRC/validation/open/transform. Actual FPS uses the full run,
including between-frame scheduling and communication work. Copy and DMA waits are
nested display costs, so adding them again would double-count.

Exact physical evidence: SD10 clock/MCU matrix (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-o3-10mhz-retry.jsonl`),
SD20 matrix (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-o3-sd20.jsonl`),
requested-SD40 matrix (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-o3-sd40.jsonl`),
and word/CRC/seek trial (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-pipeline-2048-sd20.jsonl`).

The host-only format/size/quality table and exact commands are in
[FORMAT_AND_AUDIO_COMPARISON.md](FORMAT_AND_AUDIO_COMPARISON.md). For example, the
q8/Lanczos/30-FPS 320×240 MVB was 3419792 bytes; the raw RGB565 counterpart was
55296000 bytes. This Lanczos file is distinct from the physical bicubic clip above.
Raw source frames were generated directly, rather than by decoding an already
lossy JPEG. Host SSIM is a single-frame objective comparison and does not prove
perceived TFT quality or smoothness.

## 4a. Later SD, strip and decoder comparisons

All rows below retain the same physical 320×240/q8/bicubic/4:2:0 content,
30 source/encoded/target FPS and native geometry described above. Decode
excludes JPEG preparation and drawing but includes color conversion and any
adapter strip copy. SD seek is separate from SD read.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS / scope | SD read ms | SD seek ms | Decode ms | Render ms | Total frame ms |
| --- | ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 8 KB stdio, DMA80/MCU8 | 320×240 | MVB/JPEG / jpegdec | 8 | 30 | 11.3492 counter; unqualified | 17.381 | 16.960 | 42.106 | 5.851 | 84.879 |
| POSIX, DMA40/MCU8 | 320×240 | MVB/JPEG / jpegdec | 8 | 30 | 14.2651 counter; unqualified | 9.635 | 2.338 | 41.880 | 10.525 | 67.265 |
| POSIX strips, DMA40/MCU20 | 320×240 | MVB/JPEG / jpegdec | 8 | 30 | 16.4037 counter; unqualified | 9.736 | 1.922 | 38.638 | 5.306 | 58.088 |
| POSIX strips, DMA80/MCU20 | 320×240 | MVB/JPEG / jpegdec | 8 | 30 | 16.6972 counter; unqualified | 9.745 | 1.859 | 38.600 | 4.292 | 56.981 |
| ROM strips, DMA40 | 320×240 | MVB/JPEG / rom | 8 | 30 | 11.7804 counter; unqualified | 9.960 | 2.336 | 63.248 | 4.743 | 82.142 |
| Corrected clocks, JPEGDEC DMA40/cmd20 | 320×240 | MVB/JPEG / jpegdec | 8 | 30 | 16.5119 | 9.667 | 1.975 | 38.310 | 4.905 | 57.620 |
| Corrected clocks, JPEGDEC DMA40/cmd40 | 320×240 | MVB/JPEG / jpegdec | 8 | 30 | 16.5416 | 9.649 | 1.984 | 38.305 | 4.902 | 57.604 |
| Software FASTDECODE2, DMA40/cmd20 | 320×240 | MVB/JPEG / tjpgd_fast | 8 | 30 | 13.5119 | 8.790 | 2.338 | 53.907 | 4.129 | 71.116 |
| Espressif block codec, blocks40/cmd20 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 13.0284 | 8.776 | 2.167 | 27.283 | 33.647 | 73.945 |
| Espressif block codec, DMA40/cmd20 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 20.3679 | 8.982 | 1.150 | 27.563 | 6.881 | 46.631 |
| No-PSRAM profile, JPEGDEC DMA40/cmd20 | 320×240 | MVB/JPEG / jpegdec | 8 | 30 | 18.9545 | 9.643 | 1.445 | 31.429 | 5.057 | 49.938 |
| No-PSRAM profile, Espressif DMA40/cmd20 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 20.0428 | 9.699 | 1.277 | 27.361 | 6.959 | 47.317 |

The unqualified rows establish intermediate software timing improvements,
not a visually stable 80 MHz default. Accepted rows have matching observed
command/pixel clocks and zero mismatches in their sampled video pixels.
The best new-codec row checked 144 pixels across 16 frames; this is sparse
TFT-memory verification, not an entire-screen or endurance proof.

Evidence: buffered read (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-buffered-2048-sd20.jsonl`),
POSIX strips (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-posix-5120-sd20.jsonl`),
ROM (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-rom-posix-5120-sd20.jsonl`),
corrected clock matrix (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-split-driver-fixed.jsonl`),
software TJpgDec (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-tjpgd-fast40.jsonl`),
Espressif block codec (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-new-jpeg40.jsonl`).

The no-PSRAM JPEGDEC run completed 228 frames in 12.028820 seconds, with zero
135 sampled mismatches. The no-PSRAM NewJpeg run completed 241 frames in
12.024281 seconds, with zero 144 mismatches. Both used actual SD20 MHz. The
NewJpeg profile did not improve on the earlier codec sample; the JPEGDEC
improvement is useful measured evidence, without assigning every difference
to one compiler flag. Evidence:
JPEGDEC (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-no-psram-jpegdec.jsonl`),
NewJpeg (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-no-psram-new_jpeg.jsonl`).

Five further full-clip NewJpeg repeats at **20 requested FPS and actual SD10 MHz**
completed 222 frames each, achieving 18.4236–18.4374 FPS. Each checked 126 pixels
across 14 frames with zero mismatches; free heap stayed 48,408 bytes, largest block
30,708 bytes and minimum heap 43,232 bytes. Across these five samples, 16,650 DMA
submissions/completions matched, with zero DMA ownership errors or UART overruns.
One event-queue drop was present by the fourth sample. These approximately
60 seconds of active playback do not establish long-file or thermal endurance,
and the SD10 setting must not be compared as an equivalent SD20/target 30 run.
Evidence: five repeats (local-only evidence: `../.verification/session-2026-11-13/hardware/replay-new-jpeg-target20-no-psram.jsonl`).

### Compressed read-ahead and overlapping stages

The first complete read-ahead run preserved full 320×240/q8/bicubic content,
30 source/encoded/target FPS and actual TFT40/cmd20/SD20 clocks. It used the
verified no-PSRAM NewJpeg profile and a 2,000,000-baud application UART. The added
compressed slot was 9,776 bytes, with a 4,096-byte worker stack; no full-screen
RGB565 framebuffer was allocated.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | Background SD read ms | Background SD seek ms | Decode ms | Render ms | Foreground fetch wait ms | Foreground frame ms |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Compressed read-ahead, DMA40/cmd20/SD20 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 26.1697 | 15.366 | 0.303 | 27.416 | 6.539 | 1.157 | 36.049 |

The background SD/CRC/JPEG-validation operations overlap foreground decoding and
display. They are workload measurements, **not additive components of the
36.049-ms frame service time**. Foreground fetch wait reports the remaining
blocking cost. Whole-run time per completed presentation was 38.212 ms.
The first read and 28 future-index misses can still use synchronous fetch.

315 presentations completed in 12.036830 seconds, with 45 source frames dropped
and no rate-limited skips. Read-ahead reported 314 requests, 286 hits, 28 misses,
zero errors and 81.199 ms total join wait. Sparse video GRAM checks found zero
mismatches in 189 pixels across 21 frames. DMA submissions/completions matched
at 4,725, with no ownership errors or failure. There were no UART errors/overruns
or inbound/reply/event queue drops in the retained end diagnostics. Free heap
was 33,428 bytes, minimum 27,476 and largest block 26,612; minimum observed worker
stack free space was 2,772 bytes. This is a complete clip comparison, not extended
stability or visible-quality qualification.

Evidence: read-ahead run (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-prefetch-arduino-new-jpeg.jsonl`).
Implementation and deterministic ownership/control/cleanup tests are described in
[PREFETCH_DESIGN.md](PREFETCH_DESIGN.md). The subsequent same-build off/on and
SD-DMA comparisons are recorded immediately below; the entire difference from
older builds is not assigned solely to one feature.

### Same-build prefetch, DMA queue, SD-driver and entropy trials

The next comparison kept the original 320×240/q8/bicubic/4:2:0 clip and actual
40-MHz pixel / 20-MHz command clocks. Both first rows used the same DMA2 build at
115200 UART; this directly compares compressed prefetch off/on. DMA queue depth
two provides bounded buffer ownership, rather than an assumed full-frame buffer.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | SD read ms* | Decode ms | TFT submission/wait ms† | Foreground frame ms | Pixel corruption | Qualification duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Arduino SD20, DMA2, prefetch off | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 20.1756 | 9.704 | 27.904 | 6.072 | 47.044 | 0/144 sampled; 0/76800 separate full-frame check | 12.044 s |
| Same build, prefetch on | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 26.1378 | 15.235 | 27.885 | 5.876 | 36.053 | 0/180 sampled; 0/76800 separate full-frame check | 12.013 s |
| Adaptive loop yield, prefetch on | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 26.8837 | 15.479 | 27.893 | 5.692 | 35.592 | 0/189 sampled; 0/76800 separate full-frame check | 12.015 s |
| IDF SDSPI DMA SD20, prefetch off | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 18.8163 | 10.962 | 28.485 | 6.843 | 50.340 | 0/135 sampled | 12.064 s |
| IDF SDSPI DMA SD40, prefetch off | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 19.7108 | 9.040 | 28.484 | 6.829 | 48.111 | 0/135 sampled | 12.024 s |
| Optimized Huffman, adaptive DMA2/prefetch | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 27.1312 | 13.644 | 27.626 | 5.930 | 35.214 | 0/189 sampled; 0/76800 separate full-frame check | 12.016 s |

*Read time includes driver/buffer copying. With prefetch enabled it is mostly
background work and overlaps the foreground; add neither it nor background seek
to foreground decode/display/frame time. Seek was 1.289/0.199/0.244/1.778/1.511/
0.350 ms in row order. Remaining foreground fetch wait was 12.328/1.315/0.833/
14.149/11.950/0.748 ms. †TFT submission/wait is residual CPU wall time; the
full-screen wire transfer remains near 30.72 ms at actual 40 MHz and overlaps
block decoding. A complete-frame GRAM check is an uncounted subsequent rendering
probe, not verification of all frames in a measured playback run. These historical
rows are short comparisons; separate later optimized-path endurance is recorded
below rather than attributed retroactively to these exact configurations.

The same-build prefetch comparison raised actual FPS by 29.55%. Adaptive yielding
subsequently raised the short-window result by 2.85%, from 314 to 323 completed
presentations. The adaptive run completed all 4845 DMA submissions, with zero
pending transfers, zero ownership errors, aligned internal buffers and no DMA
failure. It ended with 33276 free heap bytes, 27348 minimum and 26612 largest block.
No per-frame application allocations were reported. The existing source clip has
30 source, encoded and requested FPS in all rows.

IDF SDSPI genuinely enabled SD DMA and reached an observed SD40 clock, but did not
beat the simpler Arduino SD20 path on this clip. The IDF SD40/prefetch attempt
returned `insufficient_memory` with "Synchronous playback remains available";
the failure was explicit and did not publish or erase media. A separate SD40
preparation attempt at UART921600 failed with three 3000-ms ACK waits and 232
connection-lifetime malformed frames. These failures are retained separately and
are not promoted to clean playback results or proof of card damage. The IDF SD40 sequential 153600-byte read probe
was 115.819 ms wall, versus 132.214 ms in the adaptive Arduino SD20 probe. This
component improvement alone did not improve complete JPEG playback.

Optimized Huffman encoding changes entropy tables while retaining baseline 4:2:0,
qscale 8 and resolution. Its file is 2932020 bytes, 395928 bytes (11.90%) smaller
than the 3327948-byte physical bicubic baseline. It uploaded completely over the
existing USB cable with the card installed: 9916.22 confirmed bytes/s,
295.679 s receiving, 19.889 s final verification, 315.925 s end to end, matching
size and SHA-256 `dcbaf2098e9a57a0ac508eb363e12f525b8f4b49f187707b4e6b984847a45356`.
The 326/12.015699-s playback result was 27.13117231 actual FPS. Host decoder
equivalence/compatibility checks and the active-decoder single-frame comparison
are recorded in section6. The known JPEGDEC fallback discrepancy remains disclosed.

Evidence: same-build off (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-arduino-dma2-uart115200-prefetch-off.jsonl`),
same-build on (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-arduino-dma2-uart115200-prefetch-on.jsonl`),
adaptive DMA2 (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-dma2-adaptive-prefetch.jsonl`),
IDF SD20 (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-idf-dma1-uart921600-sd20000000-off.jsonl`),
IDF SD40 (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-idf-sd40-repeat-diagnostics.jsonl`),
optimized entropy USB/playback (local-only evidence: `../.verification/session-2026-11-13/hardware/usb115-optimal-adaptive.jsonl`).

### First-block JPEG warmup and repeated independent command-clock tests

The guarded `MVB_JPEG_FIRST_BLOCK_WARMUP` experiment prepares the next resident
compressed frame's first RGB565 block while the current frame's final DMA strip
finishes. Current-frame `end_frame()` remains synchronous before its presentation
is counted. Warmup holds codec/output ownership until consumed or invalidated;
it never paints the next frame early. Cached header/block CPU time is credited
to the consuming frame. The nested lookahead timer is explicitly nonadditive.

Both first rows below use the same build, optimized q8/4:2:0 full 320×240 file,
SD20, TFT40 and command20. They directly compare warmup off/on. The earlier
`warmup-optimal-off.jsonl` trial used observed SD10 and is excluded from that
same-SD comparison. The six subsequent rows repeat only the independent command
clock at actual 10/20/40 MHz while keeping pixel data at actual 40 MHz.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | Background SD read ms* | JPEG decode ms | TFT submission/wait ms† | Foreground frame ms | Pixel corruption | Qualification duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Warmup off, cmd20 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 26.7398 | 13.735 | 28.357 | 5.642 | 35.745 | 0/189 samples; full check passed | 12.005 s |
| Warmup on, cmd20 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.8619 | 13.900 | 28.463 | 2.968 | 33.042 | 0/207 samples; full check passed | 12.023 s |
| Warmup on, cmd10, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.8657 | 13.908 | 28.463 | 2.973 | 33.039 | 0/207 samples; full check passed | 12.021 s |
| Warmup on, cmd20, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.8610 | 13.914 | 28.460 | 2.963 | 33.045 | 0/207 samples; full check passed | 12.023 s |
| Warmup on, cmd40, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.8570 | 13.906 | 28.461 | 2.963 | 33.041 | 0/207 samples; full check passed | 12.025 s |
| Warmup on, cmd10, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.8450 | 13.925 | 28.464 | 2.966 | 33.053 | 0/207 samples; full check passed | 12.030 s |
| Warmup on, cmd20, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.8510 | 13.929 | 28.464 | 2.969 | 33.063 | 0/207 samples; full check passed | 12.027 s |
| Warmup on, cmd40, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.8028 | 13.897 | 28.458 | 2.964 | 33.101 | 0/207 samples; full check passed | 12.013 s |

*Background read overlaps decode/display. †This is residual CPU submission/wait,
not the complete 30.72-ms full-screen pixel payload time. Full checks here each
read 76800 physical TFT pixels and found zero mismatches, as a subsequent uncounted
probe. Identical last-frame displayed CRC32 `526852766` was reported with warmup
off and on. These repeated clips are useful stability evidence but do not replace
long-duration optimal-path endurance.

Warmup on raised the direct same-build/SD comparison by 7.94%, from 321 to 347
completed presentations. The on run hit 332/346 warmup attempts, with 14 prediction
misses, 15 codec opens and no warmup/prefetch errors. All 5205 DMA submissions
completed; buffers remained 4-byte aligned with no ownership error or pending
transfer. End free heap was 35276 bytes, minimum 28844 and largest block 30708.
No decoder-private allocation size is guessed. The 878794-µs total lookahead CPU
work is nested and already credited to decode, rather than an extra additive cost.
Increasing command clocks provided no meaningful gain, so command20 remains the
candidate common setting. A 33.042-ms foreground frame operation still produces
only 28.8619 actual FPS because actual FPS also includes between-frame work.

Evidence: same-build warmup off (local-only evidence: `../.verification/session-2026-11-13/hardware/warmup-optimal-off-sd20.jsonl`),
warmup on (local-only evidence: `../.verification/session-2026-11-13/hardware/warmup-optimal-on-sd20.jsonl`),
two repeated command-clock matrices (local-only evidence: `../.verification/session-2026-11-13/hardware/warmup-command-clocks.jsonl`).
The lifecycle/timing regression design is in
[JPEG_FIRST_BLOCK_WARMUP.md](JPEG_FIRST_BLOCK_WARMUP.md). The two-block rejection,
five-minute optimized-path endurance and production guard comparison follow.

### Two-block warmup: tested and rejected as the default

The first-block experiment was extended to preserve two future RGB565 blocks.
This adds a 10240-byte owned cache at 320×240, with 20616 reported adapter bytes
and unknown private codec allocations. Three real-device runs used the same
q8/optimized full 320×240 file, TFT40/cmd20/SD20 and requested 30 FPS:

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | Background SD read ms* | JPEG decode ms | TFT submission/wait ms† | Foreground frame ms | Pixel corruption | Qualification duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Two cached blocks, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 27.7132 | 13.619 | 28.678 | 3.410 | 34.412 | 0/198 samples; 0/76800 full frame | 12.016 s |
| Two cached blocks, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 27.6260 | 13.599 | 28.679 | 3.395 | 34.519 | 0/198 samples; 0/76800 full frame | 12.018 s |
| Two cached blocks, repeat 3 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 27.7147 | 13.669 | 28.674 | 3.410 | 34.454 | 0/198 samples; 0/76800 full frame | 12.015 s |

*Read work overlaps foreground work. †TFT CPU submission/wait is distinct from
complete SPI payload time. Warmup hits were 305/302/304, with 27/29/28 prediction
misses and no warmup/prefetch errors. The last-frame CRC remained 526852766,
matching the single-block file's checked output. The owner described this visible
full-screen playback as clean and smooth. Free heap was 24972 bytes, minimum
18508–18588 and largest block 20468. The higher memory cost and lower actual FPS
make this a rejected default despite clean output. The single-block path remains
the production candidate. These three clips do not replace long-duration testing.

Evidence: three two-block trials (local-only evidence: `../.verification/session-2026-11-13/hardware/warmup2-full-optimal.jsonl`).

### Longer repeated playback in the two-block-capable build

One-block warmup was then selected in the newer two-block-capable firmware and
the full 320×240/q8/optimal clip was repeated ten times. All use TFT40/cmd20/SD20
and requested 30 FPS. This is 120.265415 seconds of actual playback, excluding
between-run commands, full-frame probes and restart gaps. It is repeated playback
of a 12-second file rather than one uninterrupted long file.

| Repeat | Actual FPS | SD read ms* | Decode ms | TFT submission/wait ms† | Foreground frame ms | Pixel corruption | Active seconds |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 1 | 28.1947 | 13.724 | 28.862 | 2.945 | 33.796 | 0/198 samples; full check passed | 12.023545 |
| 2 | 28.2029 | 13.750 | 28.861 | 2.950 | 33.811 | 0/198 samples; full check passed | 12.020025 |
| 3 | 28.2738 | 13.750 | 28.858 | 2.942 | 33.761 | 0/198 samples; full check passed | 12.025270 |
| 4 | 28.1836 | 13.739 | 28.862 | 2.946 | 33.809 | 0/198 samples; full check passed | 12.028261 |
| 5 | 28.1815 | 13.722 | 28.862 | 2.946 | 33.814 | 0/198 samples; full check passed | 12.029147 |
| 6 | 28.1886 | 13.726 | 28.858 | 2.947 | 33.813 | 0/198 samples; full check passed | 12.026155 |
| 7 | 28.1923 | 13.720 | 28.858 | 2.940 | 33.802 | 0/198 samples; full check passed | 12.024550 |
| 8 | 28.1783 | 13.719 | 28.858 | 2.946 | 33.801 | 0/198 samples; full check passed | 12.030553 |
| 9 | 28.1895 | 13.710 | 28.866 | 2.949 | 33.799 | 0/198 samples; full check passed | 12.025757 |
| 10 | 28.1745 | 13.734 | 28.860 | 2.951 | 33.814 | 0/198 samples; full check passed | 12.032152 |

*SD read overlaps foreground work. †TFT wall time is residual submission/wait,
not full payload transfer. Aggregate 3391 presentations/120.265415 seconds give
**28.19596972 actual FPS**. Weighted foreground service was 33.802 ms; background
SD 13.729 ms, decode 28.860 ms and residual TFT 2.946 ms. All 1980 sampled pixels and
768000 subsequent full-frame pixels matched. Free heap stayed 35228 bytes after
every run; minimum settled at 28764 bytes, with no DMA ownership errors. This is
stable within the measured repeated workload. It does not claim the older build's
short 28.8657-FPS peak as an extended result or establish a universal card limit.
Separate five-minute active optimized-path endurance and a later guard-one build
comparison follow; these ten runs are not relabelled as those other binaries.

Evidence: ten full-screen repeats (local-only evidence: `../.verification/session-2026-11-13/hardware/warmup2-build-full-oneblock-repeat.jsonl`).

### Near-30-FPS native 304×224 profile

A deliberately modest native-size reduction was tested rather than lowering JPEG
quality. The 304×224/q8/optimal file is centered at X8/Y8 with no runtime scaling
or rotation. Its 68096 pixels are 88.67% of the full 320×240 frame area; the TFT
remains 320×240 with an 8-pixel border. This is not full-screen 320×240 playback.
Source, encoded and requested rates are all 30 FPS.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | Background SD read ms* | JPEG decode ms | TFT submission/wait ms† | Foreground frame ms | Pixel corruption | Qualification duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Native centered, first-block warmup, DMA40/cmd20/SD20 | 304×224 | MVB/JPEG / new_jpeg | 8 | 30 | 29.9986 | 13.565 | 25.497 | 2.366 | 29.006 | 0/72 playback samples; 0/68096 entire-region check | 12.000550 s |

*Background read overlaps foreground work; remaining fetch wait was 0.106 ms.
†Residual CPU submission/wait is not full pixel wire time. All 360 frames were
presented, with zero dropped and zero rate-skipped frames. Exact actual FPS is
`360 / 12.000550 = 29.99862506`; this is approximately 30, rather than an inflated
source/target number. The separate full-region physical readback passed. Warmup
hit 358/359 attempts with one miss and no errors; all 5040 DMA submissions completed
with no pending/ownership/failure flags. End heap free/minimum/largest block was
36028/30960/30708 bytes, with no PSRAM. Its separate five-minute active endurance
is recorded below.

The 2764692-byte prepared file was sent through the same USB connection with the
card installed: 9893.72 confirmed bytes/s, 279.439 s receiving, 19.292 s verification,
299.093 s total. Accepted/checkpointed/verified size and SHA-256 all matched
`16bd370a8cd71485817ca97708302ed776baa5fa51f9d2e14af7f714a34b2480`.
This is an additional CLI/hardware transfer test; the earlier native desktop
acceptance retains its separate full-screen file and timings.

Evidence: native 304 USB/playback/integrity (local-only evidence: `../.verification/session-2026-11-13/hardware/usb-native304-warmup1.jsonl`).
Full-screen optimization continues independently; this reduced native profile's
extended verification is recorded below.

### Optimized five-minute active endurance at full and reduced native size

The optimized firmware with one-block warmup was tested for 25 repeated runs at
each resolution, all with actual 40-MHz pixels, 20-MHz commands, SD20 and requested
30 FPS. These are two separate approximately five-minute **active playback**
workloads using the 12-second prepared files. Between-run restart commands and
full-region readbacks are excluded from active FPS and duration; this is not one
uninterrupted ten-minute video.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Aggregate actual FPS | Background SD read ms* | JPEG decode ms | TFT submission/wait ms† | Foreground frame ms | Active duration / presentations | Pixel corruption |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Optimized one-block, full native | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.6286 | 13.984 | 28.207 | 3.273 | 33.255 | 300.608640 s / 8606 | 0/5004 playback samples; 0/1920000 full checks |
| Optimized one-block, centered native | 304×224 | MVB/JPEG / new_jpeg | 8 | 30 | 29.9649 | 13.755 | 25.323 | 2.616 | 29.145 | 300.151349 s / 8994 | 0/1782 playback samples; 0/1702400 full checks |

*Background read overlaps foreground decode/display; remaining fetch wait was
0.542/0.085 ms. †This is residual CPU submission/wait rather than total SPI wire
time. Stage means are weighted by completed presentations. The full-screen
aggregate is 8606/300.608640=28.62858499 FPS, with 394 dropped frames out of 9000
eligible and no rate-limited skips. Per-run FPS ranged 28.5921–28.6928. Native 304
is 8994/300.151349=29.96488282 FPS, with 6 dropped frames (0.067%) and no rate skips;
per-run range 29.5762–30.0000. Neither target 30 nor the near 30 numeric rounding is
substituted for the actual aggregate.

Every subsequent full-region check passed: 25 at each resolution. Free heap was
constant at 35172 bytes in every full-screen record and 36268 in every native 304
record; minimum heap 29052/30180 and largest block 30708. No sampled pixel mismatch
or full-check mismatch was recorded. There were no DMA ownership errors or
unconfirmed completion flags in the selected records. This demonstrates stable
memory/image integrity within these repeated workloads; it does not promise
unbounded uptime or every-frame pixel verification.

The full-screen tool was terminated with host exit 143 **after all 25 playback
records and all 25 complete-region checks had been saved**. Its completed device
data is reported, but the whole host invocation is not labelled exit 0/pass. The
native 304 runner completed with exit 0. The native session omitted source FPS;
its identical prepared SHA-256 matches the earlier verified 30-FPS source/prepared
artifact. Source FPS was not reconstructed from its achieved timing.

Evidence: full-screen endurance (local-only evidence: `../.verification/session-2026-11-13/hardware/optimized-full-endurance.jsonl`)
and native 304 endurance (local-only evidence: `../.verification/session-2026-11-13/hardware/optimized-native304-endurance.jsonl`).
The subsequent guard-one, pacing, direct-DMA and exact-image comparisons/endurance
are recorded below. Final 25-FPS native desktop acceptance and package verification
are reported separately.

### Final guard-one comparison and q10 area quality trial

Compiling the two-block experimental code out with the production guard retained
one-block warmup and produced five full 320×240/q8/optimal runs at 28.7043–28.8021
FPS. All 207 samples per run and all five 76800-pixel checks matched. Free heap
stayed 35208 bytes, minimum 29092. This is a separate one-minute active comparison;
the earlier five-minute optimized results are not silently assigned to this binary.

Lowering quality to qscale 10 with area scaling was then tested on the same full
320×240 region. The first selection attempt returned `invalid_container` with
SD errno 2 because the candidate was absent; that is not card corruption or a
playback result. The subsequent USB upload verified all 3152380 bytes and SHA-256
`6051c3ac96ba399adcf99f2b8e816eb6263a8c6c61f1f1fcb55fd89d69a1e22b`, at 9988.11
confirmed bytes/s, 315.613 s receiving, 18.726 s verification and 334.635 s total.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | Background SD read ms* | JPEG decode ms | TFT submission/wait ms† | Foreground frame ms | Pixel corruption | Active duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Area/default Huffman, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 10 | 30 | 28.6337 | 14.868 | 28.482 | 2.910 | 33.225 | 0/198 samples; 0/76800 complete frame | 12.013826 s |
| Area/default Huffman, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 10 | 30 | 28.6232 | 14.852 | 28.487 | 2.905 | 33.269 | 0/198 samples; 0/76800 complete frame | 12.018202 s |
| Area/default Huffman, repeat 3 | 320×240 | MVB/JPEG / new_jpeg | 10 | 30 | 28.5479 | 14.832 | 28.489 | 2.906 | 33.310 | 0/198 samples; 0/76800 complete frame | 12.014881 s |

*Background SD overlaps foreground. †TFT submission/wait is residual CPU time.
The quality reduction did not improve FPS over q8/optimal, so it is rejected as
the default. Shared-reference whole-video RGB metrics over 360 frames measured
q8/bicubic/optimal 26.095311 dB PSNR/0.692648 RGB SSIM, versus q10/area 25.802980/
0.677784. The objective quality loss is 0.292331 dB on this source; it is not a
physical perceived-quality rating and cannot be numerically compared with the
earlier single-frame YUV SSIM table. Huffman optimization alone preserves the
q10 decoded pixels on FFmpeg/TJpgDec; its 2727624-byte host candidate is not a new
physical USB/FPS success. Remaining pacing/zero-copy trials continue separately.

Evidence: guard-one comparison (local-only evidence: `../.verification/session-2026-11-13/hardware/singlecache-full-final-qualification.jsonl`),
q10 USB and three runs (local-only evidence: `../.verification/session-2026-11-13/hardware/usb-singlecache-q10area-final.jsonl`),
whole-video RGB quality (local-only evidence: `../.verification/session-2026-11-13/encoding-followup/wholevideo-RGB-quality.json`).

### Loop-pacing follow-up — qualification continued on 2026-11-14

The same full 320×240/q8/optimal file and TFT40/cmd20/SD20 path were compared with
busy-loop idle-yield intervals of 50, 250 and 1000 ms. This interval controls when
the busy main loop permits a one-tick idle sleep; it is not a per-frame delay.
Quiescent control-loop sleeps continue separately. These six 12-second runs are
two complete repetitions of the three settings in one firmware build.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | Background SD read ms* | JPEG decode ms | TFT submission/wait ms† | Foreground frame ms | Pixel corruption | Active duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Idle interval 50 ms, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.9117 | 13.961 | 28.086 | 3.306 | 32.952 | 0/207 samples; 0/76800 full check | 12.036654 s |
| Idle interval 250 ms, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.5182 | 14.068 | 28.082 | 3.290 | 32.727 | 0/207 samples; 0/76800 full check | 12.026481 s |
| Idle interval 1000 ms, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.6389 | 14.105 | 28.078 | 3.297 | 32.693 | 0/207 samples; 0/76800 full check | 12.011254 s |
| Idle interval 50 ms, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.7956 | 13.914 | 28.084 | 3.307 | 33.099 | 0/207 samples; 0/76800 full check | 12.015705 s |
| Idle interval 250 ms, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.5514 | 14.077 | 28.079 | 3.296 | 32.725 | 0/207 samples; 0/76800 full check | 12.012960 s |
| Idle interval 1000 ms, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.7173 | 14.120 | 28.079 | 3.295 | 32.654 | 0/207 samples; 0/76800 full check | 12.013199 s |

*Background SD work overlaps decoding/display. †TFT CPU submission/wait remains
distinct from the full 30.72 ms RGB565 pixel payload. At 50 ms the main loop recorded
174 busy sleeps per run, versus 44–45 at 250 ms and 11 at 1000 ms. All loop sleeps,
including quiescent intervals and telemetry/control work, occupied 403.505–500.749 ms,
267.091–268.311 ms and 234.578–317.637 ms respectively. These deltas surround the
whole benchmark and are not an isolated per-frame scheduler timer.

Every run completed all queued DMA transactions with zero ownership errors and
zero pixel mismatches in its recorded sample/full check. End heap stayed 35184
bytes, with minimum 28748. The best run presented 357/360 frames, three drops and
zero rate skips. Its whole-run presentation period is 33.650 ms, compared with
32.654 ms foreground work; the remaining approximately 0.996 ms is between-frame
work, including intentional media-PTS pacing/control/end handling. This is not an
isolated scheduler-overhead timer and does not prove
that the controller cannot approach 30 FPS. The one-second setting's separate
five-minute active qualification follows. Final exact-image endurance is reported
separately below; native control acceptance is recorded in section 11.

Evidence: same-build pacing matrix (local-only evidence: `../.verification/session-2026-11-13/hardware/loop-pacing-full.jsonl`).

### One-second idle interval: five-minute active endurance

The one-second setting repeated the same full 320×240/q8/optimal file 25 times at
30 requested FPS, actual TFT40/cmd20/SD20. All 25 playback records and 25 separate
full-region checks were saved; the host runner completed with exit 0.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Aggregate actual FPS | Background SD read ms* | JPEG decode ms | TFT submission/wait ms† | Foreground frame ms | Active duration / presentations | Pixel corruption |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Optimized one-block, one-second idle interval | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.6836 | 14.116 | 28.078 | 3.297 | 32.660 | 300.435340 s /8918 | 0/5175 playback samples; 0/1920000 full checks |

*SD work overlaps foreground decoding/display; remaining fetch wait 0.174 ms.
†TFT is residual CPU submission/wait; nested copy 0.881 ms and DMA wait 1.443 ms
are not additional stages. Stage means are weighted by presentations. Aggregate
8918/300.435340=29.68359182 FPS, 82 drops of 9000 eligible, zero rate skips;
individual runs 29.6313–29.7486 FPS. Full-region reads found zero mismatches in
1920000 physical pixels and sample checks zero of 5175. End heap stayed 35184
bytes after every run, minimum 28748. DMA ownership errors and unconfirmed-
completion flags remained zero.

This is five active minutes of repeated 12-second video with restart/readback
gaps, not one uninterrupted long file. It establishes measured image/memory
stability for this binary/setting and leaves approximately 0.3164 FPS below 30.
The newer direct-output experiment needs its own qualification before selecting
it. No 30-FPS success is claimed.

Evidence: one-second interval endurance (local-only evidence: `../.verification/session-2026-11-13/hardware/loop-1s-full-endurance.jsonl`).

### Direct SDK output strips, DMA ownership and clock follow-up

The guarded direct-output firmware was physically compared with its copied path
in the same build, using the same full 320×240/q8/optimal 30-FPS file, one-second
idle interval, actual TFT40/SD20 and one-block warmup. Three exclusive, 16-byte-
aligned SDK output strips replace the private codec strip and two staging strips.
A slot/token lease becomes peripheral ownership only after successful queueing;
exact FIFO completion makes it writable again. Current-frame end waits for DMA
while the third strip may retain the next frame's warmed output.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | Background SD read ms* | JPEG decode ms* | TFT submission/wait ms* | Foreground frame ms | Pixel corruption | Active duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Copied DMA, cmd20, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.6100 | 14.249 | 27.715 | 3.718 | 32.563 | 0/207 samples; 0/76800 full check | 12.022953 s |
| Direct output, cmd10, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8648 | 14.274 | 27.503 | 3.720 | 32.304 | 0/207 samples; 0/76800 full check | 12.020857 s |
| Direct output, cmd20, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8758 | 14.292 | 27.499 | 3.722 | 32.302 | 0/207 samples; 0/76800 full check | 12.016396 s |
| Direct output, cmd40, repeat 1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8706 | 14.313 | 27.499 | 3.711 | 32.297 | 0/207 samples; 0/76800 full check | 12.018514 s |
| Copied DMA, cmd20, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.5328 | 14.201 | 27.712 | 3.739 | 32.664 | 0/207 samples; 0/76800 full check | 12.020536 s |
| Direct output, cmd10, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8824 | 14.300 | 27.498 | 3.715 | 32.289 | 0/207 samples; 0/76800 full check | 12.013749 s |
| Direct output, cmd20, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8316 | 14.275 | 27.502 | 3.717 | 32.333 | 0/207 samples; 0/76800 full check | 12.000681 s |
| Direct output, cmd40, repeat 2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8860 | 14.288 | 27.503 | 3.715 | 32.295 | 0/207 samples; 0/76800 full check | 12.012296 s |

*SD is mostly background work. Display is residual CPU submission/wait, not the
full 30.72 ms wire payload. In the direct path, acquiring a codec output lease may
wait for DMA inside decoding; that wait is included in decode wall time and the
separate DMA-wait counter. Therefore total DMA wait is not necessarily entirely
nested in display submission time, and the timers must not be added again.

Copied staging took 0.873–0.874 ms/frame; direct staging copy is 0. Actual direct
submissions were 5370 or 5385 per run, with zero copied submissions; this proves
selection became active use. All queued transfers completed, zero ownership
errors/unconfirmed completion, and every sample/full check matched. The opaque
SDK accepted switching `io.outbuf` between blocks for these measured runs. The
owner reports clean, smooth direct-output video. All eight host runs/checks
completed with exit 0. These short trials do not replace direct-path endurance.

Direct end heap stayed 34588 bytes. Global minimum fell to 14036 bytes after
configuration/integrity work; it must not be described as the steady-state free
heap or ignored. Usable display storage is 30720 bytes with at most 45 alignment
bytes, 16-byte pointers and no PSRAM/full framebuffer. The best run presented
359 of 360 frames at 29.88604343 FPS. Independent command rates 10/20/40 did not
establish a meaningful gain. Investigation of initial frame preparation and
long-duration direct playback is recorded below; no exact30-FPS result is claimed.

Evidence: same-build direct/copy matrix (local-only evidence: `../.verification/session-2026-11-13/hardware/dma-direct-full-matrix.jsonl`)
and [ownership/API design](DMA_DIRECT_OUTPUT_EXPERIMENT.md).

### Direct SDK output: five-minute active endurance

The direct-output path at actual TFT40/cmd20/SD20, one-second idle interval and
one-block warmup repeated the full 320×240/q8/optimal 30-FPS file 25 times. The
first-record preload experiment was absent in this binary. All 25 playback
records and 25 subsequent full-region checks completed; host runner exit 0.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Aggregate actual FPS | Background SD read ms* | JPEG decode ms* | TFT submission/wait ms* | Foreground frame ms | Active duration / presentations | Pixel corruption |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Direct output, one-second idle interval | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8703 | 14.287 | 27.501 | 3.717 | 32.302 | 300.264769 s /8969 | 0/5184 playback samples; 0/1920000 full checks |

*Background SD and completion waits during codec output acquisition overlap
other stages. Decode may include lease waits; display is residual CPU submission.
Nested DMA wait 2.651 ms and zero staging copy are not additive. Remaining foreground
fetch wait 0.126 ms. The exact result is 8969/300.264769=29.87030423 FPS, 31 dropped
of 9000 frames, zero rate skips. Per-run FPS 29.8287–29.9588; the first run presented
all 360 frames at 29.95876176 FPS (12.016518 s), zero drops. This near 30 result is
reported numerically rather than rounded to an exact 30 claim.

Every sample/full check matched. All 134535 direct submissions completed with no
copied submissions, ownership errors, pending DMA or unconfirmed completion.
End heap was 34460 bytes in the first record, then 34588 in all 24 subsequent records;
minimum 24324 initially, then 14068 after full integrity/configuration work.
This is bounded settling followed by constant end heap, not a claim that every
record had the same heap or that private codec allocations never occur.

These are five active minutes of repeated12-second clips with restart/readback
gaps. They demonstrate measured stability for this direct binary/settings.
The subsequent first-record preparation experiment reports startup latency and
inclusive FPS separately. Its completed paired comparison below rejects it as
the default rather than hiding moved work.

Evidence: direct endurance (local-only evidence: `../.verification/session-2026-11-13/hardware/dma-direct-full-endurance.jsonl`)
and [first-record preparation design](FIRST_FRAME_PRELOAD_EXPERIMENT.md).

### First-record preparation: paired trial and rejected default

A guarded prototype reads/checks frame 0 before starting a fresh media clock,
then consumes that owned record exactly once. It does not paint pixels early,
change frame eligibility or suppress drops. Its separate startup latency and
`actual_fps_including_preparation` expose the work moved before the media anchor.
Preparation covers the ready/restart handler, excluding earlier full-file
hash/index checks and UART queue time. Three off/on pairs use one prototype
build, full 320×240/q8/optimal,30-source/encoded/target, TFT40/cmd20/SD20/direct.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | FPS including preparation | Preparation ms | Background SD read ms* | JPEG decode ms* | TFT submission/wait ms* | Foreground frame ms | Pixel corruption | Active duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Preload off, pair1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.5313 | 29.5313 | 0 | 14.111 | 27.865 | 3.331 | 32.607 | 0/207 samples; 0/76800 full check | 12.021141s |
| Preload on, pair1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 28.7070 | 28.6394 | 28.396 | 14.821 | 27.874 | 3.295 | 33.572 | 0/207 samples; 0/76800 full check | 12.017955s |
| Preload off, pair2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.4364 | 29.4364 | 0 | 14.058 | 27.864 | 3.339 | 32.705 | 0/207 samples; 0/76800 full check | 12.025940s |
| Preload on, pair2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.5182 | 29.4849 | 13.577 | 14.065 | 27.864 | 3.333 | 32.614 | 0/207 samples; 0/76800 full check | 12.026485s |
| Preload off, pair3 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.4465 | 29.4465 | 0 | 14.086 | 27.866 | 3.333 | 32.698 | 0/207 samples; 0/76800 full check | 12.021797s |
| Preload on, pair3 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.5323 | 29.4989 | 13.590 | 14.079 | 27.864 | 3.331 | 32.610 | 0/207 samples; 0/76800 full check | 12.020744s |

*Timers retain overlapping/nested scope. Preloaded read/CRC work is credited to
its consuming frame but occurred before playback service; do not add it again.
The three enabled runs consumed verified frame 0, but presented345/355/355 frames,
with 15/5/5 drops. Their inclusive FPS was 28.6394/29.4849/29.4989. No consistent
improvement over the paired off runs or previous direct firmware was measured.
All six complete-region checks passed; host invocation exit 0. This is a rejected
default, not an inflated30-FPS result from moving work outside a denominator.
The prototype's off-path difference from the older binary is not assigned to
one source change without an isolated proof.

Evidence: paired first-record trial (local-only evidence: `../.verification/session-2026-11-13/hardware/dma-preload-full-comparison.jsonl`)
and [explicit timing/accounting design](FIRST_FRAME_PRELOAD_EXPERIMENT.md).

### Production-like run without sampled-pixel reads

The same preload-prototype binary was tested with preloadfalse and the same
40-MHz pixels/20-MHz commands/direct path, but diagnostic playback sampling
turned off. Each measured run was followed by an uncounted full-region readback.
This evaluates probe overhead separately from a software decoder improvement.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | Background SD read ms* | JPEG decode ms* | TFT submission/wait ms* | Foreground frame ms | Pixel corruption | Active duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Sampling off, repeat1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.6896 | 14.153 | 27.859 | 3.246 | 32.446 | no playback samples; 0/76800 separate full check | 12.024408s |
| Sampling off, repeat2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.6198 | 14.084 | 27.861 | 3.250 | 32.521 | no playback samples; 0/76800 separate full check | 12.019006s |
| Sampling off, repeat3 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.6160 | 14.113 | 27.862 | 3.246 | 32.516 | no playback samples; 0/76800 separate full check | 12.020535s |

*Background SD and codec acquisition wait retain nonadditive scope. The three
runs presented357/356/356 frames, three/four/four drops. The same binary's
sampling-on/preload-off rows above ranged29.4364–29.5313 FPS; removing diagnostic
SPI reads improves this short result but does not reach30. It is not a new
five-minute endurance result or a same-binary comparison against the older
29.8703-FPS direct firmware. No zero-error playback sample count is invented
when the probe was disabled.

Evidence: sampling-disabled comparison (local-only evidence: `../.verification/session-2026-11-13/hardware/dma-preload-off-no-sampling.jsonl`).

### Automatic FAST defaults and input alignment before startup-clear optimization

Cold startup of the normal FAST image confirmed actual NewJPEG/direct3/
prefetch/one-block warmup at 40-MHz pixels,20-MHz commands,SD20 and one-second
idle interval, with no fallback. Fixed/current/prefetch inputs were 8mod16.
The aligned environment changes only fixed-record alignment; its actual measured
fixed/current/prefetch offsets were 0mod16. The prefetch allocator is unchanged.
Both images here precede the final startup border-only-clear change.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | Background SD read ms* | JPEG decode ms* | TFT submission/wait ms* | Foreground frame ms | Pixel corruption | Active duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Automatic FAST normal, repeat1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.9033 | 14.124 | 27.961 | 3.205 | 32.366 | no samples; 0/76800 separate full check | 12.005373s |
| Automatic FAST normal, repeat2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8707 | 14.127 | 27.960 | 3.207 | 32.407 | no samples; 0/76800 separate full check | 12.018486s |
| Automatic FAST normal, repeat3 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8313 | 14.106 | 27.956 | 3.210 | 32.450 | no samples; 0/76800 separate full check | 12.000833s |
| Automatic FAST aligned, repeat1 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.9669 | 14.197 | 27.870 | 3.236 | 32.318 | no samples; 0/76800 separate full check | 12.013249s |
| Automatic FAST aligned, repeat2 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8845 | 14.188 | 27.862 | 3.240 | 32.404 | no samples; 0/76800 separate full check | 12.012900s |
| Automatic FAST aligned, repeat3 | 320×240 | MVB/JPEG / new_jpeg | 8 | 30 | 29.8836 | 14.174 | 27.869 | 3.232 | 32.403 | no samples; 0/76800 separate full check | 12.013264s |

*Timers retain background/nested scope. Sampling was explicitly0; physical
checks are subsequent full-region reads, not invented zero-error samples.
Normal presented359/359/358 frames (one/one/two drops); aligned360/359/359
(zero/one/one drops). All six full checks passed; both host runners completed
with exit 0. The highest aligned run is 360/12.013249=29.96691403 FPS. The small
three-run difference is not a strong causal or universal alignment-speed claim.
Normal end heap was 34604 bytes, min 22804→12548 after checks; aligned34752 then
34880, min23080→12824. DMA output pointers remain16-byte aligned in both.

The selected final candidate adds startup region-only clear: full 320×240 performs
zero redundant clear pixels, and smaller/clipped videos clear only outside their
actual region. The first verified frame supplies video pixels. The media anchor
and completed-FPS accounting stay unchanged. This is distinct from the rejected
SD-preload prototype. Selected `cyd_optimized_input_aligned` exact-image endurance
is reported below; native USB25 acceptance is recorded in section 11.

Evidence: automatic normal (local-only evidence: `../.verification/session-2026-11-13/hardware/fast-normal-before-roi.jsonl`),
automatic aligned (local-only evidence: `../.verification/session-2026-11-13/hardware/fast-aligned-before-roi.jsonl`)
and [final default/clear behavior](QUALIFIED_FAST_DEFAULTS.md).

### Final selected image: automatic cold boot and five-minute active endurance

The final `cyd_optimized_input_aligned` binary is 665888 bytes with SHA-256
`3de1a18fdd0eb993c6fb53a311317ebe62e2b82b07ec634f030d21496483343f`.
It was rebuilt from the frozen source, flashed only at 0x10000, hash-verified and
reset. Cold-boot queries confirmed automatic NewJPEG/direct3/prefetch/warmup1,
big-endian 40-MHz pixels/20-MHz commands/SD20 and a watchdog-bounded one-second idle
interval. No research first-record preload is compiled into this selected image.

| Configuration | Resolution | Format | Quality | Source / encoded / target FPS | Actual FPS | SD read ms* | JPEG decode ms* | TFT submission/wait ms* | Foreground frame ms* | Active duration / presentations | Pixel integrity |
| --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Final automatic direct3/aligned/border-only startup | 320×240 | MVB/JPEG4:2:0, optimal Huffman | 8 | 30 /30 /30 | 29.7992 | 14.120 | 27.624 | 3.630 | 32.471 | 300.376844s /8951 | 0/5175 samples; 0/1920000 full-check pixels |

*SD wall time mostly overlaps foreground processing. JPEG decode includes possible
direct-strip lease waits and fused color conversion. TFT time is CPU submission/
wait, not the entire concurrent SPI payload duration. The measured whole-run
period is 33.557909 ms per presentation; it includes media pacing and between-frame
work. These overlapping stage counters are not an additive pipeline sum.

All 25 twelve-second clips and 25 independent full-region checks completed; host
runner exit 0. Aggregate 8951/300.376844=29.79923446 FPS, 49 dropped of 9000 eligible
frames (0.5444%), zero rate skips. Individual runs were 29.77642562–29.87305201 FPS.
The entire 1920000-pixel count is 25 separate checked last frames, not every pixel
of all 8951 video frames. Readback/restart gaps separate the clips: this is five
active minutes rather than uninterrupted long-video playback.

DMA reports three aligned strips, queue depth 2, direct submissions with zero copied
submissions, no ownership errors, no pending work and no unconfirmed-completion
or failure flags. End heap remains 34880 bytes in every run; largest block 22516,
minimum 12824 after controls/checks. There is no end-heap downward trend within this
workload. Telemetry costs 1925.942 ms across the run (0.6412%). End serial counters
retain one cumulative UART overrun and two invalid frames, with zero inbound/reply
queue drops or SD errno; asynchronous telemetry-event drops are 20. Those counters
are not invented as zero and do not invalidate the completed integrity checks.

Weighted additional costs are SD seek 0.213 ms, container preparation 0.089 ms,
CRC 0.518 ms, JPEG-profile validation 1.541 ms, codec prepare 0.399 ms, transform 0.185 ms
and remaining foreground fetch wait 0.154 ms. Both application strip-copy counters
are zero. The 2.612 ms DMA-wait counter is nonadditive because direct-output lease
waits may occur inside decode as well as display.

This final exact image is slightly slower than the earlier direct research image's
29.8703 FPS endurance. A three-run pre-ROI alignment comparison alone does not prove
why. The final automatic path is selected for reproducible startup, guarded
fallbacks and measured integrity rather than assigning the older maximum to it.

Evidence: exact-image summary (local-only evidence: `../.verification/session-2026-11-13/hardware/release-qualified-endurance-summary.json`),
all 25 records/checks (local-only evidence: `../.verification/session-2026-11-13/hardware/release-qualified-endurance.jsonl`),
build/source hashes (local-only evidence: `../.verification/session-2026-11-13/hardware/cyd-release-qualified.build.json`).
Portable selected records are included in [playback-current-matrix.json](evidence/playback-current-matrix.json).
The shareable [final-firmware-playback-endurance.json](evidence/final-firmware-playback-endurance.json)
includes the exact firmware hash, verified counts, timing scopes and host exit 0.

### Default 16:9 padding, video-only USB and short physical playback

A 640×360/30 FPS H.264 source with AAC audio was converted through the production
default to 320×184, 25 encoded FPS, qscale 8/bicubic/4:2:0/optimal: 75 frames over 3 s.
The active 320×180 image remains intact with two black rows above/below. The
472216-byte MVB was sent over 115200-baud USB with the SD installed, verified at
the exact host/device SHA-256
`bda458f2e02b731b4b1f0d75765de7809232e00e219337ad262cb3d1b51ddc5e`, then played.
Confirmed throughput was 9938.23 bytes/s; receive 47.515 s, verify 3.627 s, total 51.452 s.

| Configuration | Resolution | Format / decoder | Qscale | Target FPS | Actual FPS | Background SD read ms* | JPEG decode ms | TFT submission/wait ms† | Foreground frame ms | Pixel corruption | Active duration |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Default padded 16:9, DMA40/cmd20/SD20 | 320×184 | MVB/JPEG / new_jpeg | 8 | 25 | 24.9950 | 11.476 | 21.902 | 2.197 | 25.266 | 0/15 samples; 0/58880 complete-region pixels | 3.000597 s |

*Background reading overlaps foreground work. †TFT is residual submission/wait.
All 75 frames were presented, zero drops/skips; the physical region was X0/Y28.
End heap was 36848 bytes, minimum 28812. This is a short physical compatibility
and video-only transfer/playback result, not a padded-video endurance run or a
native GUI acceptance test. Portrait padding remains host-only.

The AAC-bearing original source retained its exact SHA-256. A separate temporary
video-only remux produced byte-identical MVB output. The converter already selected
only video and disabled audio; storage/transfer saving against the prior converter
is 0 bytes. No source audio was removed, and no FPS gain is attributed to an audio
removal that had already been implemented.

Evidence: physical padded USB/playback (local-only evidence: `../.verification/session-2026-11-13/hardware/usb-padded184-final.jsonl`)
and host source/audio proof (local-only evidence: `../.verification/session-2026-11-13/padded-physical/host-verification.json`).

### Rejected output and erroneous clock experiments

The first split-clock implementation left the SPI host gated when returning
from ESP-IDF DMA to the cached Arduino SPIClass path. Later polled block
rows reported implausible approximately 3 ms transfer time but read back
54 incorrect pixels out of 54 sampled pixels. These are failed experiments,
not 17.7 FPS clean playback. DMA also inherited the command divider instead
of the requested pixel divider: a requested 80/cmd40 run actually used 40 MHz.
Its 16.4914 counter FPS cannot be called 80 MHz performance.

The correction reacquires/reinitializes the polled SPI host after DMA release
and applies the pixel clock through the driver transaction. The complete
corrected matrix has 12 rows:40 MHz passes sampled video and color-stripe
GRAM checks; real 80 MHz fails at command 10,20,40 and 80 MHz in both polled
and DMA paths. The rejected values below remain visible as raw counters.

| Rejected real 80 MHz configuration | Counter FPS (excluded) | Video mismatches / samples | RGB stripe GRAM check |
| --- | ---: | ---: | --- |
| blocks, command 10 MHz | 13.8155 | 26 / 45 | Failed |
| dma, command 10 MHz | 16.6378 | 49 / 54 | Failed |
| blocks, command 20 MHz | 13.8497 | 22 / 45 | Failed |
| dma, command 20 MHz | 16.6127 | 49 / 54 | Failed |
| blocks, command 40 MHz | 13.8117 | 37 / 45 | Failed |
| dma, command 40 MHz | 16.6499 | 48 / 54 | Failed |
| blocks, command 80 MHz | 13.7941 | 32 / 45 | Failed |
| dma, command 80 MHz | 16.6185 | 47 / 54 | Failed |

The pre-correction matrix is retained separately:
matrix-split-jpegdec.jsonl (local-only evidence: `../.verification/session-2026-11-13/hardware/matrix-split-jpegdec.jsonl`).
It is excluded from final winner selection. No oscilloscope/electrical pin
measurement or physical observer rating is inferred from these registers
and sparse GRAM checks.

## 5. Best-performing configurations and selected image

The **final selected full 320×240 image sustained 29.79923446 actual FPS**: 8951
presentations /300.376844 active seconds, with 49 drops. All 5175 playback samples
and 1920000 subsequent full-check pixels match. It uses full 320×240/q8/bicubic/
4:2:0/optimized-Huffman content, Espressif `new_jpeg` full-width output,
compressed read-ahead, one-block warmup and three exclusive 16-byte-aligned DMA
strips with a two-transfer queue. Actual pixels/commands/SD clocks are 40/20/20 MHz.
The initial idle interval is watchdog-bounded to one second. Automatic startup,
border-only clear and fallbacks are selected in `cyd_optimized_input_aligned`.

The **highest completed full-screen five-minute research result is 29.87030423 FPS**:
8969 presentations /300.264769 seconds, with 31 drops, from the earlier direct-output
binary. Its first run presented all 360 frames at 29.95876176 FPS. Eight same-build
copied/direct trials show that direct output removes staging copies safely;
six direct runs reach 29.8316–29.8860 FPS with clean checks. The highest clean
pre-ROI full-screen single run is 360/12.013249=29.96691403 FPS, with zero drops.
Those older results are kept with their actual images, rather than attributed
to the final release hash.

The earlier copied path sustained 29.68359182 FPS: 8918 presentations /300.435340
seconds. All 5175 playback samples and 1920000 full-check pixels matched, with
constant end heap. First-record preparation was measured and rejected.

Native 304×224 sustained **29.96488282 actual FPS** over 300.151349 active seconds:
8994 of 9000 frames. Its best 12-second run presented every frame at 29.99862506 FPS.
It reduces active area by 11.33% and is not full-screen playback.

The original exact 3327948-byte JPEG container reached 26.88368263 actual FPS
without reducing quality or changing its payloads. Optimized-Huffman q8 retains
resolution/quantization and saves 11.90% bytes; host FFmpeg/TJpgDec whole-clip
comparisons and the active Espressif decoder's single checked last frame are
qualified separately below.

The selected release artifact is 665888 bytes, SHA-256
`3de1a18fdd0eb993c6fb53a311317ebe62e2b82b07ec634f030d21496483343f`,
built as `cyd_optimized_input_aligned`. Its application-only flash/hash/reset
completed before the successful exact-image endurance. Pre-ROI hashes and earlier
endurance remain separate evidence.
Native desktop USB25 transfer/integrity/automatic playback passed on this image;
the exact workflow and subsequent controls are recorded in section 11.
Final dated archive results and digests are recorded in the external
Muse-Windows-2026-11-14-verification.json (local-only evidence: `../dist-packages/Muse-Windows-2026-11-14-verification.json`)
and checksum file (local-only evidence: `../dist-packages/Muse-Windows-2026-11-14-SHA256SUMS.txt`).
Actual 80-MHz output remains rejected after corruption. The owner reports clean,
smooth tested 40-MHz video; sampled/last-frame readback does not verify every pixel
for unlimited uptime.

## 6. Best quality/performance configuration and default

The exact-payload comparison preserves the same full 320×240 active area
and exact q8 JPEG payloads at 26.8837 FPS. Its gain does not come from lower source
resolution or lower JPEG quality. Espressif full-width block output, compressed
read-ahead, adaptive yielding and genuine DMA at 40 MHz preserve that exact content.
With first-block warmup and later pacing, optimized entropy full-screen q8 reaches
29.8703 actual FPS with direct output over five active minutes, without lowering
resolution/qscale. The earlier copied path sustained 29.6836 FPS and its older
optimized predecessor28.63 FPS;
native 304×224 sustained 29.9649 FPS while retaining q8 and using less active area.
Lower-quality q10/area did not improve FPS and is rejected as the default.
Actual 80 MHz is excluded because it corrupts sampled output.

The production converter's first-launch default is now **25 encoded FPS, qscale 8,
up to 320×240, baseline 4:2:0 and optimized Huffman tables**. Existing saved FPS,
language and theme choices remain unchanged; an existing 10-FPS/English/dark
configuration is explicitly covered. The real-FFmpeg integration test verifies
default 25 and explicit 30 choices, shorter optimized entropy and identical decoded
RGB versus default Huffman at the same requested conversion rate. A full 25-FPS
artifact prepared through the native desktop picker contains 300 frames/2444120
bytes, verified on the host; its final physical native transfer is reported
separately in section 11.

FFmpeg decoded default/optimized streams have identical RGB hashes over 82944000
bytes, and actual TJpgDec decoded 360 paired frames identically. The JPEGDEC fallback
differs at 51253/27648000 pixels (0.1854%), maximum 8 RGB565 steps. Active Espressif
codec last-frame equivalence was checked separately on the actual device: the
original default-Huffman `5cef59ed…` file and optimized `dcbaf209…` file both
produced displayed RGB565 CRC32 **526852766**, with a complete 76800-pixel region
check passing. This verifies that one corresponding last frame with the active
Espressif decoder matches. It is not a physical pixel comparison of all 360 frames.
The JPEGDEC compatibility limit is disclosed rather than calling every decoder
bit-exact. Evidence: original-frame equivalence (local-only evidence: `../.verification/session-2026-11-13/hardware/warmup2-build-baseline-equivalence.jsonl`).
Host production/default/audio evidence is summarized in
[production-encoding-verification.json](evidence/production-encoding-verification.json).

Default contain conversion now pads prepared 4:2:0 frames to multiples of eight
when both requested bounds are divisible by eight. Padding follows aspect-preserved
scaling and leaves every active source pixel intact. A 320×180 image becomes
320×184 with two black rows above/below; a 180×240 portrait becomes 184×240 with
two black columns left/right. Chroma-safe even offsets can shift centering by at
most one pixel in other cases. Explicit nonaligned bounds and crop requests remain
unchanged. Nine raw-RGB oracle cases checked every content/border pixel before JPEG
encoding. The full 320×240 default artifact's SHA remains 9c19f630…; padding does
not change that file. Padded16:9 output is now physically USB-verified and played
all 75 frames at 24.9950 actual FPS with target 25, with clean sample/full-region
checks. Portrait padding is host-verified only.

Automatic fast boot/media settings and legacy-geometry/allocation fallbacks are
implemented and host-tested. The fast path was physically confirmed on the padded
25-FPS artifact; the final guard-one build was separately compared above.
The selected production environment is `cyd_optimized_input_aligned`. It starts
automatically with NewJPEG/direct3/prefetch/one-block warmup/40-MHz pixels/
20-MHz commands/SD20 and a watchdog-bounded one-second idle interval. Allocation
fallbacks retain copied2→polling blocks or JPEGDEC/synchronous playback. Preflight
avoids the redundant private RGB strip. Startup clears only outside the video at
the existing media anchor; no early presentation or FPS denominator change.
The first-record SD-preload option is rejected and absent from production flags.
The exact selected image passed its separate five-active-minute endurance at
29.7992 FPS with clean sample/full-region checks and constant end heap.
Native USB25 transfer, integrity and automatic playback passed. Final archive build/checksum status is
recorded in the external verification file linked in section 5.
Earlier four five-minute active workloads remain associated with their actual binaries.
Selecting 30 encoded/requested FPS remains distinct from achieving 30 actual FPS.
For normal playback, the measured default is full320×240/q8 at25 encoded/target
FPS. Selecting30 uses the same quality and native fast path and reaches29.7992
actual FPS on the final image. The separately tested304×224/q8 research option
approaches30 with an11.33% smaller active region; it is not an additional advertised
production profile. Lower-quality q10/area is not recommended because it reduced
objective quality without a measured throughput gain.

## 7. Whether 30 actual FPS was reached

**Full 320×240 has not reached exact 30 actual FPS.** Its highest clean short result
is 29.96691403 FPS, 360/12.013249 seconds, zero drops, from 30-source/30-encoded/
30-target content on the aligned pre-ROI image. The final selected image sustained
29.79923446 FPS over five active minutes; the earlier direct research image
sustained 29.87030423 FPS over five active minutes.
The copied one-second-interval path sustained 29.68359182 FPS, with 32.660 ms
weighted foreground work versus 33.688 ms whole-run period. Actual FPS includes
between-frame work. The earlier path
sustained 28.6286 FPS over five
active minutes. Corrupted80-MHz output and the10-encoded-FPS baseline do not count.

**Native 304×224 presented all 30-FPS source frames:** 360 completed presentations
in 12.000550 seconds, exactly 29.99862506 FPS, with no dropped/rate-skipped frames.
Its separate five-minute active result is 8994/300.151349=29.96488282 FPS, six drops
out of 9000 frames. This is approximately 30 actual FPS on the smaller native region.
It is not relabelled as full 320×240 or rounded into an unsupported exact 30 claim.

This is not a conclusion that30 FPS is physically impossible. Full 320×240 has
presented every source frame in several near30 individual runs. The final
aligned-ROI binary completed its own endurance with 49 drops out of 9000 frames.
Actual 80-MHz TFT transfers corrupt sampled pixels;40-MHz full-screen RGB565 has
a 30.72 ms payload floor, leaving about 2.61 ms in a 33.333 ms frame budget for any
processing that cannot overlap pixel transmission. Current software still has
measured transfer/processing costs, scheduling jitter and startup work. Direct
output has already removed staging copies. Whole-run period minus foreground
service includes intentional media-PTS pacing, control/telemetry and end handling;
it is not an isolated wasted-CPU or scheduler measurement.

The realistic decoder, strip ownership, batching, read-ahead, independent clock,
quality/chroma/resolution, SD-driver and startup experiments are documented above.
They demonstrate a stable near30 implementation on this unit. Following the final
exact-image result, the owner accepted 29.80 FPS and explicitly asked to stop
performance experiments. The work therefore ends at that accepted stable result;
it does not claim that full-screen 30 FPS is physically impossible.

## 8. Measured limiting factors, RAM and physical qualifications

ILI9341 readback returned **0x9341**. TFT diagnostics report the actual HSPI clock
divider at 20/40/80 MHz and continuous transactions; DMA initialization/activity
and completion failure are explicitly reported. Pin assignments describe the
configured buses, rather than a claimed independent electrical trace. Clock-divider
readback does not replace oscilloscope measurement or physical color/scan observation.

Before read-ahead, the sampled-pixel-valid 20.3679-FPS Espressif block /
DMA40/cmd20 sample had these independently instrumented costs:

| Stage | Mean ms per completed frame | Scope |
| --- | ---: | --- |
| SD read | 8.982 | Checked POSIX read, including driver and buffer copies |
| SD seek | 1.150 | Checked lseek/cache/driver work |
| Container preparation | 0.104 | Remaining frame preparation |
| CRC | 0.586 | Checked stored frame CRC |
| JPEG structure validation | 0.706 | Container JPEG-profile check |
| JPEG prepare | 0.483 | Block decoder header preparation |
| JPEG decode/color conversion | 27.563 | Display calls excluded; codec conversion included |
| Transform/callback overhead | 0.085 | Native-size/zero-rotation path |
| TFT submission/wait | 6.881 | Residual CPU wall time while DMA overlaps block decoding |
| Copy, nested in TFT | 0.875 | Reusable DMA staging; not an extra stage |
| DMA wait, nested in TFT | 4.812 | Checked completion waits; nested in display |
| Complete frame operation | 46.631 | Enclosing fetch/decode/render operation |

Telemetry consumed 73.028 ms over 12.028712 seconds, about 0.61% of the run.
This does not explain the remaining per-frame read/decode costs. Generic serial
service and codec allocator internals do not have isolated cycle timers; unknown
costs are not fabricated as zero. Steady-state adapter storage is reusable;
five-minute active endurance below held end heap constant. Private codec allocation
sizes and allocator timing remain unknown and are not reported as zero.

After read-ahead, the earlier 26.1697-FPS sample reports:

| Stage | Mean ms per completed frame | Scope |
| --- | ---: | --- |
| SD read | 15.366 | Mostly background worker; overlaps foreground work |
| SD seek | 0.303 | Mostly background worker; overlaps foreground work |
| Container preparation | 0.056 | Checked record preparation, including worker work |
| CRC | 0.580 | Stored frame CRC, including worker work |
| JPEG structure validation | 1.505 | Checked profile validation, including worker work |
| Foreground fetch wait | 1.157 | Remaining blocking read/join cost |
| JPEG prepare | 0.399 | Foreground codec header preparation |
| JPEG decode/color conversion | 27.416 | Foreground CPU decode; display calls excluded |
| Transform/callback overhead | 0.191 | Native-size/zero-rotation path |
| TFT submission/wait | 6.539 | Foreground residual while DMA overlaps decode |
| Copy, nested in TFT | 0.867 | Reusable DMA staging; not an extra stage |
| DMA wait, nested in TFT | 4.701 | Completion waits; not an extra stage |
| Complete foreground frame operation | 36.049 | Enclosing main-thread frame service |

Background fetch total was 5.879455 seconds over the run. These background
read/seek/validation timers cannot be summed with foreground decode/display to
predict frame throughput. Telemetry used 97.889 ms over 12.036830 seconds, about
0.81%. Read-ahead reduced blocking foreground work while background read wall
time increased under concurrency; the report retains both observations.

Independent physical component probes are kept separate from video FPS:

| Probe | Physical result | Qualification |
| --- | ---: | --- |
| Existing-file 153600-byte sequential read, SD10 | 516.790–517.039 ms; about 0.297 MB/s | SD stdio/driver path, not raw-video playback |
| Same read, SD20 | 446.735–446.768 ms; about 0.344 MB/s | Same original buffering; no complete raw frame pipeline |
| Same read, requested SD40 / observed20 | 446.732–446.813 ms | Confirms no achieved 40 MHz clock gain |
| Same read, word/CRC/seek trial SD20 | 464.467–464.508 ms | Slower read sample retained |
| Same read, 8 KB stdio SD20 | 133.953–134.007 ms wall | Real 3.3× read improvement; frame seek/refill still expensive |
| Same read, POSIX SD20 | about 131.2–131.5 ms wall | About 1.17 MB/s, not raw-video FPS |
| Same read, corrected split-clock build | 131.892–132.080 ms wall | Actual SD20; new render path does not remove raw-sized SD bandwidth cost |
| Same read, physical IDF SDSPI DMA SD40 | 115.819 ms wall | Actual SD40/DMA, still a component probe; complete JPEG playback slower than Arduino SD20 |
| JPEG decode-only, original O3 SD10 matrix | 36.728–38.826 ms | Repeated resident JPEG, no SD/TFT; includes color conversion |
| JPEG decode-only, SD20 matrix | 38.431–38.726 ms | Different sampled frame can affect decode cost |
| JPEG decode-only, word/CRC/seek trial | 38.764–39.094 ms | No measured decoder speedup |
| Full-screen RGB565 stripe TFT legacy20 | 66.680 ms display; 69.956 ms wall | Synthetic stripe probe, 240 pixel pushes |
| Stripe TFT blocks20 BE | 64.702 ms display; 68.079 ms wall | 40 pixel pushes |
| Stripe TFT blocks40 BE | 33.799 ms display; 37.177 ms wall | 40 pixel pushes |
| Stripe TFT blocks80 BE | 18.485 ms display; 21.858 ms wall | 40 pixel pushes |
| Stripe TFT DMA40 BE | 31.002 ms display; 34.396 ms wall | 0.890 ms nested copy; genuine DMA completion |
| Stripe TFT DMA80 BE | 15.613 ms display; 18.986 ms wall | 0.894 ms nested copy; genuine DMA completion |

The measured raw-sized read would consume far more than a 33.333 ms frame budget
with the original driver/buffering, even without JPEG decode. This proves a current
**software-path** limitation for that probe, not an immutable card bandwidth limit
or a completed raw-video benchmark. Installed-source/compiled-library inspection
identified Newlib's 128-byte stdio buffer, repeated refill/copy work and FatFS SD
CMD13 validation. Arduino FS ordinarily selects 4096 bytes. The reusable 8 KB
read-only buffer removed much of the bulk-read delay, and POSIX read/lseek removed
most frame seek/refill overhead. A 153600-byte read still takes about 131 ms;
a completed raw-video player is not claimed from that component probe. Details and evidence are in
[SD_DRIVER_INVESTIGATION.md](SD_DRIVER_INVESTIGATION.md).

Baseline runtime reported no PSRAM, about 100648 free internal-heap bytes and an
81908-byte largest free block. A full 320×240 RGB565 buffer needs 153600 contiguous
bytes, exceeding that measured largest block; two full buffers would need 307200
bytes. DMA tile buffers are much smaller and allocation can fail safely. Static
linker RAM is not runtime available RAM. Source inspection confirms reusable
compressed/index/tile storage, no unnecessary full-frame copy, and a native-size
fast path. Earlier copied adapters incurred approximately 1 ms of DMA tile copying;
both application strip-copy counters are zero in the final direct-output path.

At 20/40/80 MHz, full-screen RGB565 payload calculated floors are 61.44/30.72/15.36
ms, close to the separate stripe measurements once command/copy overhead is added.
Raw 320×240 at 30 FPS requires **4608000 SD payload bytes/s**, about 13.4 times the
original measured SD20 probe throughput. These arithmetic bounds and probes justify
further driver/format investigation, rather than replacing it.

The earlier five-minute optimized full-screen path averaged 33.255 ms foreground
work versus 34.930 ms whole-run period. One-second pacing endurance reduced those
to 32.660/33.688 ms, with 28.078 ms decode/color and 3.297 ms residual TFT work.
Its 0.881 ms staging copy and 1.443 ms DMA wait are nested display costs. SD read 14.116 ms
is mostly background work, with 0.174 ms remaining foreground fetch wait. These
measurements distinguish CPU, transfer and scheduling limits without adding
overlapped timers together. Direct-DMA ownership experiments aim to eliminate
staging copies safely; completed short/endurance results are above. The direct
path averages32.302 ms foreground work and 33.478 ms whole-run presentation period,
with 14.287 ms background SD,27.501 ms decode (including possible lease waits),
3.717 ms residual display and zero staging copy. Its2.651 ms DMA-wait counter is
nonadditive. The whole-run gap includes intentional PTS pacing, not solely
avoidable CPU work.

The final exact image averages 32.471 ms foreground work and 33.557909 ms whole-run
period, with 27.624 ms JPEG decode, 3.630 ms residual TFT submission/wait and zero
staging/decoder strip copies. Background SD read is 14.120 ms; remaining foreground
fetch wait is 0.154 ms. DMA wait is 2.612 ms and nonadditive. The approximately
1.087 ms period/service difference includes intentional media pacing and between-frame
work; it is not a proven removable scheduler cost. Current 40-MHz image/heap
stability has five separate repeated endurance workloads;
actual 80 MHz is rejected. The owner reports clean, smooth video; progressive scan
and perceived quality remain observer evidence, not native app screenshots.

## 9. Original USB transfer failure

The original owner's historical attempts did not have a recoverable complete error
log. This session reproduced the intended workflow on the real native desktop:
connection initially failed before HELLO; after reset-line correction, uploading a
different prepared video failed at `begin_upload` with **`upload_conflict`**.
The user-visible error was captured, not inferred from a unit test.

Read-only recovery inspection found a valid legacy `/muse-video/.upload.meta`
identifying a different 12224252-byte video. Its checkpoint was zero and its
identified partial occupied 65536 bytes. The global journal prevented any different
file from starting, even though it was safe to preserve that recovery and create
separate recovery for the new file.

Evidence: first desktop error (local-only evidence: `../.verification/session-2026-11-13/physical-desktop/first-upload-result.json`),
recovery before repair (local-only evidence: `../.verification/session-2026-11-13/hardware/recovery-before-fix.json`)
and recovery preserved after repair (local-only evidence: `../.verification/session-2026-11-13/hardware/recovery-repaired-before-upload.json`).

## 10. Confirmed USB root causes and exact fixes

The CH340-family USB-UART reset/control lines caused the first connection failure.
Disabling DTR on open alone was insufficient; explicitly releasing RTS and then DTR
returned 6232 application reply bytes from unchanged firmware. The selected-port
Rust adapter now releases both and reports actual control-line failures. Only real
Linux PTYs are exempt because they lack those modem-control ioctls.

New uploads use `/muse-video/.upload-<sha256>.meta`. An existing per-digest journal
takes precedence. Otherwise, a validated matching legacy journal resumes in place;
a valid foreign legacy journal and partial remain untouched while the new digest
gets its own journal. Corrupt/ambiguous recovery fails closed. Completion and
explicit discard touch only the identified current transfer. Matching legacy
compatibility, multiple cancelled-file switching/reboot recovery and preservation
of other files have meaningful integration coverage.

The existing binary protocol is retained. It uses one tested 1024-byte data chunk
in flight, sequence/CRC-checked ACKs, bounded exact-envelope retries and incremental
SD writes. Progress advances on confirmed accepted bytes. Checkpoints require
checked `fflush` and `fsync`, with separate durable offsets; an ordinary chunk ACK
is not a promise of power-loss durability. Completion requires exact transfer ID,
size, accepted/checkpointed/verified counts and SHA-256. Missing/null/wrongly typed
or mismatching SHA strings are rejected.

Periodic telemetry/frame events pause during receive/resume/verification. Logging
does not inject unframed application text. The firmware's `cancel_upload` command
checkpoints and retains identified incomplete media. For explicit user cancellation,
the shared desktop/CLI upload client then confirms the returned transfer ID and
requests `discard_upload` for that same owned transfer, removing only its partial
and journal. Transport-error cancellation retains recovery for same-file retry.
If cancellation/identity cannot be confirmed, cleanup fails closed and retained
data is reported. Startup hash/index reading can be safely preempted by a validated
upload without changing the existing video. No SD formatting was needed.

Explicit cancellation cleanup was then tested twice on the physical device with
the shared CLI uploader. Each attempt stopped after 1024 acknowledged bytes and
returned the expected `cancelled` result. Between them, a new `begin_upload` for
the same 288×216 candidate digest/2403968-byte size returned `receiving` with
`accepted_offset:0` and `checkpointed_offset:0`, proving that the previous 1-KiB
checkpoint had been discarded. A new-session `none` status alone is not used as
cleanup proof because port opening can reset the board. The second shared-client
cancellation cleaned the newly created owned upload too; unrelated media/recovery
was preserved and no card formatting occurred.
Evidence: first cancel (local-only evidence: `../.verification/session-2026-11-13/hardware/physical-cancel-cleanup.json`),
same-file rebegin at zero (local-only evidence: `../.verification/session-2026-11-13/hardware/physical-cancel-rebegin.json`),
repeat cancel (local-only evidence: `../.verification/session-2026-11-13/hardware/physical-cancel-cleanup-repeat.json`).

Selected-port baud discovery and the desktop actor preserve one port/client,
nonce, buffers and diagnostics. Valid device/I/O/JSON errors are propagated;
wrong-baud garbage is counted and resynchronized. The initial shorter-deadline
policy failed physically as recorded in section 3.

An explicit **2,000,000-baud** connection later completed HELLO, but its
3,419,792-byte q8/Lanczos upload attempt failed during preparation with no
matching reply for sequence 3350 after three 3,000-ms ACK waits. At the error,
connection-lifetime diagnostics showed 20 response timeouts, 19 retries, five
invalid/oversized received frames and 3,552,005 written protocol bytes. These
written bytes are not verified SD payload or a successful transfer count.
No final file-integrity or playback success is claimed for that attempt. Its
prepared digest `d075c4d4…` differs from the bicubic `5cef59ed…` baseline.
Evidence: failed large-file 2M attempt (local-only evidence: `../.verification/session-2026-11-13/hardware/usb2m-explicit-q8-lanczos-prefetch-off.jsonl`).

UART921600 also connected and completed some playback trials, but a later DMA2
trial failed to obtain a matching command reply after three 3000-ms waits. Its
retained connection-lifetime totals were nine timeouts, eight retries and 261
malformed/oversized frames. Much of the malformed traffic was already recorded
during handshake; it cannot all be assigned to playback. This rate is not
qualified for the normal large-file USB workflow. The successful large-file
native desktop transfer and later optimized-Huffman transfer both used 115200.

The production default remains 115200 baud, with multiple completely verified
large-file transfers. UART2M or 921600 playback samples do not qualify large-file
transfer at those rates. Faster rates are retained as failed/unqualified tests.
They do not prove a universal CH340 hardware maximum; they establish the observed
Linux VM/driver/device behavior.

## 11. Real native desktop end-to-end USB result

### Final selected image and default 25 workflow

The actual native Linux Tauri/WebKit application selected the MP4 through its
native picker and converted it with the first-launch defaults. Its production
Connect, Send over USB and Cancel handlers were exercised in the native WebKit
inspector after VM XTest pointer targeting failed. This used actual DOM handlers
and the actual native serial/backend, with no simulated state or direct backend
upload substitution. The card stayed inside the CYD throughout.

The final `3de1a18f…` image connected to `/dev/ttyUSB0` at 115200. Cancel was queued
from a snapshot with 105472 acknowledged bytes; the last sampled progress before
terminal cancellation was 124928. These are sampled values, not an invented exact
final cancel byte offset. Cancellation returned `cancelled` with no cleanup warning,
and SD free bytes returned to the pre-transfer 3712376832. The same prepared video
was then resent from confirmed offset 0 and completed successfully.

| Measurement | Actual result |
| --- | --- |
| Source FPS / encoded FPS / target FPS | 30 /25 /25 |
| Prepared video | 320×240, qscale 8/bicubic/4:2:0/optimal Huffman, 300 frames /12s; video-only |
| UART / framing / chunk | 115200, 8N1; COBS/CRC32 stop-and-wait; 1024 payload bytes |
| Accepted / checkpointed / verified bytes | 2444120 /2444120 /2444120 |
| Resumed offset | 0 |
| Receive phase | 246.611625225s |
| Confirmed receiving throughput | 9910.806101578 bytes/s |
| Final stored-file verification phase | 15.933068929s |
| Transfer through verified completion | 262.909876767s |
| SHA-256 | `9c19f630b9b2205dc5c25932c64b377f9b811dd3023dc5d68be73928bdee7fb7` — host/device match |
| Automatic playback | Matching digest, frame 1/300; encoded/target 25; simulatorfalse; error_count 0 |

Initial discovery accounted for two ACK timeouts/retries. The connection-lifetime
total retained five rejected wire frames; these are not isolated upload corruption
counts. Final size/SHA verification confirms the published stored file despite
recoverable serial noise. An immediate frame 1 snapshot proves automatic decoding/
rendering, not the steady achieved FPS; the subsequent actual playback/control
results are recorded in [NATIVE_DESKTOP_USB_E2E.md](NATIVE_DESKTOP_USB_E2E.md) and
[final-native-desktop-usb-e2e.json](evidence/final-native-desktop-usb-e2e.json).

Fifteen native control snapshots confirm restart, pause, 160×120 size, X16/Y8,
90/180/270-degree rotation, reset to 320×240, target 15/resume/target 25, stop and
Disconnect, with zero device errors. Actual completed-frame telemetry samples
have a median 24.99748775 FPS at target 25; their range 24.91872757–25.49755223 includes
startup/window effects and repeated cached snapshots. This is short-window
telemetry, not a whole-clip or endurance aggregate. The target 15 sample reports
14.99758539 actual FPS. The final renderer/SD/JPEG pipeline timings and full-region
pixel checks belong to the separate five-active-minute target 30 qualification.

The controls harness exited 0, serial descriptors were released and the native
window closed through WM_DELETE_WINDOW. After WebKit closure, the native runner
ended with 143 and the observer with 1; normal app exit 0 was not demonstrated.
These teardown outcomes are disclosed separately from the successful upload,
stored-file verification, automatic playback and confirmed live controls.

### Earlier 30-encoded native workflow

The microSD stayed inside the ESP32. The native Linux Tauri application selected
the MP4, prepared its video, connected to `/dev/ttyUSB0`, transferred the MVB over
the existing USB cable and received verified completion. Device state then reported
`playing` for the exact uploaded digest, with a real decoded/presented frame.
This is native application/hardware evidence, separate from browser or PTY simulation.

| Measurement | Actual result |
| --- | --- |
| Prepared content | 320×240, baseline JPEG 4:2:0, qscale 8, bicubic; 360 frames / 12 seconds |
| MP4 source FPS / encoded FPS | 30 / 30 |
| Target FPS in first post-upload device snapshot | 10; explicitly separate from encoded FPS |
| UART | 115200 baud, 8N1 |
| File bytes | 3327948 |
| Resumed offset / newly confirmed bytes | 0 / 3327948 |
| Sending phase | 338.022309327 seconds |
| Confirmed sending throughput | 9845.350168236 bytes/s, about 9.61 KiB/s |
| Final verification phase | 37.829769822 seconds |
| Transfer through completion | 376.113887414 seconds |
| Device accepted/checkpointed/verified bytes | 3327948 / 3327948 / 3327948 |
| SHA-256 | `5cef59ed0bd6410d54d93932bd450402676b71fe563e9c8d50bbc47f89f1807e` — exact host/device match |
| Final transfer result | `complete`, no transfer error |
| Playback acceptance | `playing`, matching digest, frame 18 / PTS600000 µs in retained snapshot |

Connection-lifetime host diagnostics reported 14 retries, 14 response timeouts and
41 invalid wire frames. They include connection history; they are not isolated
upload corruption counts. They also reported 127672 reads / 3884705 received bytes
and 4156 writes / 3493472 written bytes. A slower previous binary's bytewise host
receive counts must not be represented as the final batched client's performance.
Fresh before/after deltas are needed for faster-baud reliability comparisons.

Exact native result and post-upload state:
final-upload-continuation-success.json (local-only evidence: `../.verification/session-2026-11-13/physical-desktop/final-upload-continuation-success.json`).
The screen image itself is not physically photographed; visual smoothness/quality
qualification remains distinct from the confirmed successful transfer and playback path.

The earlier 37.830-second verification hashes SD bytes, validates the index and
decodes every JPEG before publication. The final-default 15.933-second result uses
a different file/frame count and optimized firmware; it is not an isolated
verification speed comparison. Unnecessary telemetry is already paused.
One safe future optimization is combining SD-reread hash and frame validation into
one pass, without substituting incoming-only SHA for verification of stored bytes.
The just-verified media is also rehashed by the subsequent normal load path; this
adds playback startup work beyond transfer completion and can be investigated
without relaxing final integrity checks.

Audio was already absent: conversion selects `0:v:0` with `-an -sn -dn`, MVB contains
only JPEG records/index, transfer sends MVB rather than MP4, and firmware initializes
no audio playback. Measured savings from removing audio are **zero bytes**; no
conversion/FPS improvement is attributed to a removal that was already in place.
The audio-source regression proves identical 30-frame/3-second MVB outputs from
video-only and longer-audio MP4 inputs while both source hashes remain unchanged.

## 12. Files modified or added and data preservation

There is no initialized Git history. This inventory combines the session changes
with a byte comparison against the retained pre-session project-source ZIP at
`.verification/windows-usb/combined-stage/sources/project-source.zip`; that snapshot
comparison is not described as a Git diff.

The exact current file inventory is recorded in
[source-change-inventory.json](evidence/source-change-inventory.json): 48 byte-modified
files and 91 paths absent from the retained snapshot, with no missing workspace
files. It includes each vendored file and fixture. Absence from
that archive is not proof of creation during this session;
the inventory is refreshed against the final packaged-source snapshot before handoff.
Every changed path is listed there; the following table groups the principal changes.

| Area | Modified or new files |
| --- | --- |
| Shared core/CLI/desktop | `crates/muse-video-core/src/serial.rs`, `media.rs`, `jpeg.rs`; `crates/muse-video-core/tests/device_engine.rs`, `video_storage.rs`; `crates/muse-video-core/examples/hardware_benchmark.rs`, `pack_jpeg_research.rs` (new); `crates/muse-video-cli/src/main.rs`; `crates/muse-video-desktop/src/device.rs`, `settings.rs` |
| Desktop source/default coverage | `src/lib/types.ts`; `src/App.svelte`; `vite.config.ts`; `tests/sync.test.ts`; `tests/browser/workflow.spec.ts` |
| Firmware | `firmware/include/engine.hpp`; `render.hpp`; `video.hpp`; `profiling.hpp` (new); `frame_prefetch.hpp` (new); `dma_ownership.hpp` (new); `jpeg_portable.hpp`; `firmware/patch_jpegdec.py`; new `rom_jpeg.hpp`, `tjpgd_fast.hpp`, `new_jpeg.hpp`; `firmware/src/tjpgd_fast.cpp` (new); `firmware/native/simulator.cpp`; `profile_tests.cpp` (new); `firmware/platformio.ini`; `firmware/src/main.cpp` |
| Vendored decoders | `firmware/vendor/tjpgd_fast/` source, original upstream files, configuration, README, license and provenance; `firmware/vendor/esp_new_jpeg/` classic-ESP32 archive, original headers, license, README and provenance |
| Research/tests | `research/benchmark_playback.py`; `research/build_native.py`; `tests/windows_package_test.py`; `tests/windows_device_tests_test.py` (new); new `jpeg_portable_test.py`, `rom_jpeg_test.py`, `tjpgd_fast_test.py`, `new_jpeg_test.py`, `decoder_options_test.py`; synthetic `tests/fixtures/tjpgd_fast/` fixtures; `frame_prefetch_test.py`, `dma_ownership_test.py`, `frame_integrity_test.py`, `jpeg_warmup_test.py`, `jpeg_dma_direct_test.py`, `first_frame_preload_test.py`, `loop_options_test.py`, `qualified_defaults_test.py` and `qualified_fast_defaults_test.py` |
| Packaging scripts | `packaging/build_windows.py`; `packaging/build_windows_test_kit.py`; `packaging/windows/Flash-CYD.ps1`; `packaging/windows/Flash.Common.ps1`; `packaging/windows/Device.Tests.Common.ps1` (new); `packaging/windows/Run-Device-Tests.ps1` (new) |
| Windows launchers | `packaging/windows/Flash-Firmware-English.cmd` (new); `packaging/windows/Flash-Performance-English.cmd` (new); `packaging/windows/Flash-Full-Firmware-English.cmd` (new); `packaging/windows/Run-Device-Tests.cmd` (new); `packaging/windows/Start-App.cmd` (new) |
| Copy/catalogs | `packaging/windows/FLASHER_README.md`; `packaging/windows/PORTABLE_README.md`; `packaging/windows/USB_TEST_README.md`; `packaging/windows/localization/en.json`; `packaging/windows/localization/tr.json` |
| Project documentation | `README.md`; `docs/PHASE_2_PROTOCOL.md`, `SERIAL_PROTOCOL.md`, `BUILD_AND_RUN.md`, `ARCHITECTURE.md`, `HARDWARE.md`, `WINDOWS_TESTING.md`, `USB_TRANSFER_AND_PLAYBACK_REPORT.md`; new `FORMAT_AND_AUDIO_COMPARISON.md`, `WINDOWS_DEVICE_TESTS.md`, `SD_DRIVER_INVESTIGATION.md`, `DECODER_EXPERIMENTS.md`, `NATIVE_DESKTOP_USB_E2E.md`, `PREFETCH_DESIGN.md`, `FRAME_INTEGRITY_PROBE.md`, `IDF_SDSPI_EXPERIMENT.md`, `JPEG_FIRST_BLOCK_WARMUP.md`, `docs/evidence/playback-current-matrix.json`; `QUALIFIED_PLAYBACK_DEFAULTS.md`, `QUALIFIED_FAST_DEFAULTS.md`, `DMA_DIRECT_OUTPUT_EXPERIMENT.md`, `FIRST_FRAME_PRELOAD_EXPERIMENT.md`; production-encoding, final-firmware, Windows-build and native-USB evidence JSON; this report (new) |

Generated evidence, prepared host test files, build outputs and private flash
backups remain in ignored verification/build directories. A full 4194304-byte
backup completed with per-segment verification before application-only writes.
Actual firmware update logs show writes starting at **0x10000** and esptool hash
verification; unrelated bootloader/partition/NVS/boot-selection/app1/filesystem
regions were not rewritten by those application updates. The Windows app-only
helper requires a complete backup and exact partition-table comparison, rejects
skip-backup and does not silently fall back to full flashing. Preserved OTA selection
means writing app0 alone does not guarantee an app1-selected board will boot it.
No SD formatting occurred. Private backup data and device identifiers are excluded
from the package source allowlist.

Protected hashes remain `b19624f4…` for MUSE_GUIDELINES, `5fa3df88…` for
MUSE_DESIGN_SYSTEM and `3b20053c…` for AGENTS, matching retained prior evidence.

## 13. Tests and verification actually run

| Scope | Observed result and evidence boundary |
| --- | --- |
| Initial workspace rerun | 25 available Rust tests passed; 12 native-dependent cases separately executed in that audit; historical log is not a claim of the final aggregate |
| Production optimal/default workspace run | Initially 38 tests passed: 14 core library, 7 nonignored device engine, 11 video storage, 3 desktop library and 3 desktop workflow; 20 native-dependent cases ignored and reported separately. After padding integration: 39 workspace tests passed/20 ignored, 15 guarded core-library and 11 video-storage checks passed. Real-FFmpeg default 25/explicit 30, Huffman RGB equivalence, audio exclusion, saved preferences and nine per-pixel padding oracle cases passed. Initial old-dimension assertions and a modulo-style Clippy failure were corrected and retained in `aligned-padding-initial-checks.json`; Rustfmt/Clippy passed afterward |
| Workspace including native cases | The earlier expanded invocation executed ignored native cases too: 58 passed, one desktop workflow assertion failed because it expected frame 10 after a 1-second seek despite the new 25-FPS default. The test was corrected to confirm encoded 25/1 and frame 25; isolated rerun passed in 4.66s. The complete corrected final invocation then passed all 59 tests with 0 failed/0 ignored and exit 0 in `release-final-workspace-tests.log` (15 core, 26 transport/native, 11 storage/audio, 3 desktop unit, 4 workflow). The failed invocation is retained in `zero-copy-workspace-including-native.log` and isolated rerun in `zero-copy-native-workflow-corrected.log`; leak checking remains disabled |
| Final journal/profile native engine | 19 integration cases passed, including exact completion SHA, retries/cancellation, legacy coexistence, switching/reboot resume, owned discard and display-failure FPS accounting |
| Core serial tests after baud changes | 5 passed: partial writes, cancellation, valid device errors, split/coalesced/corrupt-frame resynchronization and host receive behavior |
| Explicit cancellation cleanup regression | `cancellation_during_lost_ack_removes_owned_incomplete_files` passed against the native engine. Two physical shared-client cancellations after 1024 confirmed bytes returned expected `cancelled`; intervening same-file rebegin confirmed zero accepted/checkpointed offset. Transport-error recovery remains retained; host log `cancel-cleanup-host.log` and physical evidence linked in section 10 |
| Real Linux PTY discovery | 3 passed: withheld initial candidate, discovery through the third candidate, stale/invalid input, repeated HELLO, mapped-client preservation and explicit-rate device-error propagation; no physical baud claim |
| Native desktop actor after baud integration | 1 passed: handshake, upload, confirmed controls and reconnect using shared C++ engine/host files |
| Native ASan/UBSan build | Passed protocol/CRC, 500 transform golden cases and profile assertions; `detect_leaks=0`, so no passing leak check is claimed |
| Formatting/Clippy | Workspace Rust format check and core/desktop all-target Clippy with denied warnings passed after baud/actor changes |
| Frontend checks | Type check: zero errors/warnings; production build and format check passed; Node runner reported one file containing five individually rerun passing assertions |
| Production default frontend/browser checks | Svelte check: zero errors/warnings; six frontend tests, Vite production build, frontend format and workspace Clippy passed. Final browser workflow: one passed in 14.2s; light/wide, dark/wide and dark/narrow screenshots reviewed. Earlier assertion-before-selection and old seek-frame-one assumptions failed and were corrected in tests; those failed attempts are retained. A real Vite ELOOP watcher storm from generated Windows SDK symlinks was fixed by watch excludes; startup/JavaScript HTTP smoke returned 200 with zero errors |
| Windows package/helper host tests | 16 package tests passed, including recommended launcher flash scope, private/build-tree source exclusion, extraction aliases, checksum mutation and backup/app-only failure paths; 4 diagnostic-helper host tests passed, including explicit decoder/SPI/UART defaults and malformed metadata rejection; executed with PowerShell on Linux |
| Final-image packaging preflight | Assembly passed with exit 0 using the exact selected `3de1a18f…` firmware; portable 90084282 bytes and flasher 21468934 bytes in the temporary preflight output. The builder checked ZIP paths and complete internal checksum coverage. This preflight predates the final documentation freeze; final dated archives/digests are recorded in the external verification file rather than assigning preflight digests to regenerated packages |
| Safe JPEG word/patch checks | Four passed: exact unaligned host/ESP word reads under ASan/UBSan, pinned source mutation rejection/idempotence and allowed 2048/5120 tile bounds |
| ROM strip/ABI checks | Seven passed: 64 native/odd-edge/endian combinations, 2500 transform/clipping oracle cases, malformed callback bounds, draw failures/reuse and actual Xtensa ROM ABI compilation |
| Software decoder checks | Five passed: actual RGB565 vs unchanged RGB888 reference across four odd color/grayscale fixtures, 500 transform cases, failure/reuse/truncation/state checks and exact vendor hashes; actual Xtensa GCC14 compilation passed |
| Espressif block adapter checks | Nine passed: 32 native/endian/padded-strip combinations, 500 transform cases, lifecycle/allocation/failure checks and exact archive/header/license hashes; host codec substitute, not physical decoding proof |
| Later first-block adapter/warmup checks | Updated adapter: 13 passed; warmup engine: 7 passed under ASan/UBSan, including injected 700-µs header/900-µs block work credited to the consuming frame, codec reuse, prediction misses, command/storage invalidation, display completion failure and borrowed-buffer cleanup. Follow-up prefetch 3, decoder options 1, frame integrity 1, DMA ownership 1 passed; two optional TSan tests skipped in this later run. These precede any subsequent warmup changes and do not prove hardware FPS |
| Shared engine option checks | One test with two sanitized builds passed: enabled/disabled software decoder, old command compatibility, memory release, independent SPI clocks, and drawing/end-frame failures count zero FPS |
| Compressed read-ahead checks | Three executed tests passed in the recorded ASan/UBSan run; worker TSan was skipped there, then worker and actual-codec engine TSan tests each passed separately. Covers borrowed-slot ownership, blocked reads, command/storage cleanup, actual codec controls/failures and disabled synchronous fallback |
| DMA FIFO ownership checks | One host test passed: delayed DMA pixel reads, in-order ownership, bounded queue and invalid completion rejection; not a physical queue-depth benchmark |
| Final loop-policy checks | Three host tests passed, including interval validation, busy/quiet pacing and guarded runtime diagnostics; `loop-options-tests-final.log`. The separate physical one-second interval endurance completed 25 clips/300.435 active seconds with clean checks and exit 0 |
| Automatic FAST defaults / rejected preload | Eight actual Engine/TftPainter/codec/worker host-substitute FAST-default cases passed, including automatic direct3→copied2→blocks, codec/prefetch fallback, checked CRC, no redundant preflight RGB allocation and startup region/border coverage. Separate qualified10/direct10/loop3/preload9/warmup12 and native sanitizer500-transform checks passed. Watchdog tests cover known5/1-second/unknown configurations; normal/aligned pre-ROI cold boot and six runs passed, the selected aligned-ROI image passed its own 25-run endurance; package verification is reported separately. Preload itself was physically compared in six clean runs and rejected as the default because inclusive FPS did not consistently improve |
| Guarded direct-DMA checks | Actual TftPainter/adapter host-substitute suite: 10 passed, including 1200-frame delayed peripheral scenarios, exact FIFO/token ownership, warmup, independent integrity reads, alignment/capacity, transforms and fault/OOM cleanup. Final adapter 18, qualified-default 10, warmup 12, DMA 1 and integrity 1 passed; prefetch 5 test cases included 2 skips. Native ASan/UBSan 500 transform checks passed. These do not qualify the opaque SDK's real output-buffer switching or physical FPS; eight short physical comparisons and five-minute direct endurance passed; first-record preparation was measured and rejected; the final aligned-ROI image passed its own 25-run endurance with clean pixel checks |
| Optional IDF SDSPI checks | Four host/installed-Xtensa-SDK tests passed, including seven lifecycle/failure groups. Actual device SD20/SD40 DMA playback and sequential-read probes completed; neither complete JPEG result beat Arduino SD20. IDF SD40/prefetch failed explicitly with insufficient memory |
| Windows cross-builds | Final frozen-source MSVC x64 GUI and static-CRT CLI builds passed: `windows-final-14-app-build.log` / `windows-final-14-cli-build.log`. GUI 10820608 bytes, SHA`cc8a0946…`; CLI 1155584 bytes, SHA`2bc613bc…`. Exact hashes/imports are in [windows-build-2026-11-14.json](evidence/windows-build-2026-11-14.json). Missing external debug-PDB warnings remain. Windows runtime/COM/flashing are untested; final archive verification is recorded separately |
| ESP32 builds | USB-repair, buffering/POSIX/strip/record, optional decoders, optimized one-block and loop-options builds passed. The loop-options app was physically installed at 0x10000 and hash-verified; its artifact is 659584 bytes. Earlier corrected-driver static totals of 42052 RAM/626032 flash belong to that older build; dynamic runtime allocations remain separate. Direct-DMA build/install and all eight short comparisons passed; its five-minute direct endurance passed; first-record preparation was measured and rejected; the final aligned-ROI image passed its own 25-run endurance with clean pixel checks. Frozen selected 665888-byte firmware SHA`3de1a18f…` built, application-only flashed/hash-verified and reset; final FAST 8/direct 10 suites reran with exit 0 |
| Real native application/hardware | Captured connection failure and upload conflict, repaired firmware transfer, then passed earlier 3327948-byte and final 2444120-byte native USB size/SHA/automatic playback acceptance on the installed SD. Final 15 control snapshots confirm live size/position/rotation/FPS and playback controls; controls harness exit 0. Final native runner 143/observer 1 after WM_DELETE_WINDOW/WebKit closure are retained; normal app exit 0 was not demonstrated |

Relevant latest logs are under
real-device-investigation (local-only evidence: `../.verification/real-device-investigation/`), including
`usb-per-digest-journal-checks.json`, `baud-discovery-pty.log`,
`baud-discovery-serial-tests.log`, `baud-discovery-actor.log` and
`baud-discovery-clippy.log`. Earlier failed LeakSanitizer runs and the failed clock
and short-deadline physical connection tests are retained rather than reported as
successes. TFT_eSPI's existing missing TOUCH_CS warning means its touch functions
are unavailable; this project does not advertise a touch UI.

## 14. Remaining limitations and run instructions

Physical Linux USB-to-installed-SD transfer and firmware playback acceptance now
have end-to-end evidence. Windows GUI, COM driver, flasher and physical Windows
USB workflow are not runtime-qualified by Linux tests or Windows cross-builds.
macOS remains outside the supported/tested scope.
The final Linux native upload/control workflow succeeded, but normal application
exit 0 was not demonstrated: teardown returned native 143/observer 1 after the window
closed. The controls harness passed with exit 0 and the serial descriptor was released.

Sparse TFT-memory readback proves corruption in the tested actual 80-MHz
configurations. Current 40-MHz playback samples and separate complete-region
checks are clean, including five five-minute active endurance workloads with
constant end heap. The owner reports clean, smooth video. The final aligned-ROI binary
passed its own 25-clip/25-check workload; observations and sampled checks do not
prove every pixel or universal SD/UART limits.
FAT `fsync`/rename cannot promise transactional sudden-power-loss recovery.
Live scaling/rotation can cost more than the native-size path.
Arbitrary angles, audio playback, touch UI and calibrated CPU temperature remain
unavailable. Raw/AVI/chroma research files are not advertised production playback
formats without an implemented, physically tested player path.

The Espressif block adapter requires both prepared dimensions to be multiples
of eight. Explicit NewJpeg selection rejects unsupported geometry safely; automatic
production selection retains JPEGDEC for existing 320×180 and other unsupported
dimensions. Allocation/worker failures retain checked synchronous/block fallbacks.
Those fallback behaviors are implemented and host-tested; they are not attributed
the optimized path's FPS. Default contain conversion pads new aligned-bound 16:9
and portrait output without scaling again;16:9 playback is short physically
verified, portrait remains host-only. The selected image passed its own endurance; final native desktop acceptance is
recorded separately.

Raw session logs referenced above remain in the VM's ignored `.verification/`
directory and are excluded from the owner package together with private backups
and build tools. The portable report includes the shareable selected-record
summary [playback-current-matrix.json](evidence/playback-current-matrix.json),
including observed clocks, completed counts, sampled-pixel checks and heap data.

Independent repeatable host commands remain:

```sh
python3 research/build_native.py --sanitize
ASAN_OPTIONS=detect_leaks=0 MVB_SIMULATOR="$PWD/.verification/native/mvb-simulator" cargo test --locked --workspace -- --include-ignored --test-threads=1
cargo test --locked -p muse-video-core --test device_engine baud_discovery_pty -- --test-threads=1
cargo test --locked -p muse-video-core --lib serial::tests
cargo fmt --all -- --check
cargo clippy --locked -p muse-video-core -p muse-video-desktop --all-targets -- -D warnings
```

The persistent hardware benchmark example uses one selected connection across a
matrix. It does not flash or format the card; actual hardware operation must be
coordinated with the single port owner. Windows owner instructions are in
[WINDOWS_TESTING.md](WINDOWS_TESTING.md) and [WINDOWS_DEVICE_TESTS.md](WINDOWS_DEVICE_TESTS.md).
Final native desktop transfer/integrity/automatic playback passed. Final archive verification status
is recorded in Muse-Windows-2026-11-14-verification.json (local-only evidence: `../dist-packages/Muse-Windows-2026-11-14-verification.json`).
The UART
default remains the fully transferred/tested 115200 baud.
