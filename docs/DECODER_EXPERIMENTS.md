# Classic ESP32 JPEG decoder comparison

The existing JPEGDEC path remains the default. The optional `rom` decoder is
an explicit comparison path for the classic ESP32; host simulators and other
ESP32 targets reject it. This document describes implementation and automated
verification. Physical FPS results belong in
[REAL_HARDWARE_PERFORMANCE_REPORT.md](REAL_HARDWARE_PERFORMANCE_REPORT.md).

## Direct ROM path

The installed SDK enables the classic ESP32 ROM TJpgDec decoder. Its public
`esp_jpeg_decode` wrapper requires an entire output framebuffer, so the adapter
calls the actual `jd_prepare` and `jd_decomp` ROM functions directly using the
SDK's original ABI. It does not redefine the ROM format or use modern
TJpgDec headers with older ROM functions.

The ROM outputs temporary RGB888 MCU rectangles with inclusive coordinates
and tightly packed cropped rows. `firmware/include/rom_jpeg.hpp` consumes each
rectangle before returning, converts its pixels into the painter's requested
RGB565 byte order, and gathers one complete MCU row into a reusable strip.
The native display path then presents full-width strips through `TileRenderer`.
Position, clipping, scaling and rotation continue through the same tested
renderer as JPEGDEC.

The adapter has a 6,144-byte aligned decoder pool and a 10,240-byte RGB565 strip,
plus small state. It allocates once on explicit ROM selection, reuses storage
between frames, and releases it on JPEGDEC selection. There is no entire-frame
allocation and no per-frame application allocation. Allocation failure is
reported before decoder selection changes.

`jd_prepare`, `jd_decomp`, completed strip coverage, callback results and
`Painter::end_frame()` must all succeed. Partial or failed presentations cannot
increment actual FPS. SD/container frame validation still checks the encoded
frame dimensions before decoding. During upload verification, the new frame
need not match the previously selected renderer's dimensions.

The existing command is extended without breaking callers:

```json
{"method":"set_decoder_options","decoder":"rom","max_mcus":32,"native_blocks":true}
```

The optional `decoder` field accepts `jpegdec`, `rom`, `tjpgd_fast`, or
`new_jpeg`; omission retains the
current choice. The initial choice is JPEGDEC. The reply and `get_benchmark`
report the actual selected decoder. `max_mcus_applies` is true only for
JPEGDEC; other decoders determine output grouping themselves. Selecting decoder
options resets aggregate measurements so results do not combine configurations.

Decode timing excludes drawing and includes ROM RGB888-to-RGB565 conversion.
`color_conversion_nested` separately reports this conversion within decode
time; it must not be added a second time to the total. Decode-only probes still
perform conversion and skip SD and TFT work. The selected format is reported
as `rgb888_to_rgb565_nested_in_decode` or JPEGDEC's fused conversion.

## Software TJpgDec and Espressif block paths

The optional `tjpgd_fast` implementation uses modern software TJpgDec with
`JD_FASTDECODE=2` Huffman lookup tables. A
source review and 60 host ASan/UBSan decoding cases found 9,368 bytes of scratch
for the currently generated 4:2:0 samples. The helper uses a 10,240-byte reusable
pool plus one 10,240-byte RGB565 strip. These are observed fixture sizes,
not universal scratch guarantees for arbitrary JPEG files. Distinct modern
symbols and headers are required because its ABI differs from the ROM API.

The pinned source, original files, license and adaptation hashes are stored in
`firmware/vendor/tjpgd_fast/`. Public symbols/types/configuration have unique
names. Nine explicit allocation casts make the C decoder compile as C++ under
the firmware's global preinclude; decoding arithmetic is preserved. The
optional source wrapper is enabled with `MVB_ENABLE_TJPGD_FAST`. RGB565 conversion
is fused into decode; strip copies and optional byte swaps are reported as
`decoder_strip_copy_nested` within decode time.

