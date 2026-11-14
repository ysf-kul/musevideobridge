# Memory, performance and honest telemetry

> Phase 1 architecture/evaluation retained for reference. Current implementation and deviations are documented in [PHASE_2_REPORT.md](PHASE_2_REPORT.md).
All device budgets below are estimates until measured on the actual unit. Source IDs
are in [SOURCES.md](SOURCES.md). Host experiments are explicitly separated.

## RAM budget

ESP32 has **520 KiB on-chip SRAM for data/instructions**, not 520 KiB of available
heap. SRAM0/1/2 have different instruction/data mappings; static/IRAM, caches,
FreeRTOS/Arduino and driver allocations reduce usable space. The reference board
manifest's 320 KiB maximum RAM is a build/linker convention, not runtime free RAM.
RTC memory is not a substitute for a large normal/DMA framebuffer. Assume no PSRAM.

| Allocation | Estimate / exact arithmetic | Policy |
| --- | --- | --- |
| Full RGB565 frame, 320×240 | **153,600 bytes (150 KiB)** | Optional single source frame only |
| Two full RGB565 frames | **307,200 bytes (300 KiB)** | Not the baseline on no-PSRAM ESP32 |
| JPEGDEC working state | Roughly 20–24 KiB [J1] | Measure `sizeof` on pinned target; not per-frame heap |
| Two 320×8 RGB565 output strips | **10,240 bytes (10 KiB)** | Internal DMA-capable memory with completion ownership |
| SD read buffer | 4–8 KiB | Sequential, aligned driver-compatible buffer |
| 64-entry container index cache | **1,536 bytes** | No entire-video index in RAM |
| Serial framing/queue/receipts | Approximately 8–16 KiB | Fixed bounds, small cached replies |
| Task stacks and state | Approximately 12–24 KiB | Measure stack high-water and guard failures |
| Driver/filesystem/other state | SDK-dependent | Include in runtime baseline, avoid double-counting static buffers |
| Optional compressed frame buffer | Up to 48 KiB | Additional cost; not required for streaming |

A tile/strip player needs approximately **45–85 KiB incremental application working
memory**, depending on what is static and which queues/stacks exist. A single decoded
frame raises that by 150 KiB. Adding a 48 KiB compressed buffer as well is likely too
tight once SDK/driver overhead and safety margin are counted. These are incremental
budget ranges, not predicted idle/free heap values or promises of successful allocation.

Allocate the optional frame once only after checking free internal 8-bit heap and
largest contiguous block. Preserve a measured reserve (initial candidate 32 KiB)
and sufficient DMA-capable memory/stack margins. Allocation failure leaves a safe
bounded rendering mode and explicit unsupported capability. No second framebuffer
or full-video buffer. Enabling Wi-Fi consumes memory/CPU and requires requalification.

## Timing bounds

A full 320×240 RGB565 screen requires 153,600 SPI payload bytes. Pure transfer time,
excluding address windows, bus gaps, decoder/storage/CPU work, is:

| TFT SPI clock | Payload-only time | Unrealistic transfer-only ceiling |
| --- | ---: | ---: |
| 20 MHz | 61.44 ms | 16.3 full frames/s |
| 40 MHz | 30.72 ms | 32.6 full frames/s |

Neither row predicts real FPS. At 10 FPS there is 100 ms total budget; at 15 FPS
66.67 ms; at 20 FPS 50 ms; at 30 FPS 33.33 ms. A 40 MHz full-screen transfer alone
almost exhausts a 30 FPS deadline. Small active rectangles reduce work. Decode cost
depends on compressed size/subsampling; rotations/scaling add arithmetic and can
create many SPI address windows. DMA improves overlap but does not remove wire time.

For 10 KiB JPEGs at 15 FPS, payload SD throughput is ~150 KiB/s; for 40 KiB it is
~600 KiB/s. A 10 MHz SPI SD link has only a theoretical 1.25 MB/s wire ceiling,
before command/FAT/latency overhead. Card speed labels do not establish small-read
latency in SPI mode. Sequential buffered reads and an indexed single file are sensible,
but their actual advantage must be measured on the installed card.

