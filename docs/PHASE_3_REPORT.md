# Phase 3 report — Muse Video Bridge

Date: 2026-11-11; Windows fallback updated 2026-11-12. Scope: desktop application, real engine/firmware integration, live
controls/synchronization, verification and documentation. Desktop version 0.3.0;
firmware remains 0.2.0 and protocol/container major version 1.

## Result and acceptance boundary

The Tauri/Svelte desktop is implemented and connected to the actual Rust converter,
container, safe exporter and serial client. It provides native MP4/card selection,
real conversion/copy/verification/upload jobs, matched-frame preview, playback/seek,
coalesced transforms/FPS, genuine nullable telemetry and persistent Turkish/English/
theme settings. The ESP32 firmware builds. Native Linux MP4 selection, thumbnail,
conversion and verified preview ran in the real Tauri/WebKit window. The executable
with embedded assets also ran with Vite stopped.

Host browser integration exercises the real Rust backend and shared C++ playback/upload
engine with JPEGDEC through an explicitly labeled simulator. The real Linux serialport
adapter is also tested through a PTY. These checks establish implementation and host
behavior; they **do not prove physical CYD playback, GPIO/controller identity, real SD
writes, USB bridge/reset behavior, hardware sensor readings or achievable ESP32 FPS**.

The owner later explicitly authorized firmware flashing and physical tests. Actual ROM
queries identified ESP32-D0WD-V3 revision 3.1, 40 MHz crystal and 4 MB flash. Full flash
backup reads repeatedly lost bytes/re-enumerated USB in the VM; **no firmware write, erase
or SD write occurred**. Temporary normal-user tty ACL access was restored to original
`root:uucp` 0660 after a final query/hard reset. No persistent group/udev/device configuration
change was made. A successful 64 KiB probe is not a complete 4 MB backup.

At the owner's request, actual Windows x64 portable desktop and backup-first flashing
test ZIPs are delivered. The release executable cross-build and package checks passed;
PowerShell workflow tests run on the Linux host with explicit physical-adapter substitutes.
Windows runtime/flashing and the full MP4 → physical SD → actual CYD acceptance workflow
remain unverified. macOS is outside scope. No signed release/installed-package or universal
Linux support is claimed. [WINDOWS_TESTING.md](WINDOWS_TESTING.md) gives direct-host steps.

The three mandatory Muse documents were read completely and retained byte-for-byte;
protected SHA-256 values are recorded in [evidence](evidence/phase3-verification.json).
Historical Phase 1/2 reports were retained. No Git history was initialized.

## Implemented user workflow and design

The first launch shows a Turkish empty state, MP4 selection action and real connection
state. A single preview-led window contains video information/conversion controls; position
and device information are collapsible secondary sections. There are no placeholder pages,
mock product statistics, editor timelines, accounts or remote conversion services.

Input selection uses a native dialog and actual FFprobe. A poster is decoded locally by
FFmpeg before conversion; no platform H.264 webview codec is assumed. Qscale 2…31 and
encoding FPS 1…30 are configurable with contain/crop. Estimates are labeled maximum
bounds. Actual conversion and full output decode/integrity verification publish the
artifact before showing readiness. Work is cancellable and partial/failure records remain.

Card selection displays detected filesystem/free space and prevents unsupported/ambiguous
FAT32 or insufficient-capacity writes. The backend rechecks safety/capacity, writes to
staging, syncs, rereads SHA/CRCs and safely publishes. Recognized active selection is backed
up; unrelated files are retained. No automatic format, erase, eject or cache cleanup exists.
Optional USB upload uses the Phase 2 reliable chunks/retries/checkpoints; progress and
cancellation are wired to the real client. Retry of the same artifact resumes its validated
partial. Upload pauses device playback; the UI does not expose destructive transfer discard.