Espressif `esp_new_jpeg` 1.0.2 also explicitly supports classic ESP32. Its block
interface can produce complete 8/16-line RGB565 strips. Its decoder is distributed
as a prebuilt archive under a license permitting use on Espressif products;
it has not been adopted merely because other ESP32 variants have published
performance tables. The optional `new_jpeg` helper requires dimensions divisible
by eight and rejects other geometries during decoder selection. It reuses its
handle and a queried, 16-byte-aligned output buffer bounded to 10,240 bytes;
final padded rows are clipped to the actual image height. Prepare/process/
complete-output failures are checked. `MVB_ENABLE_NEW_JPEG` and the pinned
classic ESP32 archive in `firmware/vendor/esp_new_jpeg/lib/esp32/` enable the
path. The component's license and exact hashes are retained beside the archive.

Only the selected optional decoder object remains allocated. Switching to
JPEGDEC frees optional objects. `decoder_owned_bytes` excludes the block codec's
private allocations, which are reported as unknown rather than estimated.
The first block decoder header/output allocation is prepared during explicit
selection; steady-state frames reuse it. Configuration or source geometry
changes may require reconfiguration, so allocation metadata names this scope.

The display command also accepts an optional `command_spi_hz`, independent of
pixel `spi_hz`. Omission chooses `min(spi_hz, 40000000)`; explicit command values
are 10, 20, 40 and 80 MHz. The legacy path requires both clocks at 20 MHz. This
is an experimental control; the hardware report records measured clocks and
stable combinations.

Some valid FFmpeg 4:4:4 JPEG sampling layouts are rejected by TJpgDec. The
review observed `JDR_FMT3` for an image whose three components all use sampling
factor `0x12`; standard libjpeg 4:4:4, 4:2:2 and 4:2:0 edge fixtures succeeded.
Decoder suitability therefore includes format compatibility, not only speed.

Pinned research source/archive hashes, exact scratch probes and edge results
are retained in the local verification directory
`.verification/session-2026-11-13/tjpgd-software-review/`. This directory contains
research artifacts and is excluded from Windows source packages.

## Verification

```sh
python3 tests/rom_jpeg_test.py -v
python3 tests/tjpgd_fast_test.py -v
python3 tests/new_jpeg_test.py -v
python3 tests/decoder_options_test.py -v
```

Seven checks passed on Linux: six portable sanitizer scenarios and one actual
Xtensa SDK ABI compilation. The sanitizer scenarios cover 64 native/odd-edge
configurations in both byte orders, exact unaligned callback allocations,
immediate callback-buffer reuse, 2,500 transform/clipping pixel-oracle cases,
invalid bounds/order/incomplete output, drawing failures, decode-only work,
reset behavior, and host unavailability. The ABI check verifies the SDK types
and direct ROM symbols; it skips explicitly if the installed SDK/toolchain is
absent. Optional paths can be supplied with `MVB_XTENSA_CXX` and `MVB_ESP32_SDK`.

The software decoder suite passed five checks using actual decoded RGB565
compared with the unchanged pinned RGB888 decoder, four odd color/grayscale
fixtures, both byte orders, buffer reuse, 500 transform/clipping oracle cases,
unsupported/truncated input, stale preparation, draw failures and pinned source
hashes. Its source also compiled with the actual Xtensa GCC 14 toolchain and
the firmware's global preinclude and disabled builtin memcpy flag.

The block-adapter suite passed nine checks for 32 native/endian/padded-strip
configurations, 500 transform/clipping cases, handle/output reuse and cleanup,
metadata/size/allocation/codec/draw failures, decode-only behavior and all vendor
hashes. It uses a codec substitute on the host and does not prove the prebuilt
ESP32 decoder's runtime behavior or speed.

The shared-engine option check passed with software decoding enabled and
disabled in two sanitized builds. It covers old command compatibility,
unavailable/invalid backend rejection, memory release, independent clock
validation, and real software-decode callback/end-frame failures counting
zero presentations.

The shared native core, simulator and profiling executable also compiled under
ASan/UBSan with warnings treated as errors. The native core and profiling checks
passed. These checks establish bounded memory access and presentation accounting;
they do not establish ESP32 decode latency, visual quality, or physical FPS.
