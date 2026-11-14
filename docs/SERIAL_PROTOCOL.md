# USB Serial protocol — implemented version 1

The queue firmware extension (firmware 0.3.0) retains v1 framing and adds a
`playback_queue` HELLO capability and `queue_capacity`. Clients must refresh device
state after HELLO rather than replay previous queue or loop settings. Details and
progress measurements are in [QUEUE_AND_PLAYBACK.md](QUEUE_AND_PLAYBACK.md).

| Method | Parameters / result |
| --- | --- |
| `get_playback_state` | Device-owned queue, IDs, position, playback/mode/preparation, revisions/generation |
| `set_queue` | `ids` array (0..12 unique verified-media hashes), `expected_queue_revision`; small revision receipt |
| `select_video` | `video_id` selects an existing queue entry; legacy `sha256` selection remains available |
| `next_video`, `previous_video` | Navigate with device-owned order and mode, preserving paused/running intent |
| `set_loop_mode` | `mode`: `play_once`, `loop_current`, or `loop_queue` |

`playback_state` events contain a `playback` snapshot; order is omitted when
unchanged. `preparation` events contain actual stage and nullable integer `percent`.
`get_status`, telemetry, and `upload_status` also expose `preparation`. A successful
upload publication can precede loading completion; clients wait for loaded playback
state and matching content ID before reporting readiness. `queue_conflict` means
refresh and review the new order; never overwrite blindly. Queue/mode are volatile
device session state, reconstructed from actual boot state after restart.

Historical Phase 2/3 baseline: desktop 0.3.0 / firmware 0.2.0. The Phase 2 command contract is preserved
and integrated by Phase 3, with reliability corrections dated 2026-11-12. The older
Muse application's wire protocol is incompatible. The corrections have host
regression coverage; the native Linux desktop workflow completed a 3327948-byte
115200-baud upload, exact stored-file size/SHA-256 verification and playback on the
connected CYD. See the [current physical investigation](REAL_HARDWARE_PERFORMANCE_REPORT.md)
for exact measurements and limitations; the [earlier investigation](USB_TRANSFER_AND_PLAYBACK_REPORT.md)
preserves the previous state.
UART uses 8N1 with no hardware flow control. The selected-port client tries 115200,
then 460800, 921600, 2000000 and 3000000 only when HELLO receives no matching reply.
It keeps the same port open with RTS/DTR released. The first selected rate retains three attempts with
a three-second ACK deadline; later fallback candidates use two attempts with a
1.5-second deadline. The same HELLO envelope is retried within each candidate.
Normal three-attempt/three-second ACK behavior is restored after connection.
Exhausting all five silent candidates uses eleven HELLO transmissions and a nominal
21 seconds of ACK deadlines, plus bounded transport/driver scheduling time. Explicit
faster baud selects only that candidate and retains the initial three-second / three-
attempt startup margin. Values outside this five-rate set are rejected before any
port is opened. Driver,
valid device and JSON errors remain errors; malformed/CRC-invalid input is counted
and resynchronized. Diagnostics identify the configured baud and attempted candidates,
with per-candidate elapsed time, RX/TX/retry/invalid-frame deltas and last error.
These are connection mechanics, not hardware throughput qualification. Firmware uses
a compile-time UART baud; physical stable-rate results belong in the real-device report.
No GPIO, firmware-flashing, NVS-write, arbitrary-file-path, format or shell command is exposed.

The 2000000/3000000 entries are implemented qualification candidates, not verified
physical throughput or Windows driver guarantees. Read-only inspection of the
installed Linux CH341 module and upstream driver confirms nominally exact divisors:

| Requested baud | CH341 clock / factor / divisor | Encoded divisor register | Physical qualification |
| ---: | --- | --- | --- |
| 2000000 | 48 MHz / 8 / 3 | `0xfd03` | Pending bridge/UART transfer test |
| 3000000 | 48 MHz / 8 / 2 | `0xfe03` | Pending bridge/UART transfer test |

