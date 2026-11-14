# Bounded compressed-frame prefetch

The guarded `MVB_FRAME_PREFETCH` experiment overlaps preparation of the next
compressed frame with decoding and displaying the current frame. It preserves
synchronous exclusive storage ownership at every command and `tick()` boundary.
The implementation is host-tested; current physical results, selected defaults
and image-integrity qualification belong in
[REAL_HARDWARE_PERFORMANCE_REPORT.md](REAL_HARDWARE_PERFORMANCE_REPORT.md).

It allocates a second compressed slot using verified frame metadata. It does not
allocate a full RGB565 framebuffer, load a whole video, or assume PSRAM exists.

## Measured starting point

The physical device has no PSRAM. The 320×240, 30 FPS, qscale 8 acceptance artifact
contains at most 9,757 compressed bytes per frame. Its checked container record
therefore needs at most 9,776 bytes, including the 16-byte header and three-byte
padding allowance. The already allocated engine buffer remains one slot. A second
slot is allocated once using the verified `header.max_jpeg + 19`, not the entire
file size and not an assumed 153,600-byte RGB framebuffer.

| Physical configuration | Actual FPS | Frame ms | SD read + seek ms | Decode ms | Display ms | Free heap | Largest block |
|---|---:|---:|---:|---:|---:|---:|---:|
| JPEGDEC, native full strips, DMA 40 MHz | 16.4037 | 58.0876 | 11.6578 | 38.6383 | 5.3056 | 71,944 | 30,708 |
| ROM, native full strips, DMA 40 MHz | 11.7804 | 82.1418 | 12.2962 | 63.2485 | 4.7433 | 54,532 | 30,708 |

Source and target rates are both 30 FPS in these runs. Actual FPS counts completed
TFT presentations and includes ordinary loop/telemetry time. Decode excludes
drawing; opening and container validation have separate profile fields. DMA copy
and wait timings are nested in display time and must not be added twice.

If the complete measured JPEGDEC fetch phase (13.1398 ms including validation)
were hidden perfectly, the remaining measured frame service time would be about
44.9478 ms, or 22.25 FPS before loop overhead. Hiding SD read/seek alone gives
46.4298 ms, or 21.54 FPS. These are optimistic arithmetic bounds, not results.
Faster decoder measurements are needed before deciding whether this mechanism
can contribute to 30 actual FPS at full resolution.

Evidence: `matrix-posix-5120-sd20.jsonl` and
`matrix-rom-posix-5120-sd20.jsonl` under
`.verification/session-2026-11-13/hardware/`. The fast software and Espressif block
decoder hardware comparisons were not complete at the time of this inspection.

## Implemented ownership and lifetime

`firmware/include/frame_prefetch.hpp` supplies one persistent worker and one
outstanding request. On ESP32, the worker runs on core 0 at priority 0 with a
4,096-byte stack. The existing serial task can preempt it. The worker's clock is
`esp_timer_get_time()`; it never calls runtime telemetry, engine control, hashing,
decoder or display callbacks. Native tests use the same job/state logic with a
standard thread and an independent monotonic clock.

The verified engine buffer is one slot. The other slot has
`header.max_jpeg + 16 + 3` bytes, allowing a complete checked record. Only one job
can borrow a slot. Requests and results use acquire/release publication; a caller
cannot submit again until it consumes the completed result. No per-frame task,
slot or result allocation occurs.

For a counted playback frame, the main engine consumes the matching prepared
record or performs the normal checked synchronous fetch. It starts a worker read
into the other slot before JPEG decoding. The worker has exclusive access to
`Video`, `File`, their index/cursor/profile fields and SD while that read runs.
During overlap, the main engine accesses the retained compressed JPEG, decoder,
renderer, timeline and its own timing fields. The TFT and SD use independent SPI
hosts.

Every path joins the worker after JPEG callbacks and `Painter::end_frame()`,
including a failed decoder or display. **`render_frame()` never returns with a
read in flight.** Commands, periodic SD polling, capacity queries, telemetry,
probes, upload checkpoints and file changes therefore run with exclusive main
storage ownership. Cleanup also joins before closing or freeing resources.
A read is never interrupted by deleting its task or freeing its destination.

The current compressed record is retained until the next decode begins. Paused
position/scale/rotation redraw uses that same JPEG without a fresh SD read.
Scratch probes, seeks, restarts, stop and decoder changes invalidate the relevant
prepared/retained metadata. A successful current presentation can be counted if
preparation of the following frame fails; the checked future-frame error is then
published and playback stops after the worker relinquishes ownership. A failed
current presentation never increments actual FPS.

