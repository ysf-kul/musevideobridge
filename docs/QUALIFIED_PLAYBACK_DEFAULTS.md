# Qualified CYD playback defaults

The selected PlatformIO default is `cyd_optimized_input_aligned`, extending
`cyd_optimized`. Both enable automatic FAST defaults for the physically tested
classic ESP32 CYD. Production validation retains baseline JPEG 4:2:0;
research chroma and first-record preload remain disabled. See
[automatic FAST defaults](QUALIFIED_FAST_DEFAULTS.md) for ownership and fallbacks.

Startup allocates essential serial queues and the Engine before display resources.
The fast path selects NewJPEG, three exclusive 16-byte-aligned RGB565 DMA strips,
a two-transfer queue, 40-MHz pixels, 20-MHz commands, big-endian output and SD20
on the separate VSPI bus. UART remains115200 baud. Compressed prefetch and one
warmed future block overlap SD reading, decoding and display work. Final DMA
completion precedes presentation accounting; no full-frame RGB buffer or PSRAM
is assumed. A future decoded strip remains leased until submitted or abandoned.

The selected environment also aligns the fixed compressed record to16 bytes.
Actual fixed/current/prefetch offsets were0mod16 in its short physical comparison;
normal-build offsets were8mod16. The prefetch allocator itself is unchanged.
The small three-run difference does not establish a universal causal speedup.
The selected image sustained 29.79923446 actual FPS at full 320×240 over five
active minutes, with all 25 sample/full-region checks passing. Earlier research
images retain their own higher near30 results. Exact-image acceptance is recorded in the
[hardware report](REAL_HARDWARE_PERFORMANCE_REPORT.md).

At ready/playback startup, the fast default clears only outside the video's
clipped region. Full320×240 video needs no separate screen clear; smaller videos
clear their borders. The first checked frame supplies the actual video pixels.
This removes redundant TFT work at the existing clock anchor; it changes neither
media timing nor the FPS denominator. The separate SD-first-record preload
experiment was measured and rejected as the default, with inclusive FPS disclosed.

The initial requested FPS follows the prepared file's encoded FPS. Desktop
first-launch conversion remains25FPS/qscale8/bicubic/4:2:0/optimal-Huffman/video-only.
Explicit user FPS commands persist across media changes within that firmware
session. The firmware adds no flash preference; existing desktop FPS, language
and theme settings remain persistent and unchanged. Source, encoded, target and
actual completed-display FPS are distinct.

Allocation/driver failure falls back from three direct strips to two copied
strips, then checked polling blocks. Unsupported geometry or decoder preparation
failure retains JPEGDEC; worker/prefetch failure retains synchronous playback.
CRC/container/JPEG failures still report their real errors. Live transforms,
integrity probes and incompatible dimensions retain checked copied/transform
paths. Control/upload boundaries abandon warmed codec state and join reads before
recycling storage. Normal decoder output buffers are reused.

Use this independent query, which preserves cached output:

```json
{"method":"get_playback_defaults"}
```

It reports the actual renderer mode, byte order, clocks, direct-output selection,
idle interval, decoder, prefetch/warmup state and fallback reasons. Actual direct
use is separately demonstrated by `get_display_info.zero_copy_direct_submissions`;
selection alone is not proof of use. The one-second initial idle interval is
bounded by the known SDK watchdog configuration. Detailed diagnostics remain
separate from normal telemetry to preserve the2048-byte response limit.

Host tests cover actual Engine/container/prefetch code, delayed fake SPI,
exclusive leases, direct→copied→blocks fallbacks, no redundant RGB preflight,
CRC protection, automatic rate choice, legacy dimensions, transforms, restart/
upload ownership and startup border coverage. They do not prove physical FPS.

```sh
python3 tests/qualified_fast_defaults_test.py
python3 tests/qualified_defaults_test.py
python3 tests/jpeg_dma_direct_test.py
cd firmware
pio run -e cyd_optimized_input_aligned
```

Final cold-boot/endurance/native USB acceptance and exact packaged binary hashes
remain tied to the specific physical evidence in the hardware report; historical
manual-mode measurements are not silently assigned to a new image.
