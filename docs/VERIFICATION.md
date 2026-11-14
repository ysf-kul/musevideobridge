# Phase 1 verification record

Date: 2026-11-11. This record separates source review, host checks, failed hardware
inspection and future acceptance. See [PHASE_1_REPORT.md](PHASE_1_REPORT.md).

## Environment and scope

Available host: CachyOS Linux, kernel `7.3.3-1-cachyos`, VMware USB infrastructure
visible in enumeration. FFmpeg/FFprobe: **n9.0.2**, existing system installation.
Existing esptool: **4.9.0** in the local PlatformIO tool directory; used through an
existing Python environment. No application/firmware dependencies were installed.
There was no Git repository, application, build configuration or test suite at start.

## Checks run and actual results

| Check | Result / meaning |
| --- | --- |
| Mandatory standards | All three read completely before repository changes |
| Integrity of mandatory files | SHA-256 exactly matches pre-change hashes below |
| Repository/ecosystem inspection | Root structure, neighboring README/manifests/tokens and old hardware reports inspected read-only |
| External research | 37 key successful source snapshots recorded; MCU/LCM images actually visually inspected. Failed vendor/license/API URLs disclosed in SOURCES.md |
| USB enumeration | CH340-family `1a86:7523`, `/dev/ttyUSB0` observed; owner confirms one-USB CYD |
| ROM flash identification | **Failed**, exit 2/errno 13 before port open; no device command/reset/stub/write |
| Permission observation | Port `root:uucp` 0660; user lacks access; non-interactive sudo unavailable. No permissions/groups changed |
| Generated media experiment | Two local synthetic four-second H.264 MP4s; 12 MJPEG AVI cases; script completed |
| JPEG packet inspection | **600/600** complete baseline SOF0, 8-bit, 4:2:0 packet headers/dimensions matched their case |
| Host decode | **12/12** decoded with FFmpeg; a separate strict `-xerror` pass also had exit 0 and zero error diagnostics for all 12 |
| Timing/model arithmetic | **600/600** PTS match CFR `i/fps` within FFprobe precision; all 12 modeled byte counts match draft layout arithmetic |
| Maximum frame analysis | Largest JPEG 66,031 bytes; all 100 noise/q=4 packets exceed proposed 49,152-byte limit. This is a measured constraint, not a failed product test |
| Research source syntax | Python AST parse passed; no additional Python dependencies |
| JSON/localization/profile checks | Valid JSON; **31 identical nonempty keys** in tr/en; Turkish-default policy; observed fields remain nullable and one-USB profile retained |
| Local Markdown links | All local documentation/evidence links resolve after completing the document set |
| Evidence consistency | Committed summary matches actual host results; source snapshot hashes and script hash recorded |

No custom container was written or parsed, no COBS protocol was implemented, and no
ESP32 decoder/SD/TFT or desktop runtime workflow was validated. AVI/custom comparison
uses real JPEG/AVI measurements and an explicitly labeled custom-size **model**.
The host research corpus intentionally includes a worst-case-like noise sample;
it is generated test media, not fabricated device telemetry or the owner's video.

## Reproduce host encoding research

```sh
python3 research/benchmark_encoding.py
```

Output is a new `.verification/encoding-<UTC>/`. The actual initial run used:

```sh
python3 research/benchmark_encoding.py --output .verification/phase1-encoding
```

That explicit directory now exists, so rerunning the latter command will safely
refuse it. Choose a new local research directory or the default timestamped one.
The final script includes strict decode checking; the original experiment was followed
by the separate strict pass after that check was tightened. No re-encoding was
needed to validate existing identical artifacts.

Raw synthetic MP4/AVI, command logs and per-frame JSON remain in ignored
`.verification/phase1-encoding/`. The small shareable
[encoding summary](evidence/encoding-summary.json) contains sizes/hashes/method;
[hardware evidence](evidence/hardware-inspection.json) records the permission-limited
attempt; [source snapshots](evidence/source-snapshots.json) contain bibliographic
URLs and hashes, not third-party copyrighted downloads or private firmware backups.

## Mandatory document hashes — before and after identical

| File | SHA-256 |
| --- | --- |
| `MUSE_GUIDELINES.md` | `b19624f475656d2ad9839e28c14d1e8792f2e9c41af0d2f7688371961ae27317` |
| `MUSE_DESIGN_SYSTEM.md` | `5fa3df88a80b2f9ef454efd886fc0301675983e1c6c83a0e4c5aa92ed363ed87` |
| `AGENTS.md` | `3b20053c06f27bdaea9478d446d690993c601a8d35b0dfb79b4f1829892a6c1e` |

## Not run / not claimed

- Desktop compilation, app unit/integration/E2E suites, linter/formatter configuration:
  not yet present; no NPM/Cargo/firmware stack was installed for Phase 1.
- Manual UI review, theme/resize/keyboard/screen-reader/reduced-motion verification:
  no UI implemented.
- Current PCB/controller/wiring identification, ROM chip/flash response, runtime heap/
  PSRAM/CPU statistics, card filesystem/capacity/free space, SD performance or visible
  TFT playback: not obtained. ROM access failed before communication.
- Firmware compilation, flashing, flash backups, SD export/upload, live transforms,
  serial reliability or transfer benchmarks: not performed in this phase.
- Windows application builds/runtime and additional Linux distributions: not tested.
  The CachyOS host research is not a claim of supported Linux application workflows.
- macOS: outside current platform scope; no build/runtime/release target promised.

Phase 2 waits for approval. Current read-only authorization is limited to safe hardware
identification and does not permit flash/SD/configuration modification.


## Phase 2 verification

Current results are in [PHASE_2_REPORT.md](PHASE_2_REPORT.md) and
[evidence/phase2-verification.json](evidence/phase2-verification.json). Seven Rust unit,
nine FFmpeg/storage integration and six sanitized shared-engine integration tests passed.
The Linux PTY test used the actual serialport adapter. Native core assertions passed
320 transformations, four dirty-region cases, bounded protocol/CRC and rational timing.
Formatting/Clippy/documentation/catalog checks passed. Firmware compiled with 36,884
static RAM bytes and 627,214 application flash bytes. This excludes dynamic engine/queue/
SDK allocations and is not a measured physical heap report. No board/card/flash/configuration
writes or actual TFT/SD/UART playback tests occurred. Windows remains untested.