The connection section lists available ports, remembers the last selection and supports
connect/disconnect/reconnect. It opens only the selected port. Fresh HELLO acquires firmware,
configured board and capabilities/status. Unexpected transport loss marks disconnected or
unresponsive, invalidates live measurements and exposes a localized error. No root/admin
execution is required by the app; permissions/drivers are OS-specific prerequisites.

Play/pause/resume/stop/restart and indexed seeking send real commands. The canvas follows
matching converted JPEGs, confirmed frame index/PTS and transforms; it is explicitly a
reconstruction, not a physical TFT capture. Stop clears to black. A different digest shows
a mismatch/local preview and disables source-dimension-dependent transform editing.
Coordinates, final width/height, scale and 0/90/180/270° rotations update the requested
preview immediately; confirmation remains pending until accepted revision/state is reported.
Target FPS is distinct from actual completed-draw FPS. Seek requests similarly wait for
their confirmed playhead generation, preventing telemetry from overwriting keyboard edits.

The monitor presents real reported CPU/heap/flash/SD/Wi-Fi/uptime/playback fields. Unsupported
or stale values are visibly unavailable. Classic ESP32 calibrated temperature and arbitrary
angles remain unavailable. Host adapters explicitly identify themselves and set ESP hardware
values null. No simulator RAM/FPS is represented as a physical ESP32 measurement.

Muse's existing Material 3 roles, Manrope Latin/Latin Extended fonts, outlined icons, shapes,
spacing, focus and motion tokens were reused from the inspected Muse desktop precedent.
Only product density/layout adapts to this utility. Light/dark/system themes, native inputs,
keyboard focus, zoom, narrow reflow and reduced motion are implemented. English communication
and project documentation are separate from the Turkish-default application catalog.
All application-owned text/errors/dialog titles use catalogs; native OS dialog chrome uses
the host's locale (English in this environment), independently of the app locale.

## Files created and modified

| Files | Change |
| --- | --- |
| `package.json`, `package-lock.json`, `vite.config.ts`, `svelte.config.js`, `tsconfig.json`, `index.html` | Pinned frontend/Tauri/test tooling and entry configuration |
| `src/main.ts`, `src/App.svelte`, `src/app.css` | Real desktop workflow, controls, jobs, connection/monitor and adaptive Muse styling |
| `src/lib/Preview.svelte` | Bounded binary JPEG canvas and ImageBitmap lifecycle; stop/error/stale/mismatch handling |
| `src/lib/{bridge,sync,types,i18n}.ts` | Narrow IPC, typed state, generation/revision/order guards and catalog formatting |
| `src/lib/Icon.svelte`, `src/lib/design/tokens.css`, `public/fonts/*` | Shared icon/token/font assets and OFL notice |
| `localization/tr.json`, `localization/en.json` | Expanded complete desktop copy/error catalogs with matching keys |
| `src-tauri/Cargo.toml`, `build.rs`, `src/main.rs` | Tauri shell, native pickers, background IPC and window-close cleanup |
| `src-tauri/tauri.conf.json`, `tauri.windows.conf.json`, `capabilities/default.json`, `icons/*` | Window/CSP/core permissions and intended Linux/Windows packaging assets |
| `crates/muse-video-desktop/{Cargo.toml,src/lib.rs,src/device.rs,src/settings.rs,src/paths.rs}` | Desktop ownership, cancellable/joined jobs, cached preview index, registered destination, settings, checked portable paths and serial actor |
| `crates/muse-video-desktop/examples/desktop_harness.rs` | Explicit localhost-only host verification server; excluded from product binary |
| `crates/muse-video-desktop/tests/workflow.rs` | Real conversion/export/preview/lifecycle and actor/native-engine integration |
| `crates/muse-video-core/src/media.rs` | Bounded real first-frame thumbnail |
| `crates/muse-video-core/src/serial.rs` | Persistent bounded receive framing/event polling and transport extraction for actor ownership |
| `crates/muse-video-core/src/storage.rs` | Linux-only Duration import for Windows compilation |
| `packaging/build_windows.py`, `packaging/windows/*`, `tests/windows_package_test.py` | Pinned archive verification, portable ZIP, exact-image manifest, localized backup-first flasher and host failure/integrity tests |
| `docs/WINDOWS_TESTING.md`, `dist-packages/*.zip`, `dist-packages/packages.json` | Owner-test usage, actual generated Windows artifacts and independent archive digests |
| `Cargo.toml`, `Cargo.lock`, `.gitignore` | Desktop workspace/locks and generated-test/build ignores |
| `tests/sync.test.ts`, `tests/browser/workflow.spec.ts`, `playwright.config.ts` | State/localization unit and real-backend UI verification |
| `README.md`, `docs/{ARCHITECTURE,VIDEO_FORMAT,SERIAL_PROTOCOL,HARDWARE,BUILD_AND_RUN,PHASE_3_REPORT}.md`, `docs/evidence/phase3-verification.json` | Current implementation, run/flashing/troubleshooting instructions, limitations and evidence |