## Frame selection

`Timeline::next_for_prefetch()` predicts on a copy using the existing eligible
source-frame rules. It prepares the newest eligible candidate expected at the
next presentation start; if playback is early, it prepares the next eligible
frame before it is due. It never mutates live drawn, dropped, rate-skipped or
playhead state.

Prediction uses the previous measured decode plus display duration. The next
actual timeline decision remains authoritative. If its frame differs from the
prepared index, the engine records a miss and fetches the selected frame safely;
it never presents a stale record merely to count a cache hit. FPS, transform and
seek changes invalidate predictions. Two compressed slots and the separate RGB565
DMA buffers have independent ownership.

## Memory and fallback

Enable only after selecting the decoder and renderer, so their actual
allocations are included in the available-memory check. The ESP32 guard requires
free internal byte-addressable RAM of at least:

```
32768 reserve + verified slot bytes + 4096 stack + 1024 task/allocator allowance
```

Allocation and task creation must both succeed. A larger legal JPEG may exceed
the available contiguous block or this reserve; the command reports
`insufficient_memory` and synchronous playback remains available. Builds without
`MVB_FRAME_PREFETCH` reject enablement with `unsupported_configuration`.
Disabling or closing the video joins first and releases the second slot and task.

Experimental control:

```json
{"method":"set_prefetch_options","enabled":true}
```

`get_benchmark` and `get_pipeline_profile` expose `prefetch`: availability,
enabled state, allocated slot bytes, worker stack bytes/minimum free stack,
requests, hits, misses, errors, join wait and total background fetch time. The
benchmark utility supports `--prefetch on` or `--prefetch off` and checks the actual
selected reply. Omission preserves the selected state.

## Timing interpretation

The normal SD read/seek/CRC/JPEG-validation profile describes actual work on the
consumed frame, even when that work ran in the previous frame's worker. Aggregate
`average_read_us` is actual checked fetch duration; `average_us.frame_fetch_wait`
is the time spent obtaining the selected record on the main thread. These can
differ substantially on a prefetch hit.

`sd_fetch_overlapped` identifies the pipeline. Background fetch duration, decode
and display duration overlap and must not be added as independent serial stages.
`frame` remains measured current service time, including any join wait. Actual
FPS still counts only completed TFT presentations over elapsed playback time,
including ordinary loop/telemetry overhead. Read/decode probes are not video FPS.
Worker stack and memory require real-device measurements before recommending a
configuration.

## Verification

```sh
python3 tests/frame_prefetch_test.py -v
MVB_RUN_TSAN=1 CXX=g++ python3 tests/frame_prefetch_test.py -v
python3 tests/decoder_options_test.py -v
```

Three ASan/UBSan checks passed on Linux: actual-container blocked-read
join/release and slot ownership; actual JPEGDEC engine controls and cleanup;
and a disabled-build synchronous fallback. The worker and engine also passed
ThreadSanitizer separately on this host. The existing two-build decoder-options
suite passed after integration. Logs are retained under
`.verification/session-2026-11-13/prefetch-*.log`.

The deterministic tests exercise a read held across cleanup, one outstanding request,
unaligned record buffers, reusable allocations, 200 alternating-slot requests,
short and corrupt records, source eligibility, 30 varied seek/FPS/resume/pause
transitions, live retained redraw, SD scratch-probe invalidation, upload
preemption, failed current displays and failed future records. Main storage
operations and close assert that no worker read owns SD; delayed real-codec draw
callbacks confirm that overlap actually occurs. Host timings are synthetic test
evidence, never ESP32 performance results.

## Remaining tradeoff

Joining at the frame boundary deliberately keeps existing control and storage
semantics intact. If an SD read takes longer than decode/display, join waits for
the actual driver completion. Arduino's retries mean there is no assumed
500-ms whole-request cancellation bound. This experiment therefore does not
provide asynchronous command handling during a stalled card read; the prior
synchronous fetch had the same driver-bound blocking concern. No timeout may
free a borrowed slot while SD still writes to it.

If physical measurements justify reads that survive command boundaries later,
a separate asynchronous state machine would need immutable metadata/storage
snapshots, bounded deferred commands, cancellation acknowledgments, generation
handling and upload/remount preemption. That more complex design is not the
implemented experiment and must not be claimed as complete.
