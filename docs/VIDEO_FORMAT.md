# Video input, conversion and container

Implemented experimental container version 1, firmware 0.2.0, desktop 0.3.0. Exact
binary offsets are in [CONTAINER.md](CONTAINER.md), which remains a draft interoperability
specification. The Rust and C++ parsers implement those header/record/index layouts.

## Input and conversion

Local MP4 containing a decodable video stream is the supported input. MP4 is a container,
not a guarantee of a particular codec: installed FFmpeg must decode the selected codec.
Invalid/missing/nonlocal inputs, audio-only MP4, unsupported encoding/tool features and
corrupted media return errors. FFprobe reports source dimensions, display rotation, sample
aspect ratio, duration and rational FPS when available; unknown duration/FPS remain null.
No online conversion or ESP32 H.264 decoding is used. Audio is not included.

The converter normalizes display rotation and square pixels, scales within 320×240 and
preserves aspect by default. Crop mode fills the screen when explicitly selected. Output
is 8-bit baseline Huffman YCbCr 4:2:0 JPEG; independent frames include their own tables.
Rational CFR determines frame count, PTS and duration. CLI rates can be rational; desktop
encoding controls expose integer 1…30 FPS. Default is 10 FPS and FFmpeg qscale 8; qscale
2…31 uses **lower values for higher quality**. Selected options are recorded unchanged.

JPEGs stream directly from FFmpeg into one indexed file, without thousands of SD files.
Frame/index CRCs, exact file arithmetic, JPEG tables/structure and full FFmpeg decode are
checked before publishing `video.mvb` and its manifest. SHA-256 identifies the completed
file. Progress reports conversion then verification separately. Cancellation kills the
owned process and retains partial/failure metadata. A new retry job preserves old output.
The desktop poster before conversion is only a first-frame thumbnail; device preview uses
the selected prepared JPEG frame.

## Limits and layout

| Property | Implemented bound |
| --- | --- |
| Encoded dimensions | 1…320 width, 1…240 height |
| JPEG frame | At most 49,152 bytes |
| File | At most 2,147,483,647 bytes; below FAT32 4 GiB−1 |
| Encoding rate | Rational 1…30 FPS, numerator ≤30000, denominator ≤1001 |
| Frames | 1…1,000,000, also limited by checked file arithmetic |
| Header | 96 bytes, `MVBJPG1` plus NUL, version 1 |
| Record | 16-byte frame header + complete JPEG + 0…3 zero alignment bytes |
| Index | End-of-file table, 24-byte absolute offset/PTS/length/CRC entries |
| Device index cache | 64 entries / 1,536 bytes; no whole index in RAM |

Overhead is 40…43 bytes per frame plus the header. Size estimation reports a worst-case
bound from the frame cap, not a promised compression ratio. Complex/high-quality frames
may exceed 48 KiB: conversion fails with `frame_too_large` rather than silently changing
quality/FPS or dropping them. Use an explicitly selected lower quality after inspecting
the error. Partial media never becomes the playable library.

## Card layout and playback

```text
muse-video/
  <lowercase-SHA256>.mvb
  active.json
  active.previous-<unique>.json       # recognized previous selection, when replaced
  *.part                          # incomplete app-owned staging; not playable
```

`active.json` has schema_version 1, SHA-256 and the matching `<digest>.mvb` filename.
The converter's source/options/tool manifest stays in the desktop cache; the card needs
only the self-describing video and active selection. Do not manually rename a hashed
filename/selection independently. Firmware opens the active video or a compatible library
entry, validates incrementally and auto-plays. The SD card is the primary playback storage.

The engine reads/verifies one compressed frame before JPEGDEC draws clipped RGB565 tiles.
No full decoded framebuffer/PSRAM is required. Independent JPEGs allow indexed seeking
without decoding intervening frames. Requested FPS caps presentation while preserving the
encoded timeline; higher requested FPS cannot manufacture new frames, and lower requested
FPS intentionally skips source slots. Actual FPS counts completed timed draws. Rotation,
position and size do not alter the file or require reconversion.

## Compatibility and evidence

The custom format was selected provisionally for bounded parsing, integrity and simple
seek state. Standard MJPEG AVI remains a valid comparison candidate; identical JPEGs cost
the same decode work. No physical SD/ESP32 comparison has established that `.mvb` is faster.
It is not compatible with the older application's `.mvj`/serial protocol and is not a
standard desktop video-player format. Keep version checks; do not freeze interoperability
until actual decoder, long-file seek/startup, card and FPS qualification is complete.

Real host tests cover MP4/aspect/VFR/rotation, options, timestamps/indexing, corrupt and
oversized files, malformed JPEGs, cancellation, safe exports and real JPEGDEC through the
native engine. They do not prove visible TFT output or physical memory safety. See
[PHASE_3_REPORT.md](PHASE_3_REPORT.md) for final test results.