Firmware implementation and its pinned dependencies were retained from Phase 2; its build
was rerun without upload. Generated Windows test packages are ignored under `dist-packages/`;
source/recipes remain project knowledge. Screenshots/synthetic inputs/partials remain under
`.verification/`. Tauri-generated schemas are ignored build output, not new Muse rules.

## Architecture and dependency decisions

The approved Tauri 2/Svelte 5/TypeScript/Rust and C++ Arduino architecture was preserved.
No custom editor framework, cloud/AI runtime, telemetry analytics, database or extra serial
executor was added. Separate events for frames versus full status avoid rebuilding monitor
values at every frame. One selected artifact reader/index and one bitmap avoid rescanning
or decoding whole videos. Queues/history are bounded. Transform/seek requests coalesce at
100 ms with one request in flight; telemetry normally runs at 1 Hz. UI freshness expires
at 3.5 seconds. Reconnection queries current state without replaying stale requested values.

Jobs are serialized at preparation boundaries, run off the UI thread, and are cancelled/
joined on close. FFmpeg is an external owned process with watchdog cleanup. Preferences
save in order and atomically; invalid/future settings are backed up before repair. A checked
adjacent portable marker uses `Data/config`/`Data/cache`; Windows WebView2 profile is also
directed to that cache. Window creation follows backend initialization to select this path.
Bundled default FFmpeg names resolve beside the executable; explicit paths remain unchanged. Cache
partials/complete jobs remain intentionally retained. Native-dialog registered paths and
backend digest/command validation prevent an unrestricted filesystem/shell bridge.

| Added dependency | Version, role, license and compatibility/size assessment |
| --- | --- |
| Tauri Rust / build | 2.12.5 / 2.7.4; native shell/build, MIT/Apache-2.0; uses OS WebKit/WebView2 instead of bundling Chromium; Linux build/runtime checked |
| Tauri dialog | 2.8.4; native pickers, MIT/Apache-2.0; backend-owned, no frontend filesystem/shell permission |
| Tauri API / CLI | 2.12.4; necessary IPC/build integration, MIT/Apache-2.0; compatible in tested build |
| Svelte | 5.57.3; reactive UI, MIT; exact existing Muse-stack version, small compiled runtime |
| Vite / Svelte plugin | 8.3.7 / 7.3.1; build/dev only, MIT; no product dev server |
| TypeScript / svelte-check | 5.9.3 / 4.7.8; build/type checks, Apache-2.0 / MIT; development only |
| Prettier / Svelte plugin | 3.6.2 / 3.4.1; formatting, MIT; development only |
| Playwright test / playwright-core | 1.61.1; UI test runner/browser adapter, Apache-2.0; test only; direct core pin prevents peer API drift |
| axe Playwright / axe-core | 4.11.1; automated accessibility checks, MPL-2.0; test only |
| Manrope | Existing local variable Latin/Latin Extended WOFF2; SIL OFL; no network fonts |

