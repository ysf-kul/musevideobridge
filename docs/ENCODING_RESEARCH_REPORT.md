# Video encoding research — 2026-11-13

These results are host encoding, container validation and actual native decoder
measurements. They do not establish physical ESP32 FPS, TFT image integrity or
perceived quality. Physical timings belong in the hardware performance report.

The source is the retained 12-second synthetic detail-pattern MP4: 640×480,
30 FPS, H.264, no audio stream. Its SHA-256 remained
`484143c9e5352efa1b64cf52924b41f17f739adc4d3022f4a81685a700a32672`.
All candidates contain 360 independently encoded 320×240 video frames at 30 FPS.
The prepared RGB reference uses identical bicubic scaling, square pixels, full
range and BT.601 conversion. Source and encoded FPS are separate from actual
device playback FPS.

| Encoding | Quantization reference | Sampling factors Y/Cb/Cr | MVB bytes | Maximum JPEG bytes | Whole RGB PSNR dB | Parser mode |
| --- | --- | --- | ---: | ---: | ---: | --- |
| FFmpeg, default Huffman | FFmpeg q8 | 22/11/11 | 3,327,948 | 9,757 | 26.095311 | Default |
| FFmpeg, optimal Huffman | FFmpeg q8 | 22/11/11 | 2,932,020 | 8,657 | 26.095311 | Default |
| Standard libjpeg 4:2:0 | Exact FFmpeg q8 tables | 22/11/11 | 3,838,724 | 11,248 | 26.488486 | Default |
| Standard libjpeg 4:2:2 | Exact FFmpeg q8 tables | 21/11/11 | 5,434,608 | 15,862 | 31.144497 | Explicit research |
| Standard libjpeg 4:4:4 | Exact FFmpeg q8 tables | 11/11/11 | 7,587,496 | 22,213 | 32.174679 | Explicit research |
| Standard libjpeg 4:2:2 | Exact FFmpeg q10 tables | 21/11/11 | 4,657,704 | 13,612 | 30.188245 | Explicit research |
| Standard libjpeg 4:2:2 | Exact FFmpeg q12 tables | 21/11/11 | 4,095,232 | 11,958 | 29.335037 | Explicit research |

PSNR compares every RGB channel of all 360 frames against the identically
prepared source. It is an objective error measurement for this particular test
clip, rather than a promise about visible quality on a small display or other
videos. FFmpeg qscale is inverse quality, rather than a JPEG percentage. The
libjpeg candidates use the exact quantization tables extracted from FFmpeg;
`cjpeg -quality` was not substituted for qscale.

FFmpeg also produced valid 4:2:2 (22/12/12) and 4:4:4 (12/12/12) JPEG streams,
4,940,643 and 8,075,615 bytes respectively. These are legal JPEG layouts that
the tested JPEGDEC and TJpgDec decoders do not support. They are excluded from
hardware candidates; this is not a claim that those JPEGs are generally invalid.

## Huffman encoding result and decoder discrepancy

Optimal Huffman saves 395,928 MVB bytes, **11.90%**, without changing the complete
FFmpeg-decoded RGB stream: both SHA-256 hashes equal
`cfc87e9b09524a3e30d37b835315617f386aa970e8103bcc59ca6c484d86871d`
over 82,944,000 bytes. The default MVB reproduces the retained hardware baseline
byte-for-byte.

Actual host JPEGDEC output is not identical between those streams: 51,253 of
27,648,000 RGB565 pixels differ, across 221 of 360 frames. The maximum channel
difference is eight RGB565 quantization steps. Fresh versus reused decoder
instances match for both inputs, ruling out decoder reuse as the explanation
in this test. All pixels were drawn exactly once, and ASan/UBSan reported no
failure. The first-frame CRC is identical, so checking only frame zero would
have missed this difference.

The actual software TJpgDec helper produces identical RGB565 pixels across all
360 default/optimal pairs, including fresh versus reused instances. FFmpeg and
TJpgDec therefore support a JPEGDEC-specific decoding discrepancy among these
host implementations. No root cause inside JPEGDEC or equivalence of the
precompiled Espressif decoder is claimed. The active Espressif decoder needs
its own physical comparison before calling optimal Huffman pixel-identical on
the ESP32.

## Guarded standard-chroma research

