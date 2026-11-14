# Video format and audio audit — 2026-11-13

These results cover host conversion and artifact integrity. They are not ESP32 SD,
decode, rendering or visible-quality measurements. Hardware results belong in the
device investigation report once the actual board has been exercised.

## Existing audio behavior

The production converter already selects `0:v:0` and supplies `-an`, `-sn`, and `-dn`.
FFprobe selects the video stream, and the chosen video stream's duration takes
precedence over a longer MP4 container duration. JPEG verification decodes only the
prepared frames. The MVB v1 format contains JPEG frame records and a frame index;
it has no audio track. Desktop USB and direct-card export send that prepared
container, not the source MP4. Firmware validates/decodes JPEGs to RGB565 and does
not initialize an audio decoder, DAC or speaker output.

No audio-removal implementation change was needed. The measured saving against
the previous pipeline is **zero bytes** because it already excluded audio. No
conversion-time or playback-speed improvement is attributed to removing it. Source
MP4 files remain intact.

The new `audio_is_excluded_without_changing_video_timing_or_source_files` regression
creates a 3-second video and muxes it with a 7-second AAC track. It confirms the
source really contains audio and lasts over 6.9 seconds, then converts both inputs
with the current production defaults. The fixture source video is 12 FPS; both
outputs contain exactly 75 frames at 25 encoded FPS over 3 seconds and have identical
MVB SHA-256 values. Both source hashes remain unchanged. The longer unused audio
changes neither the prepared bytes nor the video timeline. This test
passed on the available Linux host. It does not require a physical ESP32 to prove
that the transferred bytes contain no audio.

## Expanded 30 FPS host comparison

The existing detailed synthetic source is 640×480 at 30 FPS for 12 seconds, with a
grid and seeded mild noise. All profiles below produce 360 independent baseline
4:2:0 JPEGs. Every output passed the production container/full-decode verification;
the saved MVB and raw SHA-256 values were checked again after session restart.

| Configuration | Resolution | Format | qscale | Source / encoded / target FPS | Actual ESP32 FPS | File bytes | Mean JPEG bytes | First-frame SSIM |
| --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: |
| Quality candidate | 320×240 | MVB/JPEG 4:2:0 | 4 | 30 / 30 / 30 | Unmeasured | 5,938,620 | 16,454.4 | 0.944863 |
| Quality/performance candidate | 320×240 | MVB/JPEG 4:2:0 | 6 | 30 / 30 / 30 | Unmeasured | 4,433,480 | 12,273.5 | 0.921168 |
| Existing-quality comparison | 320×240 | MVB/JPEG 4:2:0 | 8 | 30 / 30 / 30 | Unmeasured | 3,419,792 | 9,457.7 | 0.898293 |
| Compression comparison, area scaler | 320×240 | MVB/JPEG 4:2:0 | 10 | 30 / 30 / 30 | Unmeasured | 3,152,380 | 8,714.9 | 0.873530 |
| Reduced active image | 288×216 | MVB/JPEG 4:2:0 | 8 | 30 / 30 / 30 | Unmeasured | 2,829,132 | 7,816.9 | 0.879620 |
| Reduced active image | 256×192 | MVB/JPEG 4:2:0 | 8 | 30 / 30 / 30 | Unmeasured | 2,372,152 | 6,547.6 | 0.852136 |
| Reduced active image | 240×180 | MVB/JPEG 4:2:0 | 8 | 30 / 30 / 30 | Unmeasured | 2,150,656 | 5,932.3 | 0.845215 |
| Sequential raw candidate | 320×240 | RGB565 BE | — | 30 / 30 / — | Unmeasured | 55,296,000 | — | 0.952853 |
| Sequential raw candidate | 288×216 | RGB565 BE | — | 30 / 30 / — | Unmeasured | 44,789,760 | — | 0.942277 |
| Sequential raw candidate | 256×192 | RGB565 BE | — | 30 / 30 / — | Unmeasured | 35,389,440 | — | 0.928994 |
| Sequential raw candidate | 240×180 | RGB565 BE | — | 30 / 30 / — | Unmeasured | 31,104,000 | — | 0.926465 |

Lanczos scaling is used unless stated otherwise. Raw frames are prepared directly
from the source, avoiding the misleading alternative of decoding already lossy
JPEGs into raw frames. The raw layout is contiguous rows, two bytes per pixel,
RGB565 most-significant byte first, with geometry/rate/count/hash in a sidecar.
The complete raw output was decoded on the host and its exact frame byte count
verified. These research files are **not accepted by the production MVB player or
USB upload**. They are inputs for an isolated future raw streaming benchmark, not
an advertised product feature.

SSIM compares only the first prepared frame against a qscale-2 Lanczos JPEG reference
at matching temporal sampling, on an aspect-preserved 320×240 canvas. It does not
prove whole-video quality, temporal smoothness, color equivalence after RGB565 or
what looks indistinguishable on the 2.8-inch display. The reduced profiles must be
tested both at their native dimensions and scaled to the same active screen area;
the native test uses fewer TFT pixels and must disclose that difference.

