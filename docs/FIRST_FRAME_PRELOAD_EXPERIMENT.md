# First-frame preparation experiment

This research option performs a real checked SD read before starting a fresh
media clock. It attempts to remove a cold startup deadline miss; it does not
change frame eligibility, dropped-frame rules, media duration or the existing
playback FPS denominator. Physical qualification is required.

The separate `cyd_jpeg_dma_direct_preload` environment adds
`MVB_FIRST_FRAME_PRELOAD` to `cyd_jpeg_dma_direct`. Selection initially remains
**false**, including in this research environment. Production builds omit the
guard. Enable the experiment explicitly before restarting:

```json
{"method":"set_startup_options","preload_first_frame":true}
```

Disable it with false for a same-firmware comparison. True is rejected by
builds without the guard. The setting controls future fresh starts and is not
persisted. It can be enabled during normal video loading to test automatic
ready/playback startup. It is unavailable while an upload owns storage.

On restart, or play/resume from ended/stopped, the main task joins any outstanding
read, abandons a partially warmed picture, and reads frame zero into the existing
owned compressed slot. The normal container header, JPEG dimensions, padding and
CRC checks run before this result becomes valid. The media clock starts from a
new real timestamp after the completed preparation. A failed read rejects the
start, enters the existing error/cleanup path and presents zero frames.

The first render consumes that checked result exactly once. It performs no
second SD read, frame allocation or full compressed-frame copy. The next worker
read uses the other existing compressed slot. The initial record also works
when worker prefetch is disabled. Ordinary pause/resume retains its playhead;
if the first result is still pending, it remains available. Generation changes,
seeks, probes and storage/configuration boundaries prevent stale result reuse.

When enabled for automatic startup, the existing full-screen clear and ready
configuration happen before the media anchor. The measured preparation interval
starts on entry to ready-playback setup. Previous file hash/index validation and
UART receive/reply queue time are outside this precisely labeled interval.
Manual restart preparation starts inside its command handler; its reply may be
deferred by the real SD operation.

`get_startup_profile` reports whether preparation was attempted, verified,
pending or consumed, its trigger/generation/bytes/error, preload and preparation
wall time, and nested SD/CRC/container/JPEG validation timings. Its scope excludes
earlier hash/index validation and UART queue time. `get_benchmark` adds a compact
`startup` summary with the existing playback elapsed time plus preparation, and
`actual_fps_including_preparation = presented_frames × 1,000,000 /
(playback_elapsed_us + preparation_us)`. Report this rate alongside ordinary
playback FPS and startup latency. Source and target FPS remain separate.

Preloaded record read/CRC timings are credited to the consuming frame's SD
profile. They happened outside its playback service time and must not be added
again to that service duration. This uses the existing prefetch timing convention.

`python3 tests/first_frame_preload_test.py` exercises the actual Engine, Video and
FramePrefetch implementation against a synthetic clock, fault-injected SD
adapter and fake JPEG codec under ASan/UBSan. A 360-frame source checks real read
before the anchor, exact-once consumption, no allocations, inclusive FPS
arithmetic, worker/warmup ownership, normal/ended/stopped/restart controls,
automatic startup, invalidation, corrupted/short reads, strict options and
guard-disabled behavior. Synthetic delays demonstrate correctness only; they
are not measurements of the ESP32 or evidence that physical playback reaches
30 FPS.
