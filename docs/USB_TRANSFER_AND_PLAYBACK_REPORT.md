# USB transfer and playback investigation — 2026-11-12

This is the historical host-only investigation. Subsequent physical results on
2026-11-13 are documented in [REAL_HARDWARE_PERFORMANCE_REPORT.md](REAL_HARDWARE_PERFORMANCE_REPORT.md)
and [NATIVE_DESKTOP_USB_E2E.md](NATIVE_DESKTOP_USB_E2E.md): the real Linux desktop
transferred a complete 3,327,948-byte video to the installed microSD, verified its
size/SHA-256 and played it. The limitations below describe the earlier session.

**The source corrections have host verification, but USB transfer is not yet
qualified as fixed on the physical ESP32.** The session cannot open `/dev/ttyUSB0`:
the host exposes `ttyUSB0` in sysfs with device number `188:0`, while the restricted
session has no corresponding `/dev` node. Reconnection did not expose that node.
The captured connection failure is retained in
physical-connect.log (local-only evidence: `../.verification/usb-playback/physical-connect.log`).
No firmware responses, successful physical SD transfer, physical playback FPS or
display-quality comparison could be captured in this environment. No flash erase,
firmware installation, NVS write, card formatting or unrelated device-configuration
change was performed.

The intended workflow remains USB upload with the microSD card inside the ESP32.
Direct card export remains available, but it does not satisfy physical qualification
of that normal USB workflow.

## Observed failures and root-cause boundary

The actual serial-open attempt returned:

```json
{
  "error": {
    "code": "connection_failed",
    "details": "No such file or directory"
  },
  "message": "Could not open the selected connection. Check access permissions.",
  "ok": false
}
```

This describes the session's device-access failure, **not the cause of the owner's
repeated transfer errors**. The exact original desktop/firmware errors from those
transfers remain unavailable. Their hardware root cause is therefore unresolved.

The existing transfer was already a framed, checksummed, stop-and-wait protocol.
Investigation found specific implementation defects rather than a missing protocol.
Before changing the relevant firmware behavior, native tests reproduced:

- A conflicting chunk using the previous chunk's sequence was accepted with a
  binary ACK instead of returning `sequence_conflict`.
- An invalid recovery journal returned the generic `upload_rejected` error, hiding
  the actual journal failure.

The original implementation also permitted `select_video` to take SD ownership
while receiving an upload, and the host collapsed storage ACK failures into
`offset_mismatch`. Those paths have been corrected. These findings explain concrete
failure modes in the source; they do not establish which occurred on the owner's
board. Before-change evidence is retained in
regressions-before.log (local-only evidence: `../.verification/usb-playback/regressions-before.log`).

Further review found that Arduino `File.flush()` discarded the underlying
`fflush`/`fsync` error results, so the old checkpoint path could report success
without a checked filesystem sync. The firmware now checks both operations through
the mounted FAT VFS and records the last SD errno. Host driver drain calls could
also block beyond configured serial timeouts; the unbuffered serial adapter now
relies on checked device ACKs to confirm delivery. Recovery ownership is cleared
before a new upload identity is staged so a conflicting start cannot authorize
discarding a different file's journal. A restarted device's matching-sequence
`stale_session` rejection is reported even when its session ID has changed.

## Investigation of the requested transfer areas

