# Technology and dependency evaluation

> Phase 1 architecture/evaluation retained for reference. Current implementation and deviations are documented in [PHASE_2_REPORT.md](PHASE_2_REPORT.md).
No desktop/firmware dependency was installed or locked in Phase 1. The research
script uses Python's standard library and already-installed FFmpeg/FFprobe. Source
IDs resolve in [SOURCES.md](SOURCES.md). Freeze exact versions and transitive licenses
after the first compilation/compatibility checks, rather than choosing HEAD blindly.

| Candidate | Need / decision | Maintenance, license, size and compatibility |
| --- | --- | --- |
| Tauri 2 | Recommended desktop shell; existing Muse precedent | Active project; core MIT/Apache-2.0. OS webview avoids bundled Chromium, but Linux WebKitGTK/GTK and Windows WebView2 are real dependencies. Final installer/RSS must be measured. |
| Svelte 5 / TypeScript / Vite | Recommended reactive UI/build/type system | Existing Muse conventions; Svelte/Vite MIT, TypeScript Apache-2.0. Mostly frontend/build cost; no additional editor framework needed. Pin stable compatible versions. |
| Rust / Cargo | Recommended local processing and protocol core | Reuses ecosystem skill/code organization; safe bounded parsing and portable I/O. No GC/runtime service. MSRV determined from the actual lock/build, not assumed from adjacent tools. |
| Tokio | Bounded background jobs/processes; already used by Muse Downloader | MIT; enable only needed features. A full executor per job or extra message-bus framework is unnecessary. |
| Serde / serde_json | Typed preferences, FFprobe JSON, protocol DTOs | MIT/Apache-2.0; validate lengths/depth/types and generate frontend types to prevent drift. Binary container is manually parsed. |
| `serialport` | Recommended serial enumeration/read/write adapter | MPL-2.0; inspected source has native Windows and Unix support, optional Linux libudev. Stable 4.x candidate; inspected main is 4.10.2-alpha0, **not** a stable version pin. One bounded thread avoids needing an additional async serial crate initially. |
| Tauri native dialog plugin | Required local video/folder selection | MIT/Apache-2.0; minimal capabilities. Broad filesystem/shell/plugin permission grants are unnecessary. |
| SHA-256 implementation (`sha2`, firmware mbedTLS) | Content identity and verified transfer | Rust sha2 MIT/Apache-2.0; use SDK mbedTLS on firmware rather than adding another crypto stack. Hashing adds measurable I/O/CPU during publication. |
| CRC32 / COBS | Bounded wire framing and accidental-corruption checks | Small implementation or audited permissive crates; choose only after checking code size/license. CRC is not authentication. No base64 overhead for file chunks. |
| FFmpeg + FFprobe executables | Required local MP4 decode/filter/JPEG conversion | Mature; licensing depends on build flags, external libraries and GPL/nonfree components [V3]. The installed research build enables GPL and version3; do not call it LGPL-only. Run as a local process, do not link libav* initially. Bundled size/license/source obligations need an exact release-build audit. |
| PlatformIO v8.0.0 + Arduino-ESP32 3.3.12 | Recommended first firmware experiment | PlatformIO platform Apache-2.0; Arduino core LGPL-2.1 with SDK/transitive notices. Official pinned platform metadata supports this pairing [D4]. Compile/JPEG/display compatibility unverified; do not copy the old 6.12.0 pin without evaluation. |
| Native ESP-IDF | Evaluated alternative | Apache-2.0 core; SDK component notices still apply. Offers configurable FreeRTOS stats, DMA, esp_lcd and FatFs/SDSPI; larger initial integration effort. Use if measured limitations justify it. |
| TFT_eSPI 2.5.43 | Recommended first display adapter | Existing original-CYD configuration and SPI/DMA primitives. Inspected library metadata version 2.5.43; license file includes MIT origin and BSD/FreeBSD/inherited notices. Preserve all applicable notices. New Arduino toolchain compatibility and exact controller initialization require qualification. |
| JPEGDEC | Recommended JPEG decoder | Apache-2.0; inspected commit `32132971bcc032b5b14f1e1e968c242d8d986854`. Baseline Huffman, file callbacks, RGB565, half/quarter/eighth scale; roughly 20 KiB working-memory intent. Classic ESP32 has no S3 SIMD acceleration. Progressive full-frame support is not assumed. |
| TJpg_Decoder / ChaN TJpgDec | Evaluated alternative decoder | Small working pool and permissive ChaN notice; slower/smaller-memory tradeoff needs same-frame target benchmark. Wrapper source notices must be audited at selected version; one attempted LICENSE URL returned 404, so no complete license audit is claimed. |
| LovyanGFX | Evaluated alternative display path | Rich panel/bus/DMA support; license file includes FreeBSD and inherited MIT/BSD notices. Could help transformations but broad features/compile footprint may be unnecessary. Prefer one measured adapter, not two active display libraries. |
| Arduino SD/SPI / SDK FatFs | Recommended first storage adapter | Already supplied by selected SDK; no extra SD library until measured throughput/recovery needs justify it. FAT32 proposed; exFAT/64-bit-seek settings unverified. |
| XPT2046 touch library | Deferred until local touch controls are required | Board has distinct touch SPI wires; hardware bus ownership matters. A named library alone does not solve third-bus conflict. Evaluate software-SPI compatibility/license before adding. |
| Manrope local font assets | Adopt established Muse typography | SIL Open Font License; include local Latin + Latin Extended assets/notices. Do not load Google Fonts over the network. |

Avoid SQLite, a cloud service, a runtime AI SDK, a video-editor toolkit, music
recognition, Bluetooth, Wi-Fi setup, auto-update/downloader and heavyweight charts
unless an explicit product need emerges. Small JSON preferences and a compact
telemetry panel suffice for the defined app.

## Platform dependencies and verification limits

Linux Tauri requires WebKitGTK 4.1/GTK3 development/runtime dependencies and a working
display session. Distribution packaging, sandboxing/portals, udev enumeration and
serial access differ. On this CachyOS host the tty group is `uucp`, not necessarily
`dialout`. Never fix hardware access by silently running chmod, changing groups or
installing drivers. VM USB pass-through may add latency/failures. These are real
acceptance scenarios, not proof that all Linux environments work.

Windows Tauri requires Microsoft C++ build tools for development and WebView2 at
runtime. Verify official CH340 driver behavior where needed, native COM-port open/
reset/baud/disconnect, removable-card access, trusted FFmpeg executable paths and
process cancellation. No Windows build/runtime has been run here.

FFmpeg bundling can dominate download size even when Tauri is small. Phase 2 can
use an owner-selected trusted local executable with feature/version detection and
clear missing-tool guidance. Do not auto-download or run arbitrary binaries. Phase
3 chooses a legally redistributable audited FFmpeg build or documents an external
dependency, and measures actual package/startup/RSS size. No macOS packaging target.

No Muse source/release license is invented by this report; dependency licenses and
the owner's application license decision remain separate. No binary distribution
is produced in Phase 1.