**Initial benchmark targets: 8, 10, 12, 15 FPS**, identity first, then scale and four
quarter-turns. Start the UI's proposal at 10 FPS; do not advertise guaranteed 15/30
FPS. Test high-detail frames, not only low-entropy graphics. Long-run thermal/power,
SD latency spikes and UART activity matter. Report read/decode/draw times separately
with nonoverlapping intervals; decode callbacks often include synchronous draw time,
so subtract nested draw duration or label combined work instead of double-counting.

Arbitrary-angle rotation requires source-frame random access/resampling, dirty-area
handling and more CPU. Classic ESP32 has no JPEG or video/rotation accelerator and
no S3 SIMD benefits. Keep arbitrary angle unavailable by default; only expose it as
experimental if measured heap, drawing correctness and sustained FPS justify it.

## Actual host encoding experiment

Ran 12 cases: two four-second generated H.264 MP4 corpora, output 10/15 FPS, qscale
4/8/12, baseline 4:2:0 JPEG in AVI. The pattern source is 640×360 (output 320×180);
noise source/output is 320×240. All 600 packets were inspected and all AVI outputs
decoded with **FFmpeg n9.0.2 on CachyOS Linux**. No ESP32/SD benchmark ran.

| Corpus | Rate | qscale | Mean JPEG bytes | Maximum JPEG bytes | Measured AVI bytes | Modeled draft bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Pattern | 10 | 4 | 8,322.20 | 9,082 | 339,628 | 334,644 |
| Pattern | 10 | 8 | 5,939.93 | 6,404 | 244,342 | 239,360 |
| Pattern | 10 | 12 | 4,905.68 | 5,217 | 202,968 | 197,976 |
| Pattern | 15 | 4 | 8,328.93 | 9,082 | 506,960 | 502,296 |
| Pattern | 15 | 8 | 5,946.48 | 6,404 | 364,024 | 359,380 |
| Pattern | 15 | 12 | 4,912.12 | 5,217 | 301,960 | 297,308 |
| Noise | 10 | 4 | 65,342.28 | 66,030 | 2,620,432 | 2,615,440 |
| Noise | 10 | 8 | 48,302.97 | 48,931 | 1,938,866 | 1,933,868 |
| Noise | 10 | 12 | 39,363.62 | 39,925 | 1,581,286 | 1,576,296 |
| Noise | 15 | 4 | 65,352.30 | 66,031 | 3,928,368 | 3,923,716 |
| Noise | 15 | 8 | 48,305.07 | 48,913 | 2,905,542 | 2,900,892 |
| Noise | 15 | 12 | 39,369.63 | 39,988 | 2,369,412 | 2,364,772 |

The q=4 noise frames violate the proposed 48 KiB bound; q=8 is close to it. This
demonstrates why average JPEG size cannot replace a maximum-frame validation pass.
These synthetic inputs do not establish visual quality for the owner's media.
The draft container was **not written/decoded**; its byte count is arithmetic using
the actual packets and specified record/index overhead. No claim of custom-format
runtime validation or faster SD playback follows from this comparison.

At 15 FPS, one minute costs approximately 8.83 MiB at 10 KiB/frame or 35.2 MiB at
40 KiB/frame, including ~40 bytes/frame of draft overhead. A hypothetical entirely
free nominal 4,000,000,000-byte card could hold roughly 7.2 or 1.8 hours respectively;
usable space, the existing data and measured compression will lower this. Enforce
the per-file 2 GiB implementation bound rather than implying all capacity fits one file.

## UART transfer estimates

8N1 transmits ten bits per data byte. These are payload ceilings, before framing,
ACKs, USB latency, SD writes, checksums and retries:

| Baud | Ceiling bytes/s | Minimum for 100 MiB |
| --- | ---: | ---: |
| 115200 | 11,520 | 151.7 minutes |
| 230400 | 23,040 | 75.9 minutes |
| 460800 | 46,080 | 37.9 minutes |

Measure actual goodput and show progress/remaining time from accepted bytes. No
stable high-baud or hardware-flow-control claim. At 115200, low-rate control/telemetry
is practical; sending all video frames for a live preview is not the baseline.