| Area                               | Finding, implementation and qualification                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Serial configuration and baud   | Desktop and firmware use 115200 baud, 8 data bits, no parity, one stop bit. The host now sets those fields explicitly. No higher baud has been chosen without a physical test.                                                                                                                                                                                                                                                                                                                        |
| 2. Handshake and detection         | HELLO negotiates protocol v1 and establishes a nonzero session. Only the selected port is contacted. A new client nonce resets sequence receipts without treating arbitrary ports as ESP32 devices. Actual device detection is blocked by the missing session device node.                                                                                                                                                                                                                            |
| 3. Chunk size                      | One chunk remains in flight, capped at 1024 data bytes. The firmware advertises `chunk_bytes`, and the host respects smaller limits. This cap is host-tested; physical safe-size and throughput testing remains required.                                                                                                                                                                                                                                                                             |
| 4. Read/write timeouts             | The transport timeout changed from 100 to 250 ms. A 1024-byte chunk alone occupies roughly 92 ms at 115200/8N1, leaving little margin under the previous timeout. Buffered transports have a three-second write/flush deadline; the unbuffered serial adapter avoids potentially blocking driver drains and relies on device ACKs. Desktop responses use 700 ms and three total attempts; generic CLI responses default to three seconds. This timing analysis is not a captured hardware root cause. |
| 5. Flow control                    | Hardware flow control remains disabled. Stop-and-wait ACKs provide protocol backpressure while SD writes finish. No untested streaming window or baud increase was introduced.                                                                                                                                                                                                                                                                                                                        |
| 6. Sequence numbers                | Commands and chunks use the same increasing nonzero sequence space. The last exact chunk receipt is replayed without another SD write; conflicting or expired sequences are rejected.                                                                                                                                                                                                                                                                                                                 |
| 7. Integrity                       | COBS framing and envelope CRC32 separate binary chunks from control JSON. The file has header/index/frame CRCs plus whole-file SHA-256. Every JPEG is validated before publication.                                                                                                                                                                                                                                                                                                                   |
| 8. ACK/NACK                        | ACK fields are checked for length, transfer ID, accepted/checkpointed offset, status and reserved bytes. A storage NACK retrieves the firmware's specific transfer error. JSON rejections while awaiting a binary ACK preserve their error code.                                                                                                                                                                                                                                                      |
| 9. Retry logic                     | Missing responses retry the identical frame with the same sequence, bounded to three total attempts. Exact chunk receipts prevent duplicate SD writes after a lost ACK. Temporary partial-write interruptions continue the unwritten suffix rather than restarting the frame.                                                                                                                                                                                                                         |
| 10. Cancellation                   | Cancellation is checked while waiting for a chunk ACK. Every failed active upload requests device cancellation; if that cannot be confirmed, the original error is retained with that limitation. Explicit cancellation checkpoints the partial for safe same-file resume.                                                                                                                                                                                                                            |
| 11. ESP32 RAM                      | Transfer data is incremental; the firmware never loads the complete file. It retains a bounded wire frame/UART ring, queues, one compressed JPEG of at most 49,152 bytes, a 64-entry index cache and bounded receipts. Build RAM figures cover static sections only; actual initialized heap and peak transfer memory remain unmeasured. `get_serial_diagnostics` reports engine object bytes, while real telemetry reports free/minimum heap.                                                        |
| 12. Loss during SD writes          | Host backpressure prevents normal next-chunk data until an ACK. The serial task now drains RX between bounded TX writes instead of blocking receive service for a full telemetry frame. Its receive ring is 4096 bytes and overrun/queue counters are exposed. Whether loss actually occurs on this unit remains unmeasured.                                                                                                                                                                          |
| 13. SD/filesystem errors           | Start failures now identify unavailable SD, directory/open failure, insufficient space, malformed journal, conflicting recovery identity or missing partial. Open/read/write/flush/checkpoint/publish/hash errors preserve their stage, with the last SD errno available for diagnosis. Failed media is not published, and unrelated files are preserved. Real card fault/power-loss behavior remains untested; FAT flush/rename is not a transactional durability promise.                           |
| 14. Logging/telemetry interference | Periodic telemetry and frame events pause during receiving/resuming/verifying. Errors and requested status remain framed. ESP logging is disabled after setup. UART TX retains frame continuity across partial writes and gives replies priority over queued events. ROM startup text can still occur; COBS delimiters resynchronize afterward.                                                                                                                                                       |
| 15. Assumed immediate writes       | The original host used `write_all` and `flush`, so it did not simply equate one `write` call with a completed transfer. Its temporary-error handling and timeout margin were inadequate for robust continuation, and driver drain calls could block beyond the configured deadline. The corrected host tracks bytes accepted by the transport, completes the suffix/flush within a deadline, and advances progress only after a checked ACK.                                                          |

The wire contract and error semantics are described in
[SERIAL_PROTOCOL.md](SERIAL_PROTOCOL.md).

## Changes and design

