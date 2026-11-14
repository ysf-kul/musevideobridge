# Paused frame integrity probe

The experimental command compares decoded pixels with the current TFT image:

```json
{"method":"probe_pipeline","stage":"frame_integrity","repetitions":1}
```

It pauses playback using the normal pipeline-probe behavior. It requires an
already displayed frame, native block rendering, zero rotation, prepared native
dimensions and a complete rectangle inside the 320×240 display. Repetitions are
bounded to one through four. Scaled, rotated, clipped or unsupported display
paths are rejected before comparison starts.

The portable `Painter` API supplies `begin_integrity_check()` and
`end_integrity_check()`. The default implementation is unavailable. An armed
implementation must make frame/row/block callbacks compare existing display
memory without writing it. The engine re-decodes the retained current JPEG and
always ends an armed check, including decoder or readback failure. The physical
adapter must verify actual coordinate coverage as well as pixel totals.

A complete result reports `pixels_checked`, `pixels_mismatched`, `expected_pixels`,
`frames_checked`, `read_us`, total `wall_us`, `region`, `complete` and `passed`.
Its scope is `uncounted_actual_TFT_readback`. Corrupt pixels produce a completed
result with `passed: false`; they are not repainted to hide the corruption.
Incomplete decoding/readback or invalid coverage produces
`integrity_check_failed`. This work never increments playback frame counts or
the accumulated playback pipeline profile and is never a video FPS measurement.

Host verification:

```sh
python3 tests/frame_integrity_test.py -v
```

The actual JPEGDEC codec passed two ASan/UBSan builds, covering separate and
coalesced record reads. Each build exercises both RGB565 byte orders, all 75,763
pixels of a 317×239 fixture, repeated detection of three deliberate corrupt
pixels, untouched pixels outside the video region, image/write-count/FPS/profile
preservation, failed read/end/coverage results and unsupported geometries or
painters. The host canvas is a test double; it does not establish physical TFT
integrity. Real controller readback results belong in
[REAL_HARDWARE_PERFORMANCE_REPORT.md](REAL_HARDWARE_PERFORMANCE_REPORT.md).