Raw 320×240/30 FPS requires 4,608,000 SD payload bytes/s. It is 16.2 times the size
of the q8/Lanczos MVB on this source. Its 153,600-byte frame has a calculated TFT
payload floor of 61.44 ms at 20 MHz, 30.72 ms at 40 MHz and 15.36 ms at 80 MHz.
These are arithmetic bandwidth bounds; they are not actual SD speeds or stable
electrical clock qualifications. Real existing-file raw-sized read probes measured
about 131 ms for 153600 bytes with Arduino SD20/POSIX and 115.819 ms with actual IDF
SDSPI DMA SD40. The full-screen DMA40 RGB565 stripe probe takes about 31 ms.
These component measurements explain why raw was not selected for this current
software path, but they are not a complete raw-video player or a permanent card
bandwidth limit.

## Container and chroma alternatives

For each JPEG profile the tool muxed the exact JPEG stream into MJPEG AVI, demuxed
it again and compared SHA-256. Payload identity passed. For q8/Lanczos/30 FPS, AVI
is 3,583,516 bytes versus 3,419,792 bytes for MVB. Identical JPEG payloads have the
same JPEG decoding work; a container-size difference does not establish faster SD
parsing or playback. The firmware AVI reader remains unimplemented.

The separate chroma comparison uses 320×240, qscale 8, area scaling and 20 encoded
FPS (240 frames). Host FFmpeg decoding and FFprobe geometry/sampling/count checks
passed for 4:2:2 (3,545,712 bytes) and 4:4:4 (5,548,300 bytes). These are separate
20-FPS cases, so their total file sizes must not be compared directly with a
30-FPS file. The existing firmware safety profile accepts only 4:2:0; no wider
sampling support or physical performance advantage is claimed.

The production first-launch conversion default is now qscale 8/25 encoded FPS,
bicubic, baseline 4:2:0 and optimized Huffman tables; existing saved choices remain
unchanged. Its complete synthetic-source host artifact contains 300 frames over
12 seconds and 2444120 bytes. Explicit 30-FPS conversion remains available and is
tested separately. Default/optimized-Huffman decoding produces identical RGB hashes
over 82944000 bytes with FFmpeg and identical actual TJpgDec output for 360 paired
frames. The JPEGDEC fallback differs at 51253 pixels (0.1854%); this is disclosed
in the hardware report. One corresponding last frame from default and optimized
files produced matching active-Espressif displayed RGB565 CRC 526852766 on the
physical device. This single-frame proof is not claimed for all 360 frames.

Changing only Huffman coding on the physical 30-FPS bicubic/q8 baseline reduced
the MVB from 3327948 to 2932020 bytes, saving 395928 bytes (11.90%). That saving comes
from entropy coding; the previous pipeline already excluded audio, so audio-removal
savings remain zero. Real SD/decoder/render/USB measurements and native 304×224
versus full 320×240 performance are in
[REAL_HARDWARE_PERFORMANCE_REPORT.md](REAL_HARDWARE_PERFORMANCE_REPORT.md).
Remaining host-only rows above are not relabelled as physical benchmarks for
different prepared files.
Default contain conversion also pads already scaled 4:2:0 content to multiples of
eight when both requested bounds are divisible by eight. The active pixels and
aspect are preserved: 320×180 becomes 320×184 with exactly two black rows above/
below; 180×240 becomes 184×240 with two columns left/right. Nine pre-JPEG raw-RGB
oracles checked every content/border pixel. Crop and explicit nonaligned bounds
remain unchanged; the full 320×240 default artifact remains byte-identical. The
new 320×184 default 16:9 output has now completed a verified 472216-byte USB transfer
and physical 75-frame playback at 24.9950 actual FPS with target 25. All 15 sampled
pixels and a separate 58880-pixel region check matched. This is a 3-second
compatibility run; portrait padding remains host-only.
The tested original 640×360/30 FPS MP4 contains AAC and retained its source hash.
A separate temporary video-only remux produced byte-identical MVB output. The
prior converter already excluded audio, so the measured audio-removal saving is
0 bytes; no performance gain is attributed to removing audio that was already absent.
The selected production-default, audio and decoder/test results are portable in
[production-encoding-verification.json](evidence/production-encoding-verification.json).

## Reproduction and evidence

The saved matrix is under `.verification/session-2026-11-13/formats-30fps/`, including
`report.json`, exact command arguments/logs, JPEG and raw samples, matching-payload
AVI files, MVB files and raw sidecars. The source hash in the report is
`484143c9e5352efa1b64cf52924b41f17f739adc4d3022f4a81685a700a32672`.

```sh
cargo test --locked -p muse-video-core --test video_storage audio_is_excluded_without_changing_video_timing_or_source_files
python3 research/benchmark_playback.py --input /path/to/source.mp4 --output /new/comparison --raw-rgb565 --native-size --profiles q4-30fps-lanczos q6-30fps-lanczos q8-30fps-lanczos q10-30fps-area 288x216-30fps 256x192-30fps 240x180-30fps
```

For physical JPEG testing, add the explicitly selected `--port` and optionally
`--target-fps 30`. Source, encoded, requested and achieved rates stay separate;
the tool does not claim ESP32 timing from a simulator run. `--native-size` forwards
the CLI's `--display-native`, while the default requests an aspect-fitted full
320×240 destination. Neither mode changes the original source.

Display experiments can explicitly supply `--render-mode legacy|blocks|dma`,
`--big-endian` and `--tft-spi-hz 20000000|40000000|80000000` when using firmware/CLI
that implement these options. The tool records requested settings; the actual
display clock and DMA activity must be taken from device diagnostics. Isolated SD
read timing is used only when the device reports it. The older whole-frame fetch
timing remains separately named because it also includes parsing, CRC and JPEG
structure validation.