The Rust serial actor remains the sole port owner. It rejects live controls and
device changes while an upload owns the connection instead of placing them behind
a potentially long upload. USB upload is now the primary transfer action and explains that the card stays
in the ESP32; direct-card export is a secondary option. The desktop clearly labels
an upload, displays the exact error code, and disables
playback/port-changing actions while keeping cancellation available. Existing Muse
Material 3 color roles, controls, progress feedback, light/dark themes, focus and
responsive layout are reused; no new visual identity or dependency was introduced.

The firmware pauses playback and owns SD access throughout receive/resume/verification.
`select_video` and destructive recovery discard cannot compete with that work.
Successful finish requires contiguous bytes, flushing/checkpointing, SHA-256,
container/index/frame validation and JPEG decode validation before publication.
The host additionally validates returned completion identity and byte counts.
Cancelled/incomplete media remains an identified partial rather than a playable
video. Successful publication removes app-owned upload recovery files only.

A reconnect to an active transfer of the same digest/size/version can take over a
receiving writer after checkpointing and revalidating its prefix; it need not wait
for the inactivity timeout. A different file returns `busy`. If the same file is
already resuming or verifying, the new client attaches to that work and waits for
its checked result instead of repeating data or finish commands. Duplicate offset
checks flush the writer before opening a FATFS reader, because reader file sizes
can be fixed at open; storage failures return a storage NACK rather than a false
conflict. A failure during the first checkpoint can still leave an unjournaled
partial requiring manual inspection. Such ambiguous media is retained and fails
closed; it is not automatically deleted.
Upload data, file readers, checkpoints and active-selection staging use checked
stdio on the existing `/sd` VFS. New staging files are created exclusively;
checkpoint acceptance requires successful `fflush` and `fsync`. Errors identify
open/read/write/flush/checkpoint/publish/hash stages and preserve underlying errno
for diagnosis. This adapter change keeps the same SD mount, pin configuration,
container and portable storage interface.

The native-size, unrotated renderer now passes RGB565 JPEG tiles directly to a
block display adapter where stride and clipping permit it. This removes repeated
pixel resampling/copying and per-row SPI transactions from the common native-size
path. General transforms retain the row renderer. Pixel equivalence has host golden
coverage; the real display/render-time improvement remains unmeasured.

`get_benchmark` records completed presentations over a run and average read,
decode and draw times. It avoids using a few sampled last-frame values as averages.
`set_decoder_options` allows bounded MCU batching and the previous row path to be
compared without an NVS change. `get_serial_diagnostics` exposes UART overruns/errors,
parser failures and queue drops independently of verbose logging.

## Quality/performance comparisons and default decision

The reproducible comparison tool is
[research/benchmark_playback.py](../research/benchmark_playback.py). Its candidate
matrix covers JPEG qscale 4, 6, 8, 10 and 12; 10, 15, 20 and 30 encoded FPS;
320×240 and 288×216 bounds; bicubic, area and Lanczos scaling. It validates each
`.mvb` with the production core/decoder, reports exact file and JPEG sizes, and
compares AVI container overhead using the same JPEG payloads. Host-only 4:2:2/4:4:4
comparisons do not widen the embedded v1 safety profile, which accepts 4:2:0.

Objective sample SSIM can identify detail/compression differences; it cannot prove
that two profiles look identical on a 2.8-inch physical screen. Native simulator
timings use the host CPU/filesystem and are never ESP32 decode, TFT or SD-card
performance measurements. Every report marks physical quality unevaluated until
that screen has been inspected.

The substantive host matrix completed **14 validated profiles across two 12-second
sources**: ten profiles on a detailed 640×480/30 FPS synthetic source with seeded
mild noise and a grid, and four profiles on the existing 320×180/15 FPS source.
Every `.mvb` passed production verification and full decode. The AVI comparison
demuxed the same JPEGs and proved payload identity by SHA-256. 4:2:2/4:4:4 outputs
passed FFmpeg decode and FFprobe format/dimension/frame-count checks on the host.
An additional explicit-tool-path smoke run also passed.

Selected detailed-source results illustrate the measured trade-offs:

