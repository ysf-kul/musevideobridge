# USB transfer and batch queue repair — 0.4.1

The 0.4.0 production firmware ran at 115200 baud. Its 8N1 wire ceiling is
11,520 bytes/second before framing, ACKs, USB-driver latency and SD writes.
At that rate, large prepared MJPEG videos can take many minutes. This was a real
transport limit, not a progress-display problem.

The desktop also required one source selection/conversion/upload at a time,
hid prepared items in a collapsed section, and forgot registrations on restart.
Device restart restored only one SD video. Those behaviors made a multi-video
workflow cumbersome and lost the visible queue after a reset.

## Changes

- The production `cyd_optimized_input_aligned` firmware uses 921600 baud.
  ROM flashing remains at 115200. The desktop's existing discovery supports both
  old and new application rates; `cyd_optimized_input_aligned_115200` retains the
  original application rate for incompatible bridges/drivers.
- Select up to 12 MP4s using **Add videos**, then **Prepare all selected videos**.
  Conversion runs sequentially with real per-video progress. Select prepared
  entries and **Send selected over USB** or **Write selected videos to SD card**.
  Batch upload starts playback at its first selected video after readiness is
  confirmed. Existing queue entries/files are retained.
- Prepared registrations survive desktop restart in the local cache. Paths stay
  inside that cache; startup rechecks headers/indexes, and transfer/export and
  preview check actual media identity. Source videos are never modified.
- Uploads can reuse a hash-matching SD video without resending its bytes. Existing
  content is reread and verified before playback. Interrupted transfers resume
  from a verified checkpoint; explicit cancellation retains its existing cleanup
  behavior. A batch failure identifies the affected video and retains completed
  videos.
- ACK/session timeouts permit two bounded recovery attempts, using HELLO on the
  same selected handle and real checkpoint validation. Storage/integrity errors
  are reported immediately. The transfer inactivity window is 30 seconds, longer
  than the host's nine-second ACK retry window.
- Upload progress is limited to ten updates per second and includes measured
  acknowledged-byte throughput and estimated remaining transfer time. Verification
  and preparation retain their real measured/indeterminate stages.
- Queue order, selected position and loop mode are stored as two alternating
  CRC-protected 808-byte records on SD. An interrupted record write can fall back
  to the preceding generation. Unrecognized overwritten records are preserved.
  Mounted-card export writes the same format, so the device reads the exported
  queue on boot. The desktop always refreshes the authoritative device snapshot.
- Real hardware revealed an ESP32 panic during transfer checkpoints. Binary
  upload dispatch now bypasses the larger JSON/queue command stack frame. Hashing
  reuses its exclusively owned compressed slot instead of a 4 KiB stack buffer;
  queue persistence uses two bounded temporary 808-byte buffers only at state
  changes. Navigation stages position updates without a copied playlist. These
  changes leave the existing playback buffers, DMA and frame loop intact.
- Job controls refresh the actual native job state after creation, closing the
  gap between the IPC reply and the first progress event.
- Error codes are visible without opening a disclosure. SD write, flush,
  checkpoint and capacity failures have specific English/Turkish recovery text.
  Full diagnostics remain expandable; generic failure details open automatically.
  Queue controls remain usable after a playback error, so another item can be
  selected or the problem entry removed.

## Verification and limits

### Synchronization, playback and touch

The ESP32 owns the queue, current media, playback state, loop mode and preparation
stage. One desktop serial actor sends commands and adopts device-confirmed state;
Svelte does not maintain a competing playback clock or queue. Queue edits include
the expected queue revision, so stale edits fail and refresh. Session, state
revision and playhead generation guard against old USB events. HELLO/reconnect
reads the real snapshot instead of replaying old desktop intent. State-change
events are capped at 10 Hz and full telemetry is normally polled once per second.

Play Once traverses the remaining queue and stops at the end. Loop Current repeats
the selected item; Loop Queue advances and wraps to the first valid item. Manual
next/previous preserve play/pause intent and wrap only in Loop Queue. Missing media
is skipped; clearing the queue stops and shows **Waiting for video**. Removing a
queue entry retains its SD file. The queue holds up to 12 distinct video hashes.

Desktop and CYD progress come from the same engine: receiving counts contiguous
bytes written/acknowledged; verification/loading hash passes count actual bytes
read. Index/frame/decoder validation, publication/flush and stages without a
measurable denominator stay indeterminate. No percentage is simulated. A transfer
job stays active until the device confirms validated, loaded media.

Tapping the CYD reveals Previous, Play/Pause and Next in a compact strip for four
seconds. The first tap reveals controls; subsequent taps execute the engine's
real playback commands. Play/Pause reflects the actual state. Touch polling is
rate-limited, overlays redraw only on changes, and hiding restores video or the
paused frame. Production has no synthetic-touch command. Simulator touch tests
verify action/state logic; physical calibration and visual acceptance are separate.

Desktop batch controls reuse the existing Muse Material 3 tokens, icon family,
themes, focus styles and responsive layout. English/Turkish errors expose the
real code and practical recovery text while keeping extended diagnostics readable.

See [machine-readable evidence](evidence/usb-batch-transfer-verification.json)
for final check results. The tests exercise real FFmpeg conversion, both batch
paths, stored-file reuse, dropped ACK recovery, queue ordering and restart.
Frontend acceptance uses the production Rust backend and native C++ engine;
its transport and storage are isolated host fixtures.

