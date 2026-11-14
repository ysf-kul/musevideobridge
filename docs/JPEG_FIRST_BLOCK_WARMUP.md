# Guarded first-block JPEG warmup experiment

The controlled experiment is compiled with `MVB_JPEG_FIRST_BLOCK_WARMUP` and
requires explicit selection. Selected production FAST defaults enable one-block
warmup automatically when its prerequisites are available. It requires NewJPEG and compressed
prefetch. Selection remains requested when live transforms change; warmup is
active only for native, unrotated video completely inside the display.

After selecting those decoder/prefetch options, use:

```json
{"method":"set_decoder_pipeline","warmup":true}
```

Set `warmup` to `false` to return to ordinary frame finalization. Builds without
the guard reject enabling the experiment and preserve synchronous playback.

In the original copied path, the current frame's final strip has already been copied into the display's
private DMA buffer when the renderer callback returns. The engine joins the
next compressed SD read, parses that next JPEG and decodes its first block into
the existing aligned codec output buffer, then calls the current frame's
`end_frame()` synchronously. The next presentation submits the cached first
block once and processes its remaining blocks normally. The one-block path adds
no second RGB strip or full-frame allocation. The display must consume or copy submitted
source pixels before its block callback returns, as that copied adapter does.
The selected direct-output path instead uses three exclusive SDK/DMA strips:
submission transfers ownership to the peripheral, and exact FIFO completion
permits reuse. The third strip can retain future warmed output while current
DMA drains. See [direct-output ownership](DMA_DIRECT_OUTPUT_EXPERIMENT.md).

Current FPS accounting is unchanged: a frame counts only after successful
decode and successful synchronous display completion. A failure preparing a
future frame is reported after the successfully completed current frame is
counted. Failed display completion counts zero, even if next-frame warmup
already succeeded.

The cached key includes predicted frame index, playhead generation, state
revision, original compressed pointer/length, geometry and RGB565 byte order.
The original input key is stored separately because the SDK I/O fields may
change during processing. A prediction miss, mutation, probe, upload, load or
close aborts partial decoding before compressed/output buffers are recycled.
Ordinary status, telemetry, upload-status and diagnostic queries preserve it.
The SDK does not document a partial-picture reset API, so aborted prepared or
partly decoded pictures close their private codec handle. The output allocation
is retained and the next checked picture opens a fresh codec. Successful cache
hits reuse both handle and output buffer.

Both `get_benchmark` and `get_pipeline_profile` expose `decoder_pipeline` with
selection/activity/cache state, attempts, hits, misses, aborts, errors, nested
warmup work time and codec open-call count since the benchmark reset. The
configuration preflight can parse without decoding; abandoning that prepared
picture also closes the handle. Private codec allocations are unknown; the
reported zero steady-state application-buffer allocations excludes private
codec opens and configuration changes.

Timing requires care because work overlaps. Cached header and first-block CPU
time is credited to `jpeg_open`/`jpeg_decode` of the frame that consumes it.
`work_us_nested` separately records lookahead work while the previous frame is
still being finalized, and is nonadditive. Frame service time includes the work
done during that actual service interval. Summing decode/display/SD stages is
not a valid frame-time identity when either SD prefetch or warmup is enabled.

The later two-block experiment retains an additional checked RGB565 strip so
both cached blocks can be submitted in order. At 320×240 this adds 10240 owned
cache bytes and raises reported adapter storage to 20616 bytes, excluding private
codec allocation. It preserves the same synchronous display-completion and
abort-before-reuse ownership contract. Three physical full-screen q8 trials
measured 27.7132/27.6260/27.7147 actual FPS, compared with 28.80–28.87 FPS for the
one-block trials. All sampled and separate 76800-pixel checks passed, but the
two-block minimum heap fell to 18508 bytes. It is rejected as the default because
it is slower and uses more RAM. The single-block path remains the selected
production default. The exact final image subsequently passed 25 repeated clips
over five active minutes at full 320×240 and 29.79923446 actual FPS, with clean
sample/full-region checks and constant end heap.

Real one-block full-screen command-clock repetitions at actual 40-MHz pixels
passed samples and full-frame reads at command 10/20/40 MHz. The native 304×224
candidate presented all 360 frames in 12.000550 seconds (29.99862506 actual FPS),
with 0 dropped frames and a complete 68096-pixel region check passing. This smaller
native region is explicitly separate from full 320×240. Complete results and their
qualification scope are in
[REAL_HARDWARE_PERFORMANCE_REPORT.md](REAL_HARDWARE_PERFORMANCE_REPORT.md).

Host verification uses the actual engine/container/thread-prefetch code and a
strict fake block codec. Only a temporary header's codec availability guard is
adapted; product target gates are preserved. The fake refuses partial-picture
reparse, mutates SDK input fields, produces distinct next-frame pixels and
models a private pending DMA copy. Tests cover exact cached-block submission,
both byte orders, retained pixel ownership, abort-before-overwrite, controls,
probes, upload, prediction misses, destruction, failure accounting, timing
credit and a disabled-guard build under ASan/UBSan. Run:

```sh
python3 tests/new_jpeg_test.py
python3 tests/jpeg_warmup_test.py
python3 tests/frame_prefetch_test.py
```

These host checks prove state/lifetime/accounting behavior of the substitute.
They do not measure real JPEG decode time, actual DMA, image quality, controller
integrity, extended playback stability or physical FPS. The hardware benchmark
established the one-block gain, rejected the slower two-block cache, and recorded
five-minute repeated active workloads with clean image/heap checks. Exact selected
binary qualification is reported in [the hardware report](REAL_HARDWARE_PERFORMANCE_REPORT.md).