The driver maximum is 3000000; higher values can clamp, so they are not client
candidates. Its optional transmit-buffer bit depends on the USB device's internal
CH341 version, rather than sysfs `bcdDevice`. A reported termios speed is a requested
configuration, not a measured UART wire clock. The inspected installed module is
`/lib/modules/7.3.3-1-cachyos/kernel/drivers/usb/serial/ch341.ko.zst`, srcversion
`866E3346B62D966038C1184`, decompressed SHA-256
`056b024f1e5b140fa45529ab1ef2f0da960c4627d735abf94de3113355fc6215`.
Its inlined baud-setting code contains the 48 MHz clock and 3 Mbaud ceiling. The
[upstream v6.18 source](https://github.com/torvalds/linux/blob/v6.18/drivers/usb/serial/ch341.c#L154)
documents the divisor formula/range (source SHA-256
`f1cc643917dc56b37dfd6714c6df2c6f4fa7469f1c5efe902850b5292e820a0e`).
This source/divisor evidence does not establish the connected bridge's oscillator
accuracy, VM scheduling reliability or stable large-file throughput.

## Binary framing

COBS encoding plus a zero delimiter resynchronizes after garbage/ROM logs. CRC32
covers the decoded header/payload. Unsupported flags, oversize packets and invalid
CRC are rejected before JSON. CRC protects corruption, not authenticated access.

Decoded frame: **16-byte header + payload + 4-byte CRC32**, little-endian.

|    Offset | Type         | Field                                                                |
| --------: | ------------ | -------------------------------------------------------------------- |
|         0 | u8           | Protocol major = 1                                                   |
|         1 | u8           | Kind: command=1, response=2, event=3, upload_chunk=16, upload_ack=17 |
|         2 | u16          | Flags = 0 in v1                                                      |
|         4 | u32          | Session ID; 0 only for initial HELLO                                 |
|         8 | u32          | Sequence ID; response echoes its command/chunk                       |
|        12 | u16          | Payload length, at most 2048 bytes                                   |
|        14 | u16          | Reserved = 0                                                         |
|        16 | byte[length] | JSON UTF-8 or binary upload payload                                  |
| 16+length | u32          | CRC-32/ISO-HDLC of header + payload                                  |

The CRC parameters match [CONTAINER.md](CONTAINER.md). Maximum decoded size is
2068 bytes; conservative maximum COBS frame including delimiter is **2078 bytes**.
The firmware parser uses a fixed 2078-byte frame buffer; the UART receive ring is
4096 bytes. Discard oversize input until the next delimiter,
and time out incomplete frames after two seconds. Unknown version/flags/length/
CRC/session must be rejected before parsing a payload. No unbounded string, nesting,
queue or allocation. JSON depth ≤4; method/code strings ≤48 ASCII bytes; values are
strictly typed. Unknown optional response fields can be ignored, unknown commands
receive `unsupported_command`. Invalid wire frames are counted/rate-limited, not
echoed back recursively as diagnostics.

## Binary upload payloads

Baseline has **one chunk in flight**, at most **1024 data bytes**. This limit has
host regression coverage and has not yet been qualified on the physical board.
`begin_upload`/`upload_status` advertise `chunk_bytes`; the client uses that limit
up to the v1 ceiling of 1024. COBS/CRC protects each envelope; whole-file SHA-256
protects publication. Chunk payload:

| Offset | Type         | Field                    |
| -----: | ------------ | ------------------------ |
|      0 | u32          | Transfer ID              |
|      4 | u32          | File offset              |
|      8 | u16          | Data bytes, 1…1024       |
|     10 | u16          | Reserved = 0             |
|     12 | byte[length] | Prepared-container bytes |

Binary ACK payload is transfer ID u32, accepted offset u32, checkpointed offset u32,
status u16 (0=accepted, 1=duplicate, 2=offset_mismatch, 3=storage_error), reserved u16.
An offset means the exclusive end of contiguous bytes. Check expected offset and
file size before writing. An identical retransmission at a previously accepted
offset is acknowledged; conflicting bytes are rejected, never appended twice.

Chunks share the session's increasing, nonzero sequence space with commands. The
firmware retains the latest chunk's sequence, payload CRC and exact 16-byte ACK.
An identical same-sequence retry replays that receipt without another SD write;
conflicting data at that sequence returns a JSON `sequence_conflict` response.
Expired sequences return `stale_sequence`. An offset-based duplicate with a new
sequence is checked against stored bytes and returns status 1 only when identical.
The client recognizes JSON command errors while awaiting a binary ACK and preserves
their code. Storage ACK status 3 causes an `upload_status` request for the specific
SD failure; it is not reported as an offset error.
If a CRC-checked negative `stale_session` reply matches the pending sequence but
uses the device's new session after a restart, the client reports that reset.
Positive replies and ACKs are never accepted across mismatched sessions.

## Handshake and requests

```json
{
  "method": "hello",
  "protocol_min": 1,
  "protocol_max": 1,
  "client_nonce": "unique-client-lifetime"
}
```

Send HELLO with envelope session 0, nonzero sequence. `client_nonce` is a nonempty
UTF-8 string shorter than 80 bytes, fresh for a new client lifetime. Exact HELLO
retries reuse it. A different nonce generates a new nonzero device session and
clears receipts; playback and transfer state survive this handshake. Reusing the
same nonce does not reset sequencing. The Rust client uses process/time identity.
Nonces separate accidental stale traffic; they do not authenticate the connection.

Replies are `{ "ok": true, "result": {...} }` or
`{ "ok": false, "error": { "code": "...", "details": "..." } }`.
All methods below accept ASCII uppercase aliases. A request has a single in-flight
sequence; use at most three total transmission attempts for a missing response.
Eight compact command receipts
of at most 512 bytes prevent repeated mutations and replay matching compact errors.
Large status/HELLO replies may be regenerated. A conflicting receipt returns
`sequence_conflict`; expired sequences return `stale_sequence`. Sequence wrap requires
a new client/HELLO. Disconnected sessions return `stale_session`.

| Method                              | Required parameters / result                                                                                                                                                   |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `hello`                             | Protocol range and client nonce; configured board/controller, firmware, canvas, capabilities, hardware snapshot                                                                |
| `get_status`                        | Playback/transform/SD state, frame and timing measurements                                                                                                                     |
| `get_telemetry`, `get_info`, `ping` | Same status plus nullable hardware measurements; no paginated diagnostics yet                                                                                                  |
| `get_benchmark`                     | Accumulated completed-frame count, elapsed time, achieved FPS, average read/decode/draw times, dropped/rate-skipped frames and decoder/render options                          |
| `get_serial_diagnostics`            | UART overruns/errors, invalid frames, inbound/reply/event queue drops, baud/ring size, last SD errno and engine object bytes; simulator marks hardware diagnostics unavailable |
| `set_decoder_options`               | Loaded-video benchmark control: integer `max_mcus` 1…32 and Boolean `native_blocks`; rejected while storage is busy; no persistent configuration write                         |
| `subscribe`                         | `telemetry_interval_ms`: integer 500…10000, default 1000; frame events fixed at 100 ms                                                                                         |
| `play`, `resume`                    | Resume; start at zero when stopped/ended                                                                                                                                       |
| `pause`                             | Freeze clock and last decoded frame                                                                                                                                            |
| `stop`                              | Stop clock, clear display, zero playhead                                                                                                                                       |
| `restart`                           | Reset counters/playhead and start                                                                                                                                              |
| `seek`                              | Nonnegative integer `position_us`, clamped to an encoded frame                                                                                                                 |
| `set_position`                      | Integers `x`: −640…640, `y`: −480…480                                                                                                                                          |
| `set_size`                          | Integers `width`: 1…640, `height`: 1…480; optional Boolean `lock_aspect`                                                                                                       |
| `set_scale`                         | Finite numeric `scale`: 0.01…8 relative to encoded, rotated image; resulting rectangle must satisfy size bounds                                                                |
| `set_rotation`                      | Integer `rotation_deg`: 0, 90, 180, 270; aspect-locked quarter-turn swaps dimensions around center                                                                             |
| `set_transform`                     | Atomic x/y/width/height/rotation; optional aspect lock                                                                                                                         |
| `set_fps`, `set_target_fps`         | Rational `numerator`, `denominator`: 1…30 FPS, numerator ≤30000, denominator ≤1001                                                                                             |
| `select_video`                      | Lowercase 64-character `sha256`; loads `/muse-video/<digest>.mvb`, validates asynchronously; returns `busy` during transfer                                                    |
| `begin_upload`                      | Digest, integer `size` within profile, `container_version`: 1                                                                                                                  |
| `upload_status`                     | Current transfer ID, digest, advertised chunk limit, accepted/checkpointed offset, verified bytes, size, state and error; a supplied transfer ID must match                    |
| `cancel_upload`                     | Current nonzero `transfer_id`; flush/checkpoint and retain partial                                                                                                             |
| `finish_upload`                     | Current transfer ID; complete contiguous bytes required, returns verification state                                                                                            |
| `discard_upload`                    | Current identified app-owned transfer ID and explicit `confirmed`: true; disabled while receiving/resuming/verifying/finalizing                                                |

Invalid/out-of-range parameters return errors. Aspect-locked width/height must agree
within one pixel with the rotated encoded aspect. Commands require a validated loaded
video where relevant; otherwise `no_video`. Controls are consumed by the storage/display
owner between engine steps. Reply revision identifies accepted state; subsequent frame
and status events identify the applied frame. No arbitrary angle, `list_videos`, saved
NVS settings, GPIO, filesystem path or firmware flashing command exists.

The explicit Phase 2 English waiting-screen requirement supersedes the earlier firmware
Turkish-default suggestion. Device `set_locale` returns `unsupported_command`. Desktop
application messages remain Turkish by default with English available through catalogs.

## Upload state machine

`none → receiving → verifying → complete`, with `resuming`, `cancelled` and `error`
states. Binary chunks/ACKs match the binary chunk layout above: ≤1024 data
bytes, envelope sequence/CRC, transfer ID, contiguous offset, accepted and durable
checkpoint offsets. Identical duplicate bytes receive status 1; conflicting bytes or
out-of-order offsets receive status 2. No chunk gets appended twice.

A first checkpoint and additional checkpoints at 64 KiB progress boundaries append
96-byte records to `/muse-video/.upload-<digest>.meta`:

| Offset | Content                            |
| -----: | ---------------------------------- |
|      0 | Eight bytes `MVBUP1\0\0`           |
|      8 | 64 ASCII lowercase digest bytes    |
|     72 | u64 expected file size             |
|     80 | u32 checkpointed contiguous offset |
|     84 | u32 prefix CRC-32/ISO-HDLC         |
|     88 | u32 CRC32 of preceding 88 bytes    |
|     92 | Four zero bytes                    |

Partial path is `.upload-<digest>.part`. Budget includes remaining file bytes,
checkpoint records and 65 KiB filesystem margin. Cancelling without new bytes does
not append another checkpoint. Journals are bounded to 3,200,000 bytes. On resume,
validate identity, journal/partial sizes and prefix CRC incrementally, then accept
only the checkpointed offset. The Rust client waits for `receiving` before sending. Same-file reconnects
(digest, size and container version must match) can checkpoint/close an active
receiving writer and revalidate the prefix immediately. A different active file
returns `busy`. Reconnecting while the same file is already resuming or verifying
returns that state and transfer identity; the client attaches to the existing work,
and does not resend bytes or a second finish command while verification is active.
Corrupt/ambiguous journals fail closed and remain available for manual inspection;
they are never automatically removed to make room for another upload.

Recovery journals are isolated by SHA-256, so cancelled transfers for different
videos can coexist. An existing per-digest journal takes precedence. If none exists,
the firmware validates the legacy `/muse-video/.upload.meta`: a matching digest and
size resumes in place; a valid different digest is retained untouched while the
new upload creates its own journal. An invalid legacy journal still fails closed
when no per-digest journal exists. A matching digest with conflicting size, or a
partial without an identifying journal, also fails closed. Cancellation appends
only to the current journal; completion and explicitly confirmed discard remove
only that identified transfer's recovery files.

Transfer-start errors distinguish `invalid_parameter`, `busy`, `sd_unavailable`,
`sd_directory_failed`, `sd_open_failed`, `upload_journal_invalid`, `upload_conflict`,
`partial_unavailable` and `insufficient_space`. Data writes report `sd_write_failed`;
flush/checkpoint failures report `sd_flush_failed`/`sd_checkpoint_failed`.
Verification distinguishes `sd_read_failed`, `hash_failed`, `seek_failed`,
`digest_mismatch` and container/JPEG validation errors; publication can report
`sd_publish_failed` or `selection_failed`. Existing recovery files and
unrelated videos are preserved after failure. The desktop requests cancellation on
any failed active transfer and retains the original error if cancellation cannot
be confirmed. A transport-error cancellation preserves checkpoint recovery. For
an explicit user cancellation, the shared desktop/CLI upload client requires a
confirmed `cancel_upload` result with the matching transfer ID before issuing
`discard_upload` for that same identified upload. Successful discard removes only
its partial/journal; unrelated recovery and completed videos remain untouched.
Unconfirmed or mismatching cancellation never authorizes cleanup.

A six-second control-contact timeout checkpoints/stops an active upload; offline
validated SD playback continues. Explicit cancellation and host process restart are
covered by native tests. Sudden card power loss remains physically untested. FAT flush
and rename cannot promise transactional power-loss durability.

Finish hashes the complete file, validates the index and CRCs and decodes every JPEG
without drawing before publication. Hash/index work advances in bounded steps; GET_STATUS
and heartbeat remain serviceable. Matching existing completed media is verified/reused.
Staging is published without replacing another video; active selection backs up recognized
prior metadata. Unrecognized selection files are preserved and cause `selection_failed`.
Only completed media is considered for auto playback. A cancelled partial is not playable.
Before reporting success, the desktop checks the returned transfer ID, total size,
accepted/checkpointed/verified byte counts and an exact SHA-256 string matching the
prepared file. Missing, null, wrongly typed or mismatching digests fail verification.

The ESP32 storage adapter uses checked stdio over the already mounted `/sd` FAT
VFS for upload data, readers, checkpoints and selection staging. Checkpoints require
successful `fflush` and `fsync`; Arduino `File.flush()` did not expose either return
code. New staging files use exclusive creation. A failed start cannot acquire
discard ownership of another transfer's journal. Neither successful fsync nor FAT
rename guarantees atomic recovery from sudden power loss.

## Events and measurements

Default telemetry **1 Hz**, configurable 0.1…2 Hz. Frame events ≤10 Hz. ESP32 has separate
bounded reply and event queues so replies/ACKs take priority; excess events are discarded.
Periodic telemetry and frame events pause while an upload is receiving, resuming
or verifying. Status requests and error responses remain available. The serial task
drains RX between bounded writes limited to available TX space and preserves each
COBS frame across partial writes. ESP logging is disabled after setup so diagnostics
use framed responses rather than raw text on the application channel; ROM startup
text may still occur and framing resynchronizes afterward.
Connection freshness expires after six seconds without a valid command/chunk/retry. The
frontend in Phase 3 must send periodic ping/status requests while showing a connection.

`frame_index` and `frame_pts_us` identify the last successfully decoded/drawn frame;
`position_us` is the media clock. `state_revision` and `playhead_generation` reconcile
preview changes. `actual_fps` counts completed timed presentations over two seconds;
it is null before the first window, zero when not playing. Redrawing a paused frame
or seeking does not inflate actual FPS. `dropped_frames` counts superseded eligible
frames; `rate_skipped_frames` separately counts intentional target-FPS sampling.

`sd_read_bytes` and `read_us` describe the last compressed frame read/verification;
`sd_read_bytes_per_second` is observed host/storage read-path throughput, not a raw
card benchmark. `decode_us` excludes draw callbacks; `draw_us` measures their work.
Hardware keys/nullable semantics are documented in the [Phase 2 report](PHASE_2_REPORT.md).
Native telemetry explicitly sets `simulator: true` and hardware measurements null.

`get_benchmark` averages every counted presentation since playback start/restart or
resume rather than sampling only the last frame. Read time includes the bounded
frame fetch, CRC and JPEG header checks; it is not an isolated SD bus benchmark. Drop/rate-skip counters retain the scheduler
semantics described above; restarting before a comparison resets them.
Decode time excludes display callbacks, and draw time covers their rendering work.
Native-size, unrotated RGB565 tiles can use a block display path without resampling;
scaled/rotated/clipped cases retain the general renderer as needed. `native_blocks`
and `max_mcus` let hardware tests compare the previous row path and decoder batches.

## Desktop actor and UI integration

The actor owns one port, a 16-command queue and finite read timeouts. `connect`
queues the selected port and returns immediately; `connecting`, `connected` and
failure events report its progress. The ten-second command-response wait does not
apply to Connect, so discovery through the higher-rate candidates does not require
an artificial thirty-second Connect caller timeout. The serial candidate sequence
and subsequent HELLO/status requests retain their own bounded deadlines.
Request timeout is three seconds with at most three total identical transmission attempts; the public actor command wait
is bounded to 10 seconds. Full status/heartbeat is requested approximately once per
second and immediately after controls. Frame events are polled with a bounded budget;
The transport read/write timeout is 250 ms; a bounded read can therefore add that
latency. The generic CLI client defaults to a three-second response timeout.
Serial writes retain confirmed partial-write counts and continue the remaining suffix
after temporary timeout/would-block/interrupted errors, within a three-second
write/flush deadline for a buffered transport. The serial adapter is unbuffered and
does not call potentially blocking `tcdrain`/`FlushFileBuffers` on every frame;
matching device ACKs establish delivery rather than a host driver drain. Flushing
transport bytes never advances transfer progress. A leading zero delimiter abandons an incomplete
previous frame before a new attempt. Events/receipt buffers are bounded. The UI marks
reports stale after 3.5 seconds and shows an unresponsive/disconnected state on transport
failure. The CH340 reset-line defect and a 115200-baud physical upload were tested
as detailed in the current report. Physical unplug and Windows runtime remain
unverified; higher-rate qualification must report its own measurements.

Position/size/quarter-turn changes coalesce over 100 ms with one transform request in
flight. A returned revision acknowledges acceptance; confirmed status reconciles requested
values. Frame events additionally identify the applied revision/generation. Old-session,
old-generation, unconfirmed-revision and backward-frame events are ignored. Desktop preview
uses matching locally converted JPEGs. Stop clears the preview; reconnect queries current
state without restoring stale requested transforms. Target FPS and actual draws remain
separate values. See [ARCHITECTURE.md](ARCHITECTURE.md).

The USB upload holds storage ownership/pauses playback, reports actual accepted and
verified byte progress, and supports cancellation and validated checkpoint resume.
Explicit user cancellation confirms the current transfer and discards only its
owned incomplete files. Connection/transport failures retain a validated checkpoint
for retrying the same prepared digest. The low-level firmware `cancel_upload`
command itself remains resumable until `discard_upload` is explicitly requested.
During upload the desktop labels the transfer and disables playback
and port-changing actions, and leaves cancellation available. The actor rejects
concurrent controls/device changes instead of queuing them behind a long upload.
USB upload is presented as the primary transfer action with the card left inside
the device; mounted-card export remains available as a secondary alternative; at
115200 baud, UART's 8N1 upper bound is 11,520 bytes/s before framing/ACK/SD overhead.
At 2000000/3000000 it is 200000/300000 bytes/s by arithmetic, not a measured
transfer result. No higher-baud guarantee
or fabricated physical transfer measurement is provided.

For physical qualification on the single selected port, the rebuilt CLI accepts
an explicit application baud. This command transfers a prepared video, verifies
its stored size/digest, waits for playback, measures completed presentations and
pauses afterward, using one client/handle:

```sh
target/debug/muse-video-cli device --port /dev/ttyUSB0 --baud 2000000 --lang en \
  --upload /absolute/path/fresh-video.mvb --benchmark-seconds 10 \
  --target-fps 30 --display-native --decoder-mcus 8
```

Repeat with `--baud 3000000` only after compiling that firmware UART rate. The
tested file must actually send new bytes: a matching completed digest can reuse
its stored media, while a checkpoint resume starts above zero. Report
`upload.host_transfer.resumed_offset`, `newly_confirmed_bytes`, `receiving_seconds`,
`confirmed_bytes_per_second`, `verification_seconds` and `end_to_end_seconds`.
The upload result and benchmark both contain connection-lifetime host diagnostics;
the benchmark additionally queries device UART counters. Record fresh before/after
deltas where available. Plain CLI `--request` replies omit host diagnostics, and
`--upload` without a benchmark returns before any supplied `--request` list is run.
The CLI benchmark preserves the selected decoder; it has no dedicated decoder-name
option. The persistent `hardware_benchmark` example has `--decoder` but currently
no `--baud` parser: it starts default discovery at 115200 on the same selected
handle. The Python profile script and Windows test launcher similarly start at
115200; rebuilt clients can discover higher rates, but those wrappers cannot
explicitly isolate them.

Firmware uses `MVB_UART_BAUD` at `Serial.begin`, HELLO capability reporting and
serial diagnostics. There is no runtime baud-switch command. A qualification build
should inherit all flags from the actual selected performance environment and add
only its runtime baud, for example:

```ini
[env:cyd_uart_2000000]
extends = env:cyd_decoders
monitor_speed = 2000000
build_flags =
    ${env:cyd_decoders.build_flags}
    -DMVB_UART_BAUD=2000000
```

This is a proposed build configuration, not an existing or physically qualified
environment. Use the selected final base rather than assuming this example's
decoder profile wins. Preserve the inherited ROM `upload_speed = 115200`, which
is independent of application UART speed. Replacing all build flags with a global
environment override can lose wiring/driver/decoder definitions. Flashing still
requires the project's verified backup and application-only workflow. Windows
packaging currently accepts application baud only through 921600 and has a finite
firmware-environment allowlist; extending it requires exact manifest provenance
and separate Windows runtime qualification.

Native parser/engine/PTY and desktop-actor tests cover framing, corruption, retries,
invalid/stale/conflicting commands, interruption/reboot resume and confirmed controls.
Linux PTY regressions additionally cover discovery through all five candidates,
bounded silent exhaustion, rejected out-of-set rates, explicit high-rate selection,
preserved startup margins, stale/invalid-frame attribution and retained diagnostics.
They do not prove a physical device connection. [PHASE_3_REPORT.md](PHASE_3_REPORT.md)
records the exact verification environment.
