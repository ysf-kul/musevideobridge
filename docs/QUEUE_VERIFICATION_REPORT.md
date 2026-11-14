# Queue and playback feature verification

Test session: 2026-11-14–15. This is an implemented desktop/firmware feature set, with
automated and real USB verification. Physical touchscreen and display appearance
checks remain **unverified**, as requested by the owner. The owner subsequently
requested publication of v0.4.0 with these limitations documented. The release is
marked **pre-release** because touch qualification remains incomplete.

## Implementation and synchronization

The ESP32 engine is authoritative for queue order, current video ID/position,
playback state, loop mode, and preparation. One Rust actor owns serial transport.
The desktop sends commands and adopts confirmed revisions; it does not maintain
an independent playhead or replay old settings after reconnection.

Up to 12 unique prepared videos can be transferred, selected, removed from the
queue, reordered, and navigated with next/previous. Removing entries retains SD
files. The desktop retains multiple prepared artifacts and reconstructs preview
from the current device content ID rather than the latest conversion selection.

Play Once advances to the queue end and stops. Loop Current repeats the active
video. Loop Queue wraps at either navigation boundary. Next/previous preserve
running/paused intent; selecting an item starts it. Queue edits carry the expected
device queue revision and reject stale changes atomically. Queue order and mode
are session-local, with real boot state refreshed after device restart.

## Progress and touch

Receiving progress counts contiguous SD-written, acknowledged bytes against the
exact file size. Hash progress counts reread bytes. Resume progress counts the
verified checkpoint prefix. Index, frame/decoder validation, and final playback
setup are indeterminate. The upload job stays active after file publication until
loaded device playback is confirmed ready. Neither interface invents a percentage.

The CYD shows English waiting, receiving, verifying, and preparing screens, with
measured percentages only where applicable. A tap reveals Previous, Play/Pause,
and Next targets measuring 96×48 pixels. A subsequent contact acts; controls hide
after four seconds. Video output is clipped around the temporary control surface,
which redraws only when needed. Pressure and coordinate validation, explicit SPI
clock timing, and release gating reject invalid contacts without repeated ADC work.
Matching pressure/coordinates on two polls 30 ms apart confirm each action. Startup
and the end of each conversion restore the controller's power-down/PENIRQ state.
Software SPI on the dedicated XPT2046 pins leaves
the TFT/SD SPI buses and owned DMA rendering path intact.

The desktop uses existing Muse Material 3 colors, shape/spacing tokens, control
styles, English/Turkish catalogs, light/dark themes, keyboard access, and responsive
layout. The mandatory Muse instruction files are unchanged.

## Checks actually run

| Check | Result |
| --- | --- |
| Rust/core/desktop with native firmware simulator, including ignored integration cases | 60 passed |
| Python firmware/storage/packaging/decoder checks | 109 passed, 13 optional checks skipped; 122 discovered |
| Focused prefetch, direct-DMA and qualified-default suites after allocation fix | 22 passed, 2 optional checks skipped; overlaps the full suite |
| Final GPIO/ADC adapter timing and channel checks | Passed with address/undefined sanitizers |
| TypeScript synchronization/localization tests | 6 passed |
| Browser workflow using the real Rust backend and C++ engine | Passed: two conversions/uploads, ordering, next/previous, modes, reconnect, light/dark accessibility, narrow layout, reduced motion and keyboard seek |
| Svelte/TypeScript check | 0 errors, 0 warnings |
| Clippy, Rust format, Prettier, diff whitespace | Passed |
| Final production dependency audit / public source secret scan | 0 npm production vulnerabilities; no leaks detected |
| Optimized CYD firmware | Built and application-only flashed/hash-verified |
| Linux optimized desktop | Built; native USB connection, play/pause/resume and normal window close (exit 0) verified |
| Windows x64 optimized MSVC cross-build with final frontend | Passed; one SDK debug-information linker warning |
| Final local Linux packages | AppImage, portable tarball, Debian and RPM built; portable internal hashes and package archive checks passed |
| Final standalone Linux package runtime | Portable, direct AppImage and extraction fallback launched outside checkout, rendered a native window, created cache outside the image, and closed normally with exit 0 |