The core Cargo feature `research-jpeg-chroma` and firmware macro
`MVB_RESEARCH_JPEG_CHROMA` are both **off by default**. They extend only
luminance sampling to 11/21/22, with Cb/Cr strictly 11. Component IDs,
quantization/Huffman selectors and canonical bounds, baseline scan structure,
dimensions, 48 KiB frame bounds, entropy framing, CRCs and container arithmetic
remain checked. Header/version/index layouts are unchanged. This is not a
public version-1 interoperability expansion or a change to normal conversion.

Default and guarded Rust tests check real standard-chroma fixtures and damaged
component IDs, sampling, DQT/DHT/SOS and entropy data. The equivalent native
firmware validation runs both modes under ASan/UBSan. The default still rejects
4:2:2/4:4:4, and neither mode accepts FFmpeg's unsupported layouts. Actual
JPEGDEC decoded every pixel once for all 360 standard libjpeg 4:2:0/4:2:2/4:4:4
frames and the additional q10/q12 4:2:2 streams under sanitizers. Actual software
TJpgDec additionally passed every frame of the q8 4:2:2/4:4:4 streams.

Q10 4:2:2 reduces size 14.30% relative to q8 4:2:2 and retains 4.09 dB above
the 4:2:0 q8 RGB reference measurement. Q12 removes a further 1,654 bytes from
the maximum compressed frame requirement, with 3.24 dB above that reference.
These smaller candidates are intended to test the physical prefetch RAM limit.
They are not chosen as defaults based on host image metrics alone.

Q10 cjpeg calls totalled 0.718 seconds on this Linux host; complete conversion
time was not instrumented in that initial run. Q12 RGB-source-to-JPEG-stream
conversion measured 0.813 seconds, including 0.653 seconds of cjpeg calls.
These exclude subsequent MVB and full-decode verification and are not firmware
decode times or comparable to the original application's complete conversion job.

## Reproduction and evidence

The complete initial report, commands, hashes, frames and native results are in
`.verification/session-2026-11-13/chroma-candidates-complete/report.json`.
Q10/q12 reports are in sibling `chroma-q10-422/report.json` and
`chroma-q12-422/report.json`; their verified MVBs are `container/video.mvb`.
The q8 guarded files are `libjpeg-422-research/video.mvb` and
`libjpeg-444-research/video.mvb` inside the complete-candidates directory.

Research uses the already installed FFmpeg/FFprobe, libjpeg `cjpeg`, Rust and
clang. No cjpeg dependency or new audio/format option is added to the desktop.
The original batch deliberately requires a default-mode packer so it cannot
mislabel guarded acceptance as production compatibility:

```sh
cargo build --locked -p muse-video-core --example pack_jpeg_research
python3 research/prepare_chroma_candidates.py --source PATH-TO-12-SECOND-SOURCE.mp4 --output NEW-OUTPUT-DIRECTORY
```

For the additional standard 4:2:2 candidates, build the guarded packer, then
pass the actual sanitized JPEGDEC verifier to the bounded preparation script:

```sh
cargo build --locked -p muse-video-core --example pack_jpeg_research --features research-jpeg-chroma
python3 research/prepare_standard_422.py --source PATH-TO-12-SECOND-SOURCE.mp4 --output NEW-OUTPUT-DIRECTORY --qscale 10 --decoder-verifier PATH-TO-SANITIZED-VERIFIER
```

`research/decode_jpeg_research.cpp` builds against the pinned installed JPEGDEC
with `-D__LINUX__ -DNO_SIMD`, the project's portable JPEG preinclude, and
ASan/UBSan. Adding `-DMVB_COMPARE_TJPGD_FAST -DMVB_ENABLE_TJPGD_FAST
-DMVB_RESEARCH_JPEG_CHROMA` and compiling `firmware/src/tjpgd_fast.cpp` instead
selects the actual software TJpgDec helper. One MJPEG argument checks coverage;
two arguments additionally compare each paired image and both decoder lifetimes.
The encoded/decoded image buffers are bounded to a few frames on the host;
neither research utility opens UART, flashes hardware, mounts a card or modifies
the source.

Checks performed for these changes: 14 core library tests in default mode,
the same 14 with the explicit feature, default/guarded firmware validation under
ASan/UBSan, default and guarded core Clippy with warnings denied, workspace Rust
format check, own C++ format check and Python syntax checks. Physical ESP32 and
Windows runtime testing were not performed by this encoding research task.

## Audio

All FFmpeg conversion/extraction/reference commands explicitly select video
and disable audio/subtitles/data. The existing ESP32 conversion already used
`-an` and the JPEG-only MVB container has no audio payload. These experiments
therefore remove no previously included audio and claim no audio-removal size
or timing saving. The original MP4 is never rewritten.
