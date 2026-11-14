# Direct JPEG output to TFT DMA experiment

This optional implementation removes the RGB565 staging copy for native,
unrotated, entirely visible Espressif NewJPEG output. It requires the explicit
`MVB_JPEG_DMA_DIRECT_OUTPUT` build flag. The controlled `cyd_jpeg_dma_direct`
environment extends `cyd_jpeg_warmup` and retains manual selection. The
`cyd_optimized` production environment adds `MVB_QUALIFIED_FAST_DEFAULTS` to
select the measured direct path automatically. Host verification does not
qualify SDK output-buffer switching or physical performance.

Physical direct-path endurance has completed with clean pixel checks; detailed
measurements remain in the hardware report. Production default integration still
requires cold-boot/upload validation. Build and serial-device ownership belong
to the hardware-test operator.

## Runtime selection

After pausing playback, return enough internal memory before configuring the
display: disable compressed prefetch and select JPEGDEC to release the existing
NewJPEG workspace. Enabling three buffers while the full existing pipeline is
allocated can correctly fail the retained 32-KB allocation safety reserve.
Configure the guarded renderer, then select NewJPEG, re-enable compressed
prefetch and first-block warmup, and restart:

```json
{"method":"set_render_options","mode":"dma","big_endian":true,"spi_hz":40000000,"command_spi_hz":20000000,"zero_copy":true}
```

Production `cyd_optimized` performs this selection during initialization. It
falls back to two copied DMA strips, then polling blocks if allocation or driver
setup fails. Its qualified JPEG-header preflight avoids reserving a redundant
owned codec strip when the direct painter can supply output leases, preserving
memory for compressed prefetch. `get_playback_defaults` reports the actual
renderer options and fallback. Controlled research environments continue to
require the explicit runtime command.

`zero_copy` accepts a JSON boolean. A true value requires the research build,
DMA, big-endian RGB565 output and queue depth two. An omitted or false value
keeps copying. Successful render configuration invalidates pending warmup before
reallocation. The decoder acquires no display buffer during header preparation.

## Ownership and memory

The active direct path owns three 320×16 RGB565 DMA-capable internal strips.
Their usable storage is 30,720 bytes; alignment padding adds at most 45 bytes
across the allocations. Each output pointer is aligned to 16 bytes as required
by the JPEG SDK. This replaces a 10,240-byte private codec strip plus two
10,240-byte staging strips. It allocates no full-frame RGB buffer or PSRAM.

The codec may write only after acquiring an exclusive slot/token lease. A
successful SPI submission changes that lease into pending peripheral ownership.
The pixels and persistent descriptor remain unchanged until the exact FIFO
descriptor completion is consumed. The two-transfer queue may hold the final
strips of a current frame while the third strip holds the future frame's first
decoded block. Current `end_frame()` drains its pending transfers and preserves
the independent future lease. The current frame is counted only after this
join succeeds.

Abandoning warmup closes private codec state and returns its unsubmitted lease.
Reconfiguration and integrity checking refuse outstanding leases. Invalid,
duplicate or reordered completion never makes borrowed memory writable.
Unconfirmed DMA completion retains all buffers and requires an actual reset.
An ownership fault also makes copied submission fail promptly.

Native dimensions, no rotation, no clipping, enabled native blocks, big-endian
pixels and a non-probe painter are required for direct submission. All other
cases retain the checked copied path. Complete TFT-memory integrity probes
allocate independent expected codec output, while the completed display strip
serves as readback scratch. Returning to direct playback releases the independent
codec allocation once; ordinary direct frames allocate no output buffers.

`get_display_info` exposes selection separately from actual use:
`zero_copy_selected`, `zero_copy_used`, `zero_copy_direct_submissions`,
`copied_dma_submissions`, `zero_copy_active_leases`, buffer/allocation byte counts,
alignment, pending/completed submission counts and ownership errors. A selected
mode with zero direct submissions must not be reported as active direct output.

DMA acquisition can wait inside a codec process call when all strips are owned.
That wait contributes to decode wall time as well as the display wait counter.
Thus DMA wait is a diagnostic subset of combined decode/display service; it is
not necessarily entirely nested in display submission and must not be added
again to either timing. The stage totals and overlapping lookahead counters
also remain nonadditive.

## Verification boundaries

Run `python3 tests/jpeg_dma_direct_test.py` to compile the actual TftPainter and
NewJPEG adapter against delayed fake SPI/codec peripherals under AddressSanitizer
and UndefinedBehaviorSanitizer. Tests cover 1,200 frames per repeated-frame
scenario, immutable queued pixels/descriptors, exact-once warmed submission,
third-strip ownership across joins, default/guard-disabled copying, full-region
integrity reads, transforms, allocation failure, codec/queue errors, stale
tokens, output alignment/capacity and unresolved completion retention. Parser
regressions are included in `tests/qualified_defaults_test.py`.

Before choosing this path for production, physically compare it with the copied
path using the same video, TFT clocks and scheduler interval. Measure actual
presented FPS, SD/decode/display/copy/wait/frame times, free/minimum heap and
ownership counters. Require complete TFT-memory readback plus long playback and
the owner's visual confirmation. The opaque pinned SDK must be shown to accept
switching `jpeg_dec_io_t.outbuf` on every block without corrupted pixels. Do not
infer that behavior or a speed improvement from the host substitute.