An early sanitizer failure exposed a retained startup string pointing into command
stack memory; that lifetime bug was fixed and the relevant sanitizer suite passed.
Browser acceptance exposed editing before mode/queue confirmation; subsequent
edits now wait for the accepted revision, and the workflow passed. The existing
fake TFT adapter was extended for the new preparation/control drawing methods.

## Real hardware results

Test environment: CachyOS Linux x86-64, connected ESP32-2432S028R / classic ESP32,
ILI9341, installed FAT32 microSD, 115200-baud USB. A complete private 4,194,304-byte
flash backup completed with per-segment verification and a separate whole-image
device digest comparison before application-only writes at `0x10000`.

The production desktop backend transferred two real prepared videos, reported
measured receiving and verification/loading stages, waited for readiness, paused,
navigated next/previous, reordered entries, removed/restored an SD entry without
retransferring it, and exercised all three loop modes. Reconnection restarted
this unit through the port/reset behavior; the desktop adopted its actual single
boot item and Play Once mode instead of restoring the old queue/mode. One earlier
HELLO-discovery attempt timed out; later probes and complete acceptance succeeded.

The native optimized Linux window also connected to the real device, played,
paused and resumed its boot video; its buttons changed with confirmed playback
state, and normal close returned exit 0. Earlier coordinate-based GUI attempts did
not establish a connection; those captures are not acceptance evidence. Native
GUI upload-progress observation was not repeated: real USB progress was checked
through the production backend, with frontend progress/queue flows checked through
the browser integration and native C++ engine. A two-second fixture also caused an
acceptance wait to miss the playing interval after a serial delay; six-second
fixtures completed the final real-device checks.

The final guarded-touch firmware passed another complete USB acceptance run with
26 acknowledged commands, two completed uploads, and zero accepted idle touch
contacts both before and after reconnect. Its first attempt exposed a runner
observation race: matching video/state alone could accept a snapshot from before
the selection command and then navigate from the wrong queue boundary. The runner
now logs command replies and waits for their acknowledged revisions, matching the
production UI's existing confirmation rule. The corrected acceptance passed.

Standalone package checks initially expected a settings file on first launch;
the app intentionally writes preferences only when they change. The smoke runner
was corrected to check actual startup cache directories. An overly broad archive
path check also matched original third-party license locations under `licenses/`;
the final inspection preserves those notices and checks for development code and
user data. Both corrected checks passed. AppImage packaging initially lacked
`mksquashfs` on PATH; an official Arch package was extracted into ignored task-local
tools, after which packaging and both launch methods passed without a system install.

Physical taps, touch orientation/calibration, and display appearance were not
observed. Host-only synthetic taps exercise the real engine handler for reveal,
pause/resume, next/previous, timeout, and host sequence independence. The synthetic
touch method is excluded from production firmware. Physical cable interruption is
unverified; automated tests cover transport interruption, checkpoint retry, reset,
and state refresh.

## Playback performance

All runs used the same 320×240, 30-FPS prepared clip, 40-MHz pixel / 20-MHz command
clocks, owned direct DMA, prefetch and one-block codec warmup.

| Measurement | Actual completed-draw FPS | Scope |
| --- | ---: | --- |
| Previous qualified reference | 29.7992 | Historical 25×12-second clips, five active minutes |
| Installed firmware before this change | 29.9110 | Five clips, 60.178466 active seconds |
| Final guarded-touch firmware | 29.7438 | Five clips, 60.079732 active seconds; full-screen draw and idle touch counters checked |