The Rust desktop crate reuses existing serde/serde_json/core; tempfile is test-only.
Phase 2 FFmpeg, serialport, SHA/CRC, PlatformIO/Arduino/TFT_eSPI/JPEGDEC/ArduinoJson and
OS adapters remain as evaluated in that report. FFmpeg remains external for source/Linux
installs; the requested private Windows ZIP explicitly bundles the unmodified shared LGPL
9.0 snapshot, not an automatic product download. Archive SHA-256 matched upstream release
metadata; config shows shared/version3 without GPL/nonfree. Exact config, LGPL/GPLv3 text,
FFmpeg source snapshot and upstream dependency/build references are supplied. Official
esptool 5.6.0 Windows amd64 (GPL-2.0-or-later) is included only in the flasher with license
and source snapshot. Cargo-xwin 0.23.1 (MIT) and PowerShell 7.6.1 (MIT) are build/test tools,
not app runtime dependencies. Public distribution license and complete transitive
attribution/source/relink review remain unresolved; these are private owner-test builds.
Windows WebView2, Linux WebKitGTK/GTK/libudev and firmware toolchain remain real prerequisites.

NPM audit reports **two low development-dependency findings** (deepmerge 4.3.1 and its
Svelte-plugin dependency path), GHSA-gcqx-w48m-w2h4, with no automatic fix available.
`npm audit --omit=dev` reports zero findings. This is an NPM audit result, not a complete
security or license audit. Exact lockfiles/notices are retained. Muse's own distribution
license, installer signatures/attribution review and an audited FFmpeg release strategy
remain release decisions; no provider-specific service or SDK was added.

## Build and automated results

Available environment: CachyOS Linux VM, Node.js 26.13.0, Rust/Cargo 1.99.1, Python 3.14.9, clang 23.1.4,
FFmpeg/FFprobe 9.0.2, PlatformIO 6.2.0, Arduino 3.3.12/Espressif32 8.0.0.

| Check | Actual result |
| --- | --- |
| `npm run check` | Passed, zero Svelte/TypeScript errors or warnings |
| `npm run test` | **5 passed**: catalog parity/nonempty text, rotated aspect and stale/reordered frame/status/seek guards |
| `npm run format:check` | Passed |
| `npm run build` | Passed; final JS 88.10 kB (31.76 kB gzip), CSS 11.45 kB (3.19 kB gzip) |
| `cargo build --locked -p muse-video-bridge -j2` | Passed native Linux desktop build |
| Tauri `build --debug --no-bundle` | Passed embedded-production-assets executable; Vite-independent runtime smoke |
| `cargo test --locked --workspace` | **22 passed**, seven native-dependent tests explicitly ignored for separate execution; includes two portable-path/tool tests |
| Explicit core native-engine tests | **6 passed**, real JPEGDEC/shared engine, Linux PTY, malformed media, upload retries/checksums/interruption/resume and controls |
| Explicit desktop native-engine integration | **1 passed**, empty-device handshake, real upload, confirmed transforms/seek, allowlist and reconnect |
| `python3 research/build_native.py --sanitize` | Passed ASan/UBSan compilation/core assertions: **320 transform cases**, dirty-region/framing/CRC/rational timing/pause/seek/drop behavior |
| `cargo fmt --all -- --check` | Passed |
| `cargo clippy --locked --workspace --all-targets -- -D warnings` | Passed |
| PlatformIO `cyd_ili9341` | Passed build, no upload: **36,884 bytes static RAM / 627,214 bytes application flash** |
| Windows x64 release cross-build | Passed via cargo-xwin/MSVC/clang-cl/lld; actual **10,733,056-byte** AMD64 GUI executable with embedded frontend |
| Windows flasher/package host tests | **9 passed**, Python stdlib + PowerShell 7.6.1 on Linux; parser/arguments, native-process failure, SHA/offset/tool corruption, backup failure/short read, wrong flash size, cancel, explicit skip and verification failure |
| Windows package inspection | Upstream archive SHA matched release metadata; complete per-file ZIP checksum coverage and x64 PE checks; no Windows runtime/physical write |
| Chromium real-backend workflow | **1 passed**; ~9.3 seconds in the final expanded run, zero page errors |
| axe checks | Zero WCAG 2 A/AA / 2.1 AA violations in tested light/dark/hover states; not full assistive-technology certification |
| Documentation/catalog/protected-hash checks | Recorded in evidence; three protected documents unchanged |

