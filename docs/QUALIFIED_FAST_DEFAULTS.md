# Automatic qualified playback defaults

The selected PlatformIO default is `cyd_optimized_input_aligned`, extending
`cyd_optimized`. Both enable `MVB_QUALIFIED_FAST_DEFAULTS` with the measured direct
JPEG/DMA path. Automatic boot and newly verified desktop uploads select 40-MHz
pixel data, 20-MHz commands, big-endian output, three owned DMA strips, one-block
JPEG warmup and compressed prefetch. Encoded FPS still controls initial target
FPS; explicit user FPS settings remain in effect across media selection.

The adaptive idle interval initially uses one second only when the known SDK
watchdog bound permits it. A shorter supported interval is selected if that
margin is unavailable. User-selected loop intervals remain checked by the same
bound. This changes initialization, not frame eligibility or presentation
accounting. Source, target and completed actual FPS remain distinct.

At ready/playback startup, the fast default clears only the screen area outside
the prepared video's clipped region. A full 320×240 frame performs no separate
clear; bordered video clears its borders. The actual video pixels are supplied
by the first checked frame. This removes redundant TFT work without changing
the media-clock anchor or FPS denominator. Traditional and first-preload
research startup paths retain their existing clear behavior.

DMA initialization first attempts three direct-output strips. Failure falls
back to two copied strips, then checked polling blocks. The selected endian and
40/20-MHz clock pair remain consistent across these fallbacks. Decoder or
prefetch failure retains checked JPEGDEC or synchronous decoding as before.
`get_playback_defaults` now reports actual renderer mode, byte order, zero-copy
selection, clocks, idle interval and fallback rather than only intended options.

When direct output is available, JPEG-header preflight validates its metadata
without allocating a redundant private RGB strip. Private partial codec state is
closed before reserving prefetch memory. The active native path retains three
bounded output strips and no full-frame RGB allocation. Transforms, integrity
probes, incompatible dimensions and alternate decoders retain their checked
fallbacks. Control/upload boundaries abandon warmup and join borrowed reads.

First-record preload was tested physically but produced inconsistent improvement.
It remains outside production flags. The controlled `cyd_jpeg_dma_direct` and
`cyd_jpeg_dma_direct_preload` environments extend the common research base and
retain explicit direct/preload selection without the automatic fast-default
guard. Their comparison baselines therefore remain selectable.

`cyd_optimized_input_aligned` changes only the embedded compressed record to
`alignas(16)`. The prefetch allocator remains unchanged. Direct-capable builds
report actual fixed/current/prefetch record and current payload offsets modulo
16 in `get_benchmark.input_alignment`. Its short physical comparison reported
fixed/current/prefetch offsets0mod16 versus8mod16 in the normal build. It is
selected and passed final exact-image qualification; the small three-run difference
does not establish a universal causal speedup from alignment alone.

`tests/qualified_fast_defaults_test.py` runs the actual Engine, TftPainter,
NewJPEG lease adapter and compressed worker against delayed fake SPI. It covers
automatic 25-FPS selection, no redundant output strip, direct-to-copied-to-block
OOM fallbacks, codec/prefetch failure, CRC protection, warmup/reload/restart/upload
ownership, aligned heap allocation and startup border-clear coverage. Existing qualified and guard-disabled
tests remain separate. These host tests prove ownership and fallback behavior;
final cold-boot, large USB upload, playback performance and image integrity
require the physical device.
