# Queue, playback modes, progress, and touch

This feature set is included in v0.4.0, with physical touch and visual acceptance
still unverified. The portable C++ engine owns
playback and the queue. The Rust serial actor owns the USB connection; Svelte
displays device-confirmed snapshots. There is no competing desktop playhead.
Mandatory Muse instruction files are unchanged.

## Synchronization

Video IDs are the SHA-256 of validated `.mvb` files. The device holds up to 12
unique IDs, their order, current position, and loop mode. Upload publication adds
the validated video and starts its real playback preparation. Removing an entry
does not delete its SD file. Conversion selection and the session's prepared
preview library are separate from the device's current video.

`get_playback_state` returns queue order/revision, current/next/previous IDs,
position, playback state, loop mode, state revision, playhead generation, and
preparation. `playback_state` events report changes at most every 100 ms. Order is
included only when the queue revision changes. The existing one-second telemetry
poll recovers missing events; queue snapshots are queried when a revision changes.
Frame reports retain the existing 100 ms cadence and session/generation guards.

`set_queue` supplies the complete desired order and `expected_queue_revision`.
The engine validates the complete request before replacing order, rejecting
duplicates, missing files, invalid hashes, excessive length, and stale revisions.
Mutation replies remain small enough for the existing retry-receipt cache. The
desktop waits for the acknowledged revision before enabling another queue edit.
Touch actions use the same playback/navigation functions without consuming USB
command sequence numbers. No speculative icon change represents completed playback.

Connection always performs HELLO and refreshes actual device state. Old requested
queue/mode/transform values are never replayed. Existing upload identity,
checkpoint, SHA-256, cancellation, and session-reset checks remain in effect.
Transport failures retain recovery checkpoints; explicit cancellation removes
only the identified upload's incomplete files.

## Queue behavior

- **Play Once:** advance through the remaining queue and stop at its end.
- **Loop Current:** repeat the current item; manual navigation still selects
  adjacent entries without wrapping.
- **Loop Queue:** advance and wrap; previous from the first entry wraps to the last.
- Next/previous preserve whether playback was running or paused. Selecting a
  specific entry starts it. Removing the current entry prepares the item at the
  same position, or the preceding item when removing the last; running/paused
  intent is preserved. Clearing the queue stops playback and shows waiting.
- Navigation skips missing SD files and reports failed preparation. Loading a
  hash-named file verifies its content identity before playback.

Queue order and mode are session state. A device restart starts from its recognized
SD selection/discovery with Play Once, and the desktop adopts that state. This is
not persistent playlist storage. Prepared titles and cached preview registrations
are also session-local; unknown device media remains controllable and is identified
by its hash, without pretending a local preview matches it.

## Measured progress

| Stage | Measurement |
| --- | --- |
| Receiving/uploading | Contiguous bytes written and acknowledged / exact file size |
| Resume validation | Checked checkpoint prefix bytes / checkpointed size |
| SHA verification | Bytes reread and hashed / exact file size |
| Index, frame/decoder validation | Indeterminate; no percentage |
| Loading | Bytes reread and hashed / exact file size |
| Playback preparation | Indeterminate index/decoder setup |
| Ready | Device confirms validated loaded media; upload job may now complete |

Receiving and SD writing occur together in the acknowledged chunk operation;
there is no invented separate write percentage. Hash completion is not the end
of validation. The desktop upload worker continues through publication and the
subsequent playback load pass. Fast stages can finish between two status polls.

The CYD uses English receiving, verifying, preparing, and waiting screens.
Preparation redraws/events are limited to changes and at most four per second.
The desktop uses the same stage measurements, localized in English/Turkish.

## Touch and rendering

The original CYD XPT2046 uses GPIO 25/32/39/33/36. Its dedicated wires use software
SPI so neither TFT nor SD hardware SPI configuration changes. Explicit microsecond
clock/setup/sample delays meet the XPT2046 timing requirements. An idle poll reads
only PENIRQ, at most every 30 ms. A contact must have resistive pressure and stable
coordinates across three samples, and requires release before another action.
Two matching pressure/coordinate observations 30 ms apart confirm a contact;
a transient valid-looking ADC result does not emit a playback action.
Invalid held contacts are not repeatedly sampled. Diagnostic telemetry reports
sampled and accepted contact counts. Touch polling and playback-state serialization
stay out of the hot caller through explicit compiler function boundaries.
The ADC conversion sequence is adapted from the MIT-licensed
[XPT2046_Touchscreen](https://github.com/PaulStoffregen/XPT2046_Touchscreen), with
its notice retained in the adapter. CS stays asserted through pressure, a discarded
settling conversion and three X/Y pairs; the final conversion drains and powers
down the ADC. Startup also completes a power-down command to restore PENIRQ after
an ESP32-only restart. This adds no runtime library or shared SPI-bus dependency.

The first tap reveals three 96×48 targets: Previous, Play/Pause, Next. A later tap
acts. Controls hide after four seconds. While visible, video output is clipped
above the bottom control surface; the surface is drawn only when shown or its
state changes. Hiding restores full video and redraws a paused frame. Owned DMA
leases are released for excluded strips, preserving completion/ownership rules.
Normal hidden-control playback retains the selected direct-DMA renderer,
prefetch, codec warmup, and buffer ownership. No full RGB framebuffer is added.
Manual NewJPEG configuration also preflights the selected display leases, avoiding
a redundant RGB strip allocation that otherwise prevents prefetch reallocation
at the existing internal-heap reserve threshold.
The qualified firmware aligns both reusable compressed-record slots to 16 bytes,
including the dynamic prefetch slot. Queue/media changes therefore cannot change
its alignment through heap placement. This allocation happens at media preparation,
with no allocation or copy added to the frame loop.

The mapping uses reference landscape raw bounds. Physical resistive-panel
orientation, accuracy, and visual acceptance require owner testing on the actual
unit; simulator taps verify playback logic, not physical calibration.

## Reproducible checks

```sh
python3 research/build_native.py
MVB_SIMULATOR="$PWD/.verification/native/mvb-simulator" cargo test --locked --workspace -- --include-ignored --test-threads=1
python3 -m unittest discover -s tests -p '*_test.py'
npm run check
npm test
npm run format:check
cargo clippy --locked --workspace --all-targets -- -D warnings
```

The native simulator alone enables `MVB_TEST_TOUCH`; production firmware has no
synthetic-touch command. The browser acceptance extends the real Rust backend
workflow with two converted/uploaded videos, ordering, next/previous, modes,
themes, accessibility, narrow layout, and reconnect.

`queue_hardware` explicitly accepts a real serial port, two short local MP4
fixtures, and a new evidence filename. It uses the production desktop backend
and never flashes, formats, or deletes media. Run only against the intended CYD:

```sh
cargo run --locked -p muse-video-desktop --example queue_hardware -- /dev/ttyUSB0 red.mp4 blue.mp4 new-acceptance.jsonl
```

Use fixtures lasting at least six seconds for hardware acceptance. A two-second
clip can finish before the host observes its playing state after a serial retry;
that timing race must not be mistaken for a failed playback transition.
The runner records each command/reply and waits for the acknowledged state
revision before checking playback, so an earlier matching video/state cannot
satisfy a later selection or navigation check.
Add `--expect-no-touch` to the runner command for unattended checks: it rejects
accepted touch contacts before and after reconnection. Leave the panel untouched
for this mode; it does not replace physical touch acceptance.

See [the feature verification report](QUEUE_VERIFICATION_REPORT.md) for actual
results, performance comparisons, and the remaining qualification limits.