## Reliable telemetry methods and unavailable values

| Requested measurement | Proposed source / semantics | Limits / unavailable policy |
| --- | --- | --- |
| Free heap | `heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)` | Exact capability set shown; not all 520 KiB SRAM |
| Minimum free heap | `heap_caps_get_minimum_free_size` with same caps | Allocator low-water since boot; not minimum over current video |
| Largest free block | `heap_caps_get_largest_free_block` | Needed for framebuffer allocation; total free can be fragmented |
| Total RAM | Separate SoC SRAM (532,480 bytes) and `heap_caps_get_total_size` for allocator | Do not sum overlapping memory-capability views; no invented used-RAM percentage |
| Available RAM | Free internal 8-bit heap plus separately reported PSRAM if detected | Not a single fabricated desktop-style total-minus-usage figure |
| PSRAM | Initialized SPIRAM capacity/free via heap APIs; Arduino detection helpers | `null` before supported detection, 0 only after confirmed none; no board assumption as a reading |
| CPU utilization | Per-core FreeRTOS runtime idle/task deltas, runtime stats enabled | Estimated scheduler busy % over a stated window, not decode duration / frame period; null if unavailable |
| CPU frequency | `getCpuFrequencyMhz()` or IDF clock API | Actual current clock, not configured maximum |
| CPU temperature | No supported calibrated classic-ESP32 driver path qualified | **Unavailable**; never synthesize from CPU load, ambient range or LDR |
| SD raw capacity | Card sector count × sector size / `SD.cardSize()` | Distinct from formatted filesystem capacity |
| SD free/total space | `SD.totalBytes/usedBytes`, or FatFs free-cluster queries | May scan FAT; cache outside frame loop; null when unmounted/query fails |
| SD read performance | Actual bytes and monotonic read durations; isolated sequential benchmark later | Report bytes/s and latency/sample size; exclude sleep/decode; no read yet → null |
| Wi-Fi | Actual interface state and connected AP RSSI (`esp_wifi_sta_get_ap_info`) | Default disabled; RSSI null when disabled/disconnected, not 0 dBm |
| Uptime | `esp_timer_get_time()` / 1,000,000 | Since boot; 64-bit counters/times |
| Current frame | Last completely submitted/drawn JPEG index/PTS plus generation | Not next decoded frame; partial failure does not increment completion |
| Actual FPS | Completed video-frame draws / measured active interval, e.g. sliding 2 s | No pause time in active rate; no repeated blank/preview draws; not panel refresh measurement |
| Dropped frames | Eligible timeline frames skipped because work was late | Separate intentionally omitted `rate_skipped_frames` when FPS cap is lower |
| Late frames | Completed scheduled frames finishing after their deadline | Separate from skips/drop counts; define tolerance/window |
| Playback errors | Stable code, frame index, subsystem and bounded diagnostics | Counters since boot/playback identified; never silently ignored |

FreeRTOS SMP accounting needs care: migrations and two idle tasks mean summed task
time is not automatically one-core wall time. Prefer per-core idle deltas, bounded
task-count storage and documented timer resolution; clamp/report invalid windows.
Enabling runtime stats may need an Arduino-as-IDF-component build to configure the
SDK. If the pinned prebuilt Arduino core cannot supply trustworthy stats, CPU load
remains unavailable until that configuration is implemented and verified. Do not
substitute a UI timer or a guessed constant.

Arduino's classic `temperatureRead()` calls an explicitly **undocumented** ROM
`temprature_sens_read()` in inspected source [E4]. Existence of that symbol does not
establish a supported accurate sensor. ESP-IDF's supported temperature driver builds
only with `SOC_TEMP_SENSOR_SUPPORTED`, which classic ESP32 does not advertise [E3].
The product therefore reports temperature unavailable; an explicit future supported
external sensor would be a different measurement, not CPU die temperature.

Full telemetry should normally run at **1 Hz**, completed-frame events up to **10 Hz**.
Each nullable field has a support/status reason and sample time; disconnect retains
last-known values with a stale label, not live readings. Capacity/free-space queries
and benchmarks never run inside every frame's critical path.
