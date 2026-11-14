# Three-phase implementation and acceptance roadmap

This is the continuation plan for the requested three-phase process, not permission
to start Phase 2. All subsequent prompts/explicit owner requirements can refine it.

## Phase 1 — discovery and foundation (this phase)

Deliver source-backed hardware/reference GPIO research, exact-unit verification
status, desktop/firmware separation, conversion/transfer architecture, draft binary
container/wire protocol, memory/FPS/telemetry limits, design/localization/dependency
decisions and reproducible host encoding evidence. Preserve Muse standards and user
data. No desktop/firmware build, flash, SD write or device configuration changes.

**Exit:** owner reviews the detailed report and approves Phase 2. Unresolved PCB/LCD/
runtime RAM measurements stay explicitly recorded; no guessed values become verified.

## Phase 2 — core vertical workflow and measured format choice

1. Create the Tauri/Svelte/Rust workspace with pinned compatible dependencies and
   minimal capabilities. Integrate the existing Muse tokens and typed complete tr/en
   localization, Turkish default, light/dark/system themes and one simple workflow.
2. Implement local probe/conversion, draft container writer/parser and prepared-JPEG
   preview. Validate all output, maximum frame/file bounds, VFR timing, aspect/SAR,
   corrupt/unsupported input and cancellation. Use deterministic generated media;
   do not copy/delete the owner's videos for testing.
3. Implement safe direct mounted-folder export, verified copy/publication and selection
   metadata. Exercise empty/full/read-only/removed destination, duplicate output,
   interrupted writes and path containment in isolated local test directories.
4. Implement portable firmware core and explicit **original one-USB candidate**
   display/SD adapters. Compile without assuming PSRAM. Compare custom versus AVI
   using identical JPEG payloads. Unit/native simulation tests do not establish TFT FPS.
5. Implement serial handshake/control/status and device-confirmed preview, pause/
   resume/stop/restart/seek, positioning, width/height/scale and 0/90/180/270° paths.
   Required transforms cannot be marked complete if only half-scale/identity works.
   If low-memory rotation costs FPS, disclose/measure the cost rather than requiring
   reconversion for every transform.
6. Physical firmware installation occurs **only after explicit authorization**, with
   a verified restorable flash backup and preservation of installed SD contents.
   First establish the controller with visible color/corner/orientation tests. Do not
   proceed to timing claims if the TFT is white. Read-only identification authorization
   from Phase 1 does not authorize flashing, formatting or configuration writes.
7. Measure SRAM/heap/largest block, native decoder compatibility, SD latency and
   visible playback at 8/10/12/15 FPS. Evaluate tile versus one-frame transforms.
   Freeze container/protocol v1 only after the benchmark and correctness gates.

**Exit:** real basic select→convert→copy→insert→play workflow; serial controls and
faithful last-reported preview; meaningful host checks; controller/SD/heap evidence;
honest tested FPS/capabilities. If hardware access/authorization remains unavailable,
report physical acceptance as incomplete and never substitute simulator results.
Do not label the phase finished merely because the firmware compiles.

## Phase 3 — optional transfer, resilience and release qualification

Implement optional UART file transfer with bounded chunks, actual throughput display,
whole-file verification, checkpointed resume, cancellation and explicit partial discard.
Qualify baud rates and preview/telemetry responsiveness under transfer load. Add
long-run measurements, power/interruption/corrupt-card error recovery and sampling
limits. Optimize only where measurements show a bottleneck (SPI batching/DMA, bounded
prefetch, transforms). Arbitrary-angle rotation remains optional and experimental;
unavailable is acceptable when memory/performance do not support it.

Complete real Windows and CachyOS Linux desktop workflows, then additional Linux
distribution/session tests where available. Audit dependency notices and exact FFmpeg
build redistribution; measure package/RSS/startup size; document native prerequisites,
card workflow, safe removal, flashing/backup procedures and supported/unsupported
metrics. No untested macOS target or release promises.

**Exit:** acceptance evidence is attached per platform/workflow, UI controls match
implemented capabilities, recovery protects user data, release artifacts have actual
build provenance and licenses. A build-only or host-simulated platform is labeled as
such. Missing test devices remain explicit limitations, not hidden support claims.

## Benchmark matrix and evidence record

| Area | Required actual checks |
| --- | --- |
| Panel | Identified/matched controller, RGB/black/white bars, all corners, orientation, no clipping/color-order errors |
| Media | Pattern, natural/high-motion/high-detail, portrait, anamorphic SAR, rotated metadata, VFR, invalid/truncated, HDR rejection/qualified conversion |
| Playback | 8/10/12/15 FPS, ≥10-minute sustained runs; read/decode/draw p50/p95/max, actual FPS, lateness/drops, heap/stack low-water |
| Formats | Same JPEG payloads in AVI/custom; sequential reads, cold/open/index time, random seeks, near-size-limit cases; no thousands-of-files assumption as a claimed benchmark |
| Transforms | X/Y edge clipping, fully off-screen, aspect lock, width/height extrema, quarter-turns, pause/seek updates; both render paths if present |
| Storage | Existing 4 GB card actual filesystem/capacity; no format/delete; removal/full/read-only/corruption/power-loss cases on an explicitly authorized test card |
| Serial | ROM reset behavior, stable baud, connect/disconnect, stale sessions, USB pass-through vs direct host, command latency under load, upload/cancel/resume/hash |
| Preview | Content identity, last drawn index/transform revision, generation discard, pause/seek timing, missing local artifact, disconnected freshness |
| Telemetry | Compare allocator/clock/capacity APIs; null reasons, disabled Wi-Fi, unavailable temperature, real scheduler stats or null CPU load |
| UI | Turkish/English parity and persistence, light/dark, narrow/wide, 200% text/high DPI, keyboard/focus/labels, loading/error/disconnected, reduced motion |
| Platforms | Windows native runtime and specified real Linux distribution/session; build results recorded separately |

For each test, record date, OS/session/native-vs-VM, exact board/revision/driver,
firmware/tool/library versions, card/filesystem, input digest, encoding/transform,
configured **and actual** clocks, requested/encoded/actual FPS, read/decode/draw
interval definitions, heap/largest block, errors and **human visible-panel result**.
Never publish unique MACs, personal source paths or private flash backups in reports.