Across ordinary and explicit native integration commands, **29 Rust tests actually ran**.
Core tests include real MP4 metadata/aspect/VFR/rotation, invalid/corrupt/oversized files,
indexing/full decode, input preservation, storage capacity/symlinks/no-clobber and process
cancel/watchdog. Desktop tests add preview digest/frame bounds, selected-path export,
retained unrelated files, corrupt-settings backup and cancellation/join on shutdown.

The browser workflow uses the real App/FFmpeg/storage/native engine, with host pickers and
an explicit host-directory qualification bypass. It tests first-launch Turkish, English
switch/persistence, themes, selection, conversion, verified export, handshake, playback,
keyboard seek to confirmed frame 0/1, X position, scale/90° rotation/FPS, monitor null values,
stop/restart/reconnect, 620-pixel reflow, focus and reduced motion. Screenshots were inspected.
It does not prove native dialogs, actual FAT32 storage or physical rendering. Native-engine
coverage additionally exercises all four rotations, invalid commands and transfers.

Early runs caught a completion-lock deadlock in an older backend process, test locator/
peer-type setup errors, hover/theme contrast interpolation and a seek/telemetry edit race.
These were corrected and the affected checks passed afterward. Native picker keyboard
automation also required correcting layout-specific typing; actual selection/conversion
then succeeded. GTK critical/search-service warnings appeared in that automated picker
session; no runtime crash/conversion failure was observed. This does not establish that
all Linux portal/desktop combinations work. No failed physical test is hidden as a build.

## Native runtime and performance evidence

The actual Linux Tauri window rendered the Turkish empty state, shared Muse icon/font and
listed the owner's port without connecting. Native MP4 picking, FFprobe metadata, binary
thumbnail IPC, conversion and prepared-JPEG preview worked. Both development and embedded
asset executable paths were exercised, the latter with Vite stopped. OS dialog controls
use the OS locale. Generated screenshots capture empty/selected/verified and browser
light/dark/narrow states; they contain host test data, not real ESP telemetry.

A 20-second synthetic 320×240/12-FPS MP4 converted at selected qscale 10 / 10 FPS to
**200 frames and 1,628,048 bytes** in the UI workflow. These are host output measurements,
not ESP32 throughput/FPS. The latest debug desktop executable is **218,158,168 bytes**, including
debug information; this is not release/installer size. Release startup/RSS, prolonged
resource/leak profiling and installed-package measurements were not performed. After the
portable-window change, the actual Linux embedded-assets window was run again with Vite
stopped: Turkish empty state, native picker, metadata/thumbnail and conversion/verified
prepared preview worked. The desktop did not connect to the physical port.

Firmware Engine remains 74,352 bytes dynamically allocated in addition to static RAM,
queues/stacks/SDK/SD/JSON. Static linker RAM is not total runtime usage or free heap.
At configured 20 MHz, full-screen RGB565 SPI pixel payload alone requires at least
61.44 ms; 115200 8N1 UART has an upper bound of 11,520 bytes/s before overhead. These
are physical lower/upper-bound calculations, not benchmarks. Scaling/90° tile writes
can cost significantly more. No sustained 10/15/30 FPS hardware promise is made.

## Physical identification and Windows fallback evidence