| Profile                | qscale / encoded FPS / scaler | Encoded size | File bytes | Mean JPEG bytes | First-frame SSIM |
| ---------------------- | ----------------------------- | ------------ | ---------: | --------------: | ---------------: |
| Existing baseline      | 8 / 10 / bicubic              | 320×240      |  1,109,816 |         9,206.1 |         0.898072 |
| Best Quality candidate | 4 / 15 / Lanczos              | 320×240      |  2,972,972 |        16,474.5 |         0.944863 |
| q8 comparison          | 8 / 20 / area                 | 320×240      |  2,516,792 |        10,444.7 |         0.890756 |
| q8 scaler comparison   | 8 / 20 / Lanczos              | 320×240      |  2,279,372 |         9,455.6 |         0.898293 |
| Smooth candidate       | 10 / 20 / area                | 320×240      |  2,101,312 |         8,713.7 |         0.873530 |
| Compression comparison | 12 / 20 / area                | 320×240      |  1,785,112 |         7,396.1 |         0.855542 |
| Resolution comparison  | 8 / 20 / area                 | 288×216      |  1,780,256 |         7,375.8 |         0.870546 |

Against q8/20 FPS/area on that source, q10 reduced mean JPEG bytes by 16.6%,
q12 by 29.2%, and 288×216/q8 by 29.4%. q8/Lanczos reduced mean JPEG bytes by
9.5% with a slightly higher sample SSIM. These are source-specific encoded-size
and objective sample results, not measured decode-speed or screen-equivalence
claims. The first prepared frame and its qscale-2 Lanczos reference are sampled at
the same profile FPS and compared on an aspect-preserved, black-padded 320×240
canvas. SSIM does not measure whole-video temporal quality or RGB565 TFT output.

Full tables, commands and artifacts are retained in
detailed-source summary (local-only evidence: `../.verification/usb-playback/encoding-detailed/SUMMARY.md`),
wide-source summary (local-only evidence: `../.verification/usb-playback/encoding-wide/SUMMARY.md`), and
their report JSON files. All actual-device FPS/decode/draw/SD-read fields remain
unavailable. AVI byte-size differences do not establish a physical SD-read or
playback advantage; the embedded AVI alternative remains unimplemented.

**The existing default is retained: qscale 8, 10 FPS, up to 320×240, 4:2:0.**
No new device-optimal default is claimed. Best Quality, Balanced and Smooth
Playback are benchmark candidates, not physically qualified presets. The existing
higher-quality qscale choices remain available. Selecting a lower resolution,
lower quality or new default requires measured physical FPS and display inspection
on representative owner footage. Changing defaults from host-only file size or
synthetic SSIM would not satisfy that requirement.

## Verification evidence and limits

The before-change targeted run intentionally failed two regressions while two
existing cases passed. The updated native shared-engine suite passed **11 tests**,
including lost-ACK cancellation, checkpoint recovery, duplicate chunks, checksum
failure handling, wrong-digest rejection, recovery-journal ownership, immediate
same-file reconnect/takeover, playback controls and Linux serialport transport
through a host PTY. That PTY is not a
physical ESP32. The native build passed 500 transform golden cases plus bounded
protocol/timing and checked stdio sync failure tests.

The completed Rust workspace run passed 25 available tests; 12 native-dependent
tests were ignored in that run and then run separately: the 11 engine cases and
one desktop serial-actor upload/control/reconnect case all passed. The four-profile
CLI/simulator exercise transferred and verified prepared files from approximately
1.1 to 2.97 MB, played them through the shared engine, and cancelled/resumed the
baseline from a fresh client. Those are host-pipe/filesystem results, never physical
USB/SD/TFT results. Svelte checking reported zero errors and zero warnings; the
production frontend build passed. The ESP32 reference firmware build passed with
36,876 static RAM bytes and 639,146 flash bytes. The existing TFT_eSPI warning
reports that `TOUCH_CS` is undefined, so its touch functions are unavailable.
Static RAM excludes the allocated engine, task stacks, queues and peak initialized
heap. A targeted immediate reconnect
regression passed after the final metadata-validation change. The Linux Tauri debug
executable was rebuilt at
`target/debug/muse-video-bridge`. Rust formatting and Clippy with warnings denied
passed; the Node synchronization test script and frontend formatting check passed.
Browser integration could not start: Vite listen on `127.0.0.1:1420` failed with
`EPERM`, and the Rust browser harness listen on `127.0.0.1:1431` failed with
`PermissionDenied`/`Operation not permitted`. No browser integration cases or
manual light/dark/narrow/wide/focus workflow checks ran in this session. Logs are
kept under
.verification/usb-playback (local-only evidence: `../.verification/usb-playback`).

