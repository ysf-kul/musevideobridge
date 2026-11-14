# Optional classic ESP32 decoder candidate

This directory contains unmodified `espressif/esp_new_jpeg` version 1.0.2 files,
pinned to esp-adf-libs commit
`7162d32381d6e1f8c8cba2ee5edcbfe82bc964b1`. Exact source URLs, byte sizes and
SHA-256 hashes are recorded in `PROVENANCE.json`; retain `LICENSE` when copying
or redistributing this component. The license allows use on Espressif products.
The decoder implementation is supplied as an upstream ESP32 static archive.

The optional Muse adapter is `firmware/include/new_jpeg.hpp`. Enable it using
`MVB_ENABLE_NEW_JPEG` only when linking the supplied classic ESP32 archive.
It remains unavailable in ordinary host/non-ESP32 builds. No default decoder
replacement is implied by vendoring this candidate.

The adapter requests RGB565 little-endian or big-endian block output, reuses its
codec handle and aligned output allocation, and bounds queried output storage
to 320×16×2 bytes. It does not allocate a full RGB frame. Encoded dimensions
must be multiples of eight, at most 320×240. Unsupported dimensions and codec
errors fail explicitly; existing product logic decides which decoder to select.
Native and transformed output both pass through the existing `TileRenderer`.
The codec's color conversion is included in decode time, rather than reported
as an independently measured stage. Only additional renderer time is timed
by this adapter.

Upstream says internal decoder RAM is approximately 10 KB. Actual classic
ESP32 allocations, stability, decoding speed and complete playback FPS require
physical measurements. `UPSTREAM_README.md` performance tables are for ESP32-S2
and ESP32-S3; those results do not establish performance on the classic ESP32.
