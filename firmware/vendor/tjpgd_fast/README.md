# Pinned software TJpgDec comparison

Source: Espressif idf-extra-components commit
`1a02bef72a701d4d4d35865c7f006d41e6571da8`, component `esp_jpeg/tjpgd`.
Original files and their notices are retained under `upstream/`. SHA-256 hashes,
URLs and local modifications are recorded in `provenance.json`.

The adapted source preserves decoding arithmetic. Public decoder types,
results, configuration macros and entry points have unique names because the
classic ESP32 ROM decoder uses an incompatible older ABI. Nine explicit
allocation casts permit the C implementation to compile as C++ with the
existing global firmware preinclude. Standard fixed-width integer headers
replace the obsolete Windows typedef branch.

The custom configuration selects a 512-byte input buffer, RGB565 output,
FASTDECODE level 2 and saturation lookup. Decoder scaling/default Huffman-table
fallback are disabled; the existing TileRenderer handles live transformations.
The optional helper uses a reusable 10,240-byte pool and 320×16 RGB565 strip.
Current prepared 4:2:0 fixtures consumed 9,368 bytes; pool exhaustion for another
input is a checked prepare failure, not an allocation assumption.

Enable with `MVB_ENABLE_TJPGD_FAST`. `firmware/src/tjpgd_fast.cpp` compiles the
source only under that flag. The helper provides `TjpgdFast` and a portable
`Rgb565StripCollector`; no full framebuffer or per-frame application allocation
is introduced. Unsupported JPEG sampling is rejected explicitly.

Run `python3 tests/tjpgd_fast_test.py -v` for source-hash, edge/color, reuse,
failure and sanitizer checks. No host decoding result establishes ESP32 FPS.
