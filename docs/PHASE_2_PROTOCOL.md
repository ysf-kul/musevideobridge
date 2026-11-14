# Phase 2 implemented serial contract

Protocol major **1**, firmware **0.2.0**. The binary framing/layout is the Phase 1
[serial proposal](SERIAL_PROTOCOL.md); this document defines the implemented subset
and resolves earlier optional proposals. The older application's protocol is incompatible.
The historical Phase 2 baseline was **115200 baud, 8N1**. Current clients can discover
compile-time firmware rates of 115200, 460800 or 921600 on the selected port, without
closing/reopening it or scanning other ports. Explicit faster rates try only the
requested setting. The [current protocol document](SERIAL_PROTOCOL.md) describes
bounded discovery deadlines and diagnostics; physical stable-rate qualification is
reported separately from host PTY tests.

## Handshake and requests

```json
{"method":"hello","protocol_min":1,"protocol_max":1,"client_nonce":"unique-client-lifetime"}
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
sequence; retry the identical envelope at most three times. Eight compact receipts
of at most 512 bytes prevent repeated mutations and replay matching compact errors.
Large status/HELLO replies may be regenerated. A conflicting receipt returns
`sequence_conflict`; expired sequences return `stale_sequence`. Sequence wrap requires
a new client/HELLO. Disconnected sessions return `stale_session`.

| Method | Required parameters / result |
| --- | --- |
| `hello` | Protocol range and client nonce; configured board/controller, firmware, canvas, capabilities, hardware snapshot |
| `get_status` | Playback/transform/SD state, frame and timing measurements |
| `get_telemetry`, `get_info`, `ping` | Same status plus nullable hardware measurements; no paginated diagnostics yet |
| `subscribe` | `telemetry_interval_ms`: integer 500…10000, default 1000; frame events fixed at 100 ms |
| `play`, `resume` | Resume; start at zero when stopped/ended |
| `pause` | Freeze clock and last decoded frame |
| `stop` | Stop clock, clear display, zero playhead |
| `restart` | Reset counters/playhead and start |
| `seek` | Nonnegative integer `position_us`, clamped to an encoded frame |
| `set_position` | Integers `x`: −640…640, `y`: −480…480 |
| `set_size` | Integers `width`: 1…640, `height`: 1…480; optional Boolean `lock_aspect` |
| `set_scale` | Finite numeric `scale`: 0.01…8 relative to encoded, rotated image; resulting rectangle must satisfy size bounds |
| `set_rotation` | Integer `rotation_deg`: 0, 90, 180, 270; aspect-locked quarter-turn swaps dimensions around center |
| `set_transform` | Atomic x/y/width/height/rotation; optional aspect lock |
| `set_fps`, `set_target_fps` | Rational `numerator`, `denominator`: 1…30 FPS, numerator ≤30000, denominator ≤1001 |
| `select_video` | Lowercase 64-character `sha256`; loads `/muse-video/<digest>.mvb`, validates asynchronously |
| `begin_upload` | Digest, integer `size` within profile, `container_version`: 1 |
| `upload_status` | Current transfer ID, accepted/checkpointed offset, verified bytes, size, state and error |
| `cancel_upload` | Current nonzero `transfer_id`; flush/checkpoint and retain partial |
| `finish_upload` | Current transfer ID; complete contiguous bytes required, returns verification state |
| `discard_upload` | Current identified app-owned transfer ID and explicit `confirmed`: true; disabled while receiving/finalizing |

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
states. Binary chunks/ACKs match [SERIAL_PROTOCOL.md](SERIAL_PROTOCOL.md): ≤1024 data
bytes, envelope sequence/CRC, transfer ID, contiguous offset, accepted and durable
checkpoint offsets. Identical duplicate bytes receive status 1; conflicting bytes or
out-of-order offsets receive status 2. No chunk gets appended twice.

A first checkpoint and additional checkpoints at 64 KiB progress boundaries append
96-byte records to `/muse-video/.upload-<digest>.meta` (the original Phase 2 global
`/muse-video/.upload.meta` remains readable for recovery compatibility):

| Offset | Content |
| ---: | --- |
| 0 | Eight bytes `MVBUP1\0\0` |
| 8 | 64 ASCII lowercase digest bytes |
| 72 | u64 expected file size |
| 80 | u32 checkpointed contiguous offset |
| 84 | u32 prefix CRC-32/ISO-HDLC |
| 88 | u32 CRC32 of preceding 88 bytes |
| 92 | Four zero bytes |

Partial path is `.upload-<digest>.part`. Budget includes remaining file bytes,
checkpoint records and 65 KiB filesystem margin. Cancelling without new bytes does
not append another checkpoint. Journals are bounded to 3,200,000 bytes. On resume,
validate identity, journal/partial sizes and prefix CRC incrementally, then accept
only the checkpointed offset. The Rust client waits for `receiving` before sending.
Corrupt/ambiguous journals fail closed and remain available for manual inspection;
they are never automatically removed to make room for another upload.

Current firmware prefers an existing per-digest journal. Without one, it validates
the legacy journal and resumes it only when digest and size match. A valid legacy
journal for a different digest is retained unchanged, and the new video uses its
own journal. An invalid legacy journal with no matching per-digest journal still
fails closed. A conflicting size or unidentified partial is also retained and
rejected. Completion and explicitly confirmed discard clean up only the selected,
identified recovery journal and partial; other cancelled uploads remain resumable.

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
The host requires exact completion identity, byte counts and SHA-256; a missing or
mismatching final digest cannot prove a successful transfer.

## Events and measurements

Default telemetry **1 Hz**, configurable 0.1…2 Hz. Frame events ≤10 Hz. ESP32 has separate
bounded reply and event queues so replies/ACKs take priority; excess events are discarded.
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