Address/undefined-behavior sanitizer work uses `ASAN_OPTIONS=detect_leaks=0` because
LeakSanitizer could not operate under this session's ptrace restrictions; this
does not constitute a passing leak check. PlatformIO's default home cache was not
writable, so a workspace-owned core/cache with links to existing pinned packages
was used for the build. No device was flashed as part of that build.

Linux host functionality is tested in this environment. Physical Linux USB/SD/TFT
workflows remain untested. The current Windows x64 desktop release and optional
static-CRT CLI were
cross-built for owner testing; neither has Windows runtime qualification. The
current combined test package is documented in [WINDOWS_TESTING.md](WINDOWS_TESTING.md). macOS is outside the current supported/tested scope.

## Remaining physical qualification and commands

First expose the actual `/dev/ttyUSB0` device to the testing session and close other
serial owners. Confirm that the installed firmware implements the documented v1
contract. Installing firmware, if necessary, is a separate device action and was
not performed here. Do not erase flash or alter unrelated configuration to obtain
test results.

Connect the actual desktop app, retain the SD card in the ESP32, upload a prepared
video, and record the application's exact result/diagnostics. Confirm completion
byte counts and SHA-256, wait for the selected file's validated playback state,
then visually inspect playback. Capture serial diagnostic counter changes before
and after the attempt and actual heap telemetry. The CLI uses the same Rust client
and can supplement that desktop workflow with reproducible logs:

```sh
cargo build --locked -p muse-video-cli
target/debug/muse-video-cli device --port /dev/ttyUSB0 --lang en --request '[{"method":"get_status"},{"method":"get_serial_diagnostics"}]'
target/debug/muse-video-cli device --port /dev/ttyUSB0 --lang en --upload /path/prepared/video.mvb --benchmark-seconds 15 --target-fps 30 --display-full --decoder-mcus 8
```

The benchmark pauses playback after measuring. Report encoded and requested FPS
alongside achieved FPS, whole-run averages and dropped/rate-skipped frames. Read
time includes frame fetching plus CRC/header validation rather than isolated SD
bus transfer time. Compare MCU limits 1, 4 and 8 and `--row-render` using matching
footage; inspect the actual TFT before drawing a quality-equivalence conclusion.

Exercise cancellation, confirm the device retained an incomplete checkpoint and
did not publish that media, then repeat the same file to resume from a fresh client:

```sh
target/debug/muse-video-cli device --port /dev/ttyUSB0 --lang en --upload /path/prepared/video.mvb --cancel-after-bytes 65536
target/debug/muse-video-cli device --port /dev/ttyUSB0 --lang en --upload /path/prepared/video.mvb
```

The first command should return `cancelled` with exit code 1. Also test an actual
interrupted connection, reconnect, and verify same-file recovery. Preserve logs and
existing recovery media on error; a journal/conflict error must not be answered by
automatically deleting unrelated or ambiguous data.

Run the matrix in a new output directory, first on the host and then with an
explicit physical port once accessible:

```sh
python3 research/benchmark_playback.py --input /path/representative.mp4 --output .verification/new-encoding-comparison
python3 research/benchmark_playback.py --input /path/representative.mp4 --output .verification/new-device-comparison --port /dev/ttyUSB0 --test-recovery
```

Use `COM...` and the platform's executable path on Windows. The tool does not flash,
format SD or replace unrelated videos. It cannot turn an inaccessible device or
host simulation into a physical success claim. Real end-to-end transfer,
interruption recovery, peak device memory, SD behavior, sustained achieved FPS and
perceived image quality remain the completion gate for this investigation.