Final results: **66 Rust tests passed**, including native engine/workflow tests;
**109 of 122 Python tests passed, 13 skipped** (PowerShell helper and optional
ThreadSanitizer checks). All six frontend tests passed; Svelte reported zero
errors/warnings. The production-backend browser workflow passed in 30.4 seconds.
Rust/frontend formatting and all-target Clippy with warnings denied passed.
Both firmware builds passed. Native Linux portable and both AppImage launch modes
rendered and closed cleanly; Windows package hashes/executable correspondence passed.

The standard and compatibility firmware and optimized Windows/Linux desktops are
build-verified. The final reference firmware was application-only flashed at
`0x10000` and hash-verified after preserving a verified full-device backup. No
flash erase, SD format or media deletion was performed.

The real Linux USB link transferred **1,539,876 new bytes in 53.474 seconds**:
**28,796.65 acknowledged bytes/second**. Verification took 14.521 seconds and the
whole upload/readiness workflow took 68.412 seconds. This is approximately 2.9×
the earlier independently measured 115200-baud rate of about 9.9 kB/s; the tests
used different files, so this is not a controlled same-file comparison or a
Windows speed guarantee. 921600 baud has an arithmetic 8N1 wire ceiling of
92,160 bytes/second; the observed speedup is not eightfold. Large queues can avoid
the UART bottleneck through mounted FAT32 export.

Production-backend hardware tests exercise batch conversion/upload, selection,
pause, next/previous, reorder, removal/restoration, all three modes, and actual
device restart/reconciliation. A separate new-payload batch transferred 42,816
and 43,056 bytes, with real intermediate acknowledged-byte progress, verification,
loading and readiness. These are actual SD writes, not stored-file reuse.
The final 24-second-fixture batch transferred **171,936 new bytes per video** and
passed the complete queue/mode/restart acceptance with no accepted touches. Its
measured transfer rates were 16.8 and 12.9 kB/s, illustrating driver/retry/workload
variation rather than a universal 28.8 kB/s promise.
Host integration/browser tests also exercise mounted-card batch export and the
shared queue record; moving an exported FAT32 card into the CYD is unverified.

Early candidate images failed at transfer checkpoints with an ESP32 panic
(`reset_reason = 4`). The smaller binary-dispatch/hash/queue stack described above
corrected that observed failure; the final new-payload transfer completed. Long
serial benchmarks also recorded invalid frames, retries and two exhausted command
timeouts. These are retained in the evidence, not reported as clean USB operation.
A short six-second fixture completed its batch but a later queue seek arrived during
auto-advance after a serial delay (`no_video`); the acceptance repeat uses longer
fixtures. The user’s original Windows error was only “failed,” so its exact cause
cannot be established from that report.

Playback regression details are in the evidence below. Completed-draw FPS excludes
hashing/loading, inter-clip setup and diagnostic readback. The reference is the
same 320×240, 30-FPS media used for earlier qualification. Touch-affected clips and
failed benchmark commands are excluded from idle performance qualification. The
selected direct-DMA renderer, warmup, prefetch and frame scheduling remain intact;
queue writes happen at state changes, never per frame.

Across **28 contact-free completed clips**, the repaired firmware drew **10,065
frames in 336.006 active seconds: 29.9548 FPS**. The first series contributed 13
clips at 29.9931 FPS; the fresh readback series contributed 15 at 29.9216 FPS.
The earlier qualified renderer measured 29.7992 FPS, and 0.4.0 measured 29.7310
FPS. This reference workload shows no playback degradation. Inter-clip setup,
loading, hash verification and diagnostic readback are outside active playback.

All **15 new full-region TFT-memory checks** passed: 1,152,000 pixels checked,
zero mismatches, zero DMA ownership errors. Qualified runs recorded zero sampled
or accepted touches and zero application allocations per frame. The minimum
Arduino loop stack margin was 1,664 bytes. The heap low-water mark was 27,848
bytes in the first series and 17,576 including separate diagnostic readback in
the fresh series. Failed commands and the contact-bearing tail of the earlier
series are retained as exclusions. These numbers do not guarantee every video,
card or Windows driver, or establish physical touch/visual acceptance.

Physical touchscreen button actions, panel calibration and display appearance
remain **unverified**, as authorized by the user. The physical Windows COM/USB
workflow, Windows 10/11 interactive acceptance, clean DEB/RPM installation, all
power-loss/card-removal/bad-card cases, and moved-card export remain unverified.
Paused state and seek position are not persisted; transitions rehash media and
may show honest preparation gaps. The release is a **pre-release** with these
limits, not a claim that every Windows USB or SD failure is diagnosed.

## Use the repair

Install the 0.4.1 desktop and matching firmware. **Updating the desktop alone
cannot change 0.4.0 firmware's fixed UART rate.** Preserve and verify a full
device flash backup before flashing, following [INSTALL.md](INSTALL.md).
Build the standard firmware with:

```sh
python -m platformio run -d firmware -e cyd_optimized_input_aligned
```

For a bridge that requires the old rate, build the compatibility environment
instead. Keep the FAT32 microSD inside the CYD for USB transfer; use safe ejection
when moving a card between a computer and the CYD. No operation formats the card.

Automated verification commands remain those in [README.md](../README.md).
The explicit real-device runner is:

```sh
cargo run --locked -p muse-video-desktop --example queue_hardware -- /dev/ttyUSB0 red.mp4 blue.mp4 new-evidence.jsonl --expect-no-touch
```

Only run it against the intended connected CYD. It does not flash, format or
delete media. Real throughput is reported in each upload's `host_transfer` data;
reused media reports zero `newly_confirmed_bytes` and cannot qualify link speed.