ROM reads using esptool 4.9/5.4 succeeded once temporary tty access was granted.
Observed chip is ESP32-D0WD-V3 revision 3.1; flash manufacturer 0x20/device 0x4016,
4 MB, 3.3 V strap. CPU maximum capability is 240 MHz, crystal 40 MHz. These are identity
readings; runtime CPU frequency, RAM/PSRAM/SD and TFT controller are still unmeasured.
Unique IDs are excluded from public evidence and packages.

Standard flash reads failed at 460800 (3845/4096 bytes) and 115200 (4077/4096). A
read-only bounded adapter used 512-byte blocks/one outstanding packet and retained MD5
validation: 64 KiB succeeded, full 4 MB failed at 449/512. A faster probe stalled and
was stopped. USB re-enumerated during failures. This shows an unreliable VM transport,
not defective flash. No complete backup or firmware installation exists. Final ROM
flash-ID/hard reset returned to existing firmware; original tty ACL was restored.

The Windows release initially failed when forcing `+crt-static` (unresolved UCRT symbols).
Normal target configuration succeeded, with SDK missing-PDB LNK4099 warnings. PE inspection
confirms AMD64 Windows GUI and Windows system/UCRT imports; no separate vcruntime DLL
is imported. This checks linking/dependencies, not startup on Windows. FFmpeg binaries
include all seven sibling DLLs; optional acceleration libraries are not needed by the
software conversion workflow. WebView2 remains an installed prerequisite.

The portable ZIP has the real release exe, marker, tools, complete docs, SHA manifest,
license/source/provenance. The flasher ZIP has the actual four firmware images, official
esptool, exact size/SHA manifest and localized PowerShell/cmd helpers. It enforces classic
ESP32 and 4 MB, complete backup before writing, 115200 baud, DIO/40 MHz, exact
0x1000/0x8000/0xe000/0x10000 offsets, write verification and reset. Failed/partial backups
stop; explicit skip-backup requires separate confirmation. No full erase/SD format/write
is invoked. The helpers perform real operations when run on Windows. Isolated host tests
substitute physical adapters only and are not shipped as product functionality. An early
test used Windows drive paths on Linux and incorrect test array indices; both test issues
were corrected and the suite passed. Windows/PowerShell 5.1 behavior remains unverified.

## Remaining qualification and limitations

1. **Physical acceptance:** authorization is now provided; establish reliable direct
   access after VM read failures, identify PCB/controller and qualify pins/colors/orientation,
   flash only the matched profile, then test actual waiting/playback/seek/transforms/FPS,
   UART/DTR/reconnect, heap/CPU telemetry, SD insertion/removal and interrupted writes.
2. **Performance/format:** benchmark actual SD read/decode/draw and 8/10/12/15 FPS at
   native/scaled/all quarter-turn sizes, initialized heap and long-file startup/seek.
   Compare identical MJPEG AVI/custom payloads before freezing the experimental `.mvb`.
3. **Platforms/releases:** test Windows native dialogs/COM/filesystem/process lifecycle,
   additional Linux distributions/portals, normal-user installation and real FAT32 export.
   Complete release license/notices/FFmpeg strategy and installer/signature validation.
4. **Documented product limits:** MP4/video-only, FFmpeg tools (bundled in private Windows ZIP), 48 KiB frame
   and 2 GiB−1 file bounds, no audio/touch UI/arbitrary angles/calibrated temperature.
   Preview is last reported reconstructed state with latency/color differences; unknown
   hashes need matching local conversion. UART upload is slow and pauses playback.
5. **Resource/data policies:** retain partials/cache/backups, with no automatic deletion.
   No release memory/startup/leak endurance, physical power-loss durability or screen-reader
   validation is claimed. Physical CPU-load accuracy remains unverified.

The code and available host verification are delivered; physical device acceptance and
Windows/release qualification are still open. Instructions and troubleshooting are in
[BUILD_AND_RUN.md](BUILD_AND_RUN.md); protocol/container/hardware are repository-owned
knowledge, independent of this conversation or any AI tool.