Final performance is **0.19% below the historical 29.80 FPS reference** and 0.56%
below the installed firmware's shorter baseline. Individual runs measured 29.7853,
29.7178, 29.7328, 29.7619, and 29.7213 FPS. Across 1,787 completed draws, every frame
submitted all 76,800 screen pixels (137,241,600 pixels total). All runs recorded
zero sampled/accepted idle contacts, zero DMA ownership errors, and zero application
buffer allocations per frame. Five separate complete TFT-memory readback checks
covered 384,000 pixels with zero mismatches. Readback time is excluded from playback
FPS. This shorter comparison found no significant normal-playback regression; it
does not establish a new five-minute endurance qualification.

Earlier diagnostic runs exposed spurious touch readings, including unwanted
overlays and a pause. Those runs are not normal playback qualification, even when
their separate TFT readback checks passed. Startup power-down and stable-contact
confirmation were added before the clean final guarded-touch runs above.

The first diagnostic performance attempt could not re-enable prefetch because
manual codec preflight reserved an unnecessary RGB strip despite selected direct
DMA output. That path now preflights display leases consistently with automatic
startup; the new regression assertion passes and all five final runs completed.
The rendering implementation was retained. The final application-only flashed
image is 680,240 bytes, SHA-256
`814a2be9ceea7005fc550c2217f114b546a840bd0af6452c4a9f6e72d90dba8b`.

## Version 0.4.0 packaging and device checks

Version 0.4.0 rebuilt the desktop and firmware with matching version identifiers.
All 60 Rust/native tests and six frontend tests passed again; Linux and Windows
optimized builds, fresh package checksums, and all three standalone Linux launch
modes passed. Windows packaging retains the byte-identical, checksum-verified
conversion tools and corresponding source from the production v0.3.0 ZIP.
An initial concurrent cross-build failed while frontend files were regenerated;
the successful rebuild used a frozen frontend snapshot. No rendering changes were
made while versioning the release.

The first connection after flashing timed out during HELLO discovery. A normal
boot probe and retries followed. One acceptance run completed its queue actions
but failed its idle-contact assertion; the later complete acceptance passed with
zero idle contacts before/after reconnect. A longer performance run subsequently
recorded accepted contacts during its first clip and a temporary overlay. Physical
interaction was not observed, so their cause is unverified. These diagnostic runs
are not clean idle-touch qualification. **Touch controls are experimental in this
pre-release; a final release still requires physical and idle-touch qualification.**
Of 20 new twelve-second clips, the initial overlay-affected clip is excluded from
normal playback comparison. The remaining 19 clips drew every full-screen pixel,
measured 29.7310 FPS over 228.078266 active seconds, and accepted no
additional contacts. All 20 separate TFT readback checks passed with zero mismatches
and DMA ownership errors. This does not qualify the initial contact behavior.
The release performance evidence keeps those scopes separate. Earlier clean guarded-touch results above remain historical.

## Remaining qualification and publication

Windows runtime/COM hardware and physical touch/visual acceptance remain unverified.
Queue/mode and local preview registrations are not persistent playlists. Resistive
touch uses reference raw bounds; actual panel mapping requires physical testing.
The new performance test covers one active minute with restart/check gaps, rather
than repeating the entire historical five-minute qualification.

The owner's later request authorizes a new v0.4.0 release with physical touch and
visual acceptance still unverified. Desktop/firmware versions have been updated,
fresh production packages are prepared separately, and the public v0.3.0 tag and
assets are preserved. The release notes state the remaining hardware and platform
limits. Windows interactive/COM acceptance and clean Debian/RPM installation are
not established by packaging or runner smoke tests.

Versioned release packages and checksums are prepared under
`.verification/release-040-artifacts/` (ignored, not source files).
The Windows build is `target/x86_64-pc-windows-msvc/release/muse-video-bridge.exe`.

The machine-readable results are in
[queue-playback-verification.json](evidence/queue-playback-verification.json).
Synchronization contracts and reproducible commands are in
[QUEUE_AND_PLAYBACK.md](QUEUE_AND_PLAYBACK.md). Development launch remains
`npm run tauri -- dev`; the real USB backend runner is explicitly invoked as
documented there.
