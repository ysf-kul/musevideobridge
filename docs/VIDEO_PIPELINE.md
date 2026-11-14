# Conversion and transfer architecture

> Phase 1 architecture/evaluation retained for reference. Current implementation and deviations are documented in [PHASE_2_REPORT.md](PHASE_2_REPORT.md).
Status: proposed pipeline. The research script validates host encoding only.

## Encoding decision

Use independent **8-bit baseline Huffman JPEG, YCbCr 4:2:0**, normally up to
320×240, decoded to RGB565. Encode CFR; default proposal **10 FPS, qscale 8**.
Offer simple quality presets initially (compact q=12, balanced q=8, high q=4), with
an explicit maximum-frame check. FFmpeg qscale is inverse quality, not a percentage
and not interchangeable with a JPEG library's 0–100 quality setting.

Preserve aspect by encoding the fitted **active image** without baked-in black bars:
16:9 becomes 320×180; a 9:16 source becomes approximately 134×240 after even-pixel
rounding. The firmware supplies the black canvas and positioning. This saves decode/
draw work and makes live destination changes useful. Optionally offer center crop
to 320×240. Never silently stretch. Metadata records the mode and actual output size.

Cap any JPEG to the advertised device profile, initially **49,152 bytes (48 KiB)**.
The high-entropy host corpus exceeded this cap at q=4. Retry conversion at a lower
quality only with visible progress/explanation and record the effective quality.
Do not silently drop large frames; fail clearly if the selected supported bounds
cannot be achieved. The device may advertise a different measured bound later.

## Job lifecycle

1. Select a local file through a native dialog. Probe with FFprobe JSON, a video-only
   stream selection, duration/timebase/dimensions/SAR/rotation/color tags and bounded
   process time/output. MP4 is the supported product input; its extension alone is
   not evidence of a decodable video stream. Reject no-video, zero-length, corrupt,
   encrypted/DRM and incompatible streams with localized error codes.
2. Normalize the chosen stream. Apply display rotation metadata once, deinterlace
   only when detected/selected, use square output pixels, fit/crop and choose CFR.
   VFR inputs are resampled predictably. Validate rational FPS, quality, dimensions,
   maximum duration/output estimate and available cache space before starting.
3. Spawn trusted local FFmpeg without a shell. No network inputs are allowed by this
   local-file workflow. Restrict nested protocols as practical, bound resources, and
   preserve exit-code/stderr diagnostics separately from localized UI errors.
4. Consume a **stream of JPEG packets**, not permanent per-frame files. A small
   baseline-JPEG segment parser must respect segment lengths, entropy byte stuffing,
   restart markers and EOI; searching blindly for `FF D9` is insufficient. Alternatively
   demux a temporary MJPEG AVI if that is safer in the first slice. Benchmark parsing
   effort and intermediate disk use before choosing the extraction implementation.
5. Append framed JPEG records and spool fixed-size index entries to a temporary file.
   Compute frame CRC32 and whole-file SHA-256 incrementally. Do not store an unbounded
   index/video in RAM. On completion append index, patch header, and validate every
   offset, timestamp, JPEG mode/dimension/size and CRC. Decode all output frames on
   the desktop as the conversion validation gate, with cancellation/progress.
6. Publish the local completed artifact with no-clobber rename after flush. Retain
   a sidecar manifest containing schema version, output SHA-256, dimensions, frame
   count, rational rate, duration, quality, fit/crop, converter version and settings.
   Keep original paths/private titles out of on-card metadata by default.

Progress comes from FFmpeg's `-progress pipe:1` and actual processed output time.
Use a separate stream for JPEG bytes and diagnostics. Unknown duration requires
indeterminate progress, not a fabricated percentage. Cancelling stops the owned
process, marks the job cancelled and prevents publication of partial output.

The Phase 1 host command exercised this **filter family**, using FFmpeg n9.0.2:

```text
fps=10,scale=320:240:force_original_aspect_ratio=decrease:
force_divisible_by=2:reset_sar=1:out_range=full:out_color_matrix=bt601,
format=yuvj420p
```

The lines above describe one continuous filter expression. Encoding used `mjpeg`,
qscale 4/8/12, default Huffman tables and one encoder thread. Phase 2 probes tool
capabilities rather than assuming older FFmpeg versions accept `reset_sar`. An
alternative filter must explicitly account for input SAR before applying `setsar=1`;
simply resetting anamorphic metadata would distort the picture. SDR color conversion
needs test clips for full/limited range and BT.601/709. HDR requires an explicit
qualified tone-mapping path or a clear unsupported-input error; it is not validated
by this host experiment.

## Direct SD export — primary method

The app writes to an **existing mounted directory selected by the user**, not a raw
block device. It never partitions/formats cards or guesses a destination from a drive
letter. Explain that the selected folder should be on the intended card; a folder
dialog alone cannot conclusively identify removable media on every OS.

Proposed card layout:

```text
/muse-video/
  <sha256>.mvb             Completed container, content-addressed
  <sha256>.json            Optional nonessential conversion metadata
  active.json              Selected filename + SHA-256; small versioned record
  .<job-id>.part           Incomplete generated transfer; never playable
```

Validate container size below the conservative **2 GiB** implementation bound,
filesystem compatibility and free space including staging/metadata margin. Plan for
FAT32 (4 GB nominal card usually SDHC); the installed filesystem is unverified.
FAT32 itself permits files up to 4 GiB−1; the smaller limit avoids signed-offset
problems in candidate embedded APIs. Do not claim exFAT support until firmware
configuration and actual card tests establish it. Long filenames require qualified
LFN configuration; shorten generated names only with explicit collision handling.

Create a new app-owned staging file exclusively, stream the validated artifact,
flush/sync, read back SHA-256, validate its container, then publish without replacing
an existing file. If the same digest already exists, verify and reuse it. Prevent
path traversal/symlink escape from the selected directory; filenames derive from
digests rather than media titles. Recheck mount/device availability throughout.
Write selection metadata only after media publication. Updating `active.json` must
use a validated versioned temporary record and recoverable publication sequence,
preserving a prior valid selection if interrupted. Firmware treats selection as a
hint: it validates the referenced file and falls back deterministically if missing.

FAT rename/flush is **not** journaling or a power-loss guarantee. Never claim safe
removal just because copying finished. Offer OS-specific safe-removal instructions;
do not auto-unmount arbitrary disks. User inserts the prepared card; on startup or
idle mount the firmware selects and plays a valid container. Bad/partial media produces
an explicit error, and unrelated card files are never removed.

## Optional UART upload

UART upload sends the **same prepared container** to SD through the protocol in
[SERIAL_PROTOCOL.md](SERIAL_PROTOCOL.md). No MP4 streaming or real-time USB frame
feed is required. Pause/close playback's SD reader for baseline uploads; prioritize
control/status/cancel and display transfer progress. Concurrent SD writes/playback
are a later benchmarked capability. A 100 MiB video takes at least about 38 minutes
at 460800 baud before overhead, so direct card writing remains the default.

Use bounded acknowledged chunks, whole-file SHA-256 verification, `.part` staging,
offset-checked resume and no-clobber publication. Keep transfer metadata separate
from existing videos. A received chunk ACK is not a durable-storage claim; expose
received and checkpointed offsets separately. On disconnect/reboot resume only a
validated matching artifact and checkpoint. Corrupt/interrupted files remain clearly
incomplete; discarding them needs an explicit user action. Never erase the card or
overwrite arbitrary user paths to recover a transfer.
