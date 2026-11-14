# Version 0.3.0 release verification

Audit date: 2026-11-14. Muse Video Bridge was inspected directly from the current
implementation: desktop UI/Tauri commands, Rust media/protocol/storage/settings
actors, ESP32 UART/microSD/playback/decoder/render/DMA/telemetry paths, build scripts,
locked dependencies, tests, documentation, and production packages.

## New checks

| Check | Result |
| --- | --- |
| Rust workspace including normally ignored native simulator tests | 59 passed, 0 failed, 0 ignored |
| Python firmware/storage/DMA/decoder/Windows-helper suites | 119 passed, 0 failed, 0 skipped (PowerShell and optional TSan enabled); one additional package compatibility test subsequently passed |
| Native ASan/UBSan and read-ahead TSan | Passed; native 500-transform golden cases, explicit five-case sanitizer suite; leak detection disabled |
| Rustfmt / workspace Clippy with denied warnings | Passed |
| Svelte/TypeScript check | 0 errors, 0 warnings |
| Frontend unit tests / format / optimized frontend build | 6 tests passed; formatting and Vite build passed |
| Playwright real-backend/native-simulator workflow | Passed: conversion, export, verified USB upload, playback/seek/transform/FPS/reconnect, translations, both themes, WCAG axe checks, keyboard focus, narrow layout, reduced motion |
| npm production dependency audit | 0 vulnerabilities |
| Gitleaks public-source scan | No leaks detected; generated/private files excluded |
| Optimized Linux Tauri build | Passed, embedded frontend/fonts; all linked runtime libraries found on CachyOS |
| Linux extracted portable archive native smoke | Passed: launched outside checkout from `/tmp`, actual titled desktop window, portable English/light settings retained, normal window close exited 0; no Vite/source/runtime development files |
| AppImage | Direct FUSE and extraction fallback passed native launch/render/normal close 0 on CachyOS x86-64; XDG settings/cache outside image verified, licenses/corresponding runtime source included |
| Debian and RPM | Built; package metadata/runtime dependencies, desktop icon/entry, licenses/setup and absence of development/user data checked. No installation/runtime on Debian/Fedora claimed |
| Windows optimized MSVC x64 cross-build | Passed, embedded frontend; replaceable minimal LGPL FFmpeg tools separately cross-built. Package PE/DLL/resource/internal-checksum checks passed. Published ZIP checksums, bundled probe/conversion/decode (320×240, ten frames), and native HWND creation/5-second survival passed on Windows Server 2025 AMD64; rendered UI/interactive use/USB unverified |
| Firmware production build | Passed for `cyd_optimized_input_aligned`; fresh image build-verified only |

A stale Python test named the older production environment. It was corrected to
assert the current qualified environment/inheritance and compressed alignment while
retaining all previous production guards. The initial failure was recorded; tests
were not weakened. No stable playback or transfer implementation was changed.

## Hardware results from prior qualification

There is **no physical CYD/USB serial device connected during this audit**.
The previous full-screen **320×240** endurance measured **29.799234 actual FPS**,
8,951 draws over **300.376844 active seconds**: 25 repeated 12-second clips with
restart/readback gaps. Twenty-five full-region checks (1,920,000 pixels total)
had **zero pixel mismatches**, with **zero DMA ownership errors**. Clean runs around
29.97 FPS used separate workloads/configurations; exact sustained 30 FPS is not claimed.

Previous native Linux desktop acceptance transferred/verified/played 2,444,120 bytes
through USB at 115200 baud with the FAT32 microSD installed, including cancellation,
clean resend, playback controls and telemetry. These measured physical results
remain historical, not new physical release tests. See
[firmware audit](RELEASE_AUDIT_FIRMWARE.md),
[hardware performance](REAL_HARDWARE_PERFORMANCE_REPORT.md), and
[native USB acceptance](NATIVE_DESKTOP_USB_E2E.md).

## Distribution and remaining qualification

Production downloads: Linux x86-64 AppImage, portable tarball, Debian package, RPM package,
Windows x64 portable ZIP, and SHA-256 checksums. Locked Rust dependency and exact
bundled FFmpeg sources accompany the downloads as separate assets. License texts,
attribution, corresponding-source links and rebuild instructions are supplied.

Linux native runtime scope is **CachyOS x86-64 in the existing graphical VM**, using
X11 software-rendering options for the new standalone smoke. GTK3, WebKitGTK 4.1,
libudev and external FFmpeg/FFprobe 8+ are required. The executable uses glibc 2.34+
symbols, with additional requirements imposed by installed GUI libraries. Debian/RPM
formats are build-verified only; no clean-distro or architecture-wide support claim.
Windows requires WebView2. Interactive Windows/physical USB acceptance remains open;
the narrower Windows Server 2025 runner smoke is recorded in
[its audit](RELEASE_AUDIT_WINDOWS.md) and
[sanitized evidence](evidence/windows-release-runtime-smoke.json).

An initial Tauri AppImage bundling attempt failed and a retry built a large image
containing system GUI/media libraries. That image was not published. On the owner's
request, the release instead supplies an AppImage using system GTK3/WebKitGTK/
libudev/FFmpeg, with original runtime notices and exact corresponding runtime source/
patches/rebuild recipe as a separate asset. The same optimized app/frontend is used;
no playback implementation changed. AppImage smoke results are recorded in
[evidence/appimage-release-smoke.json](evidence/appimage-release-smoke.json).
The packaging-only Tauri rerun emitted a bundle-type/updater warning; no updater is
implemented, so runtime does not depend on this marker. Existing firmware dependency
warnings and Windows SDK/FFmpeg compiler warnings are retained in the detailed audits.
The initial staged whitespace check reported preserved Markdown hard breaks,
third-party notice whitespace and Windows CRLF scripts; authoritative/vendor notices
were not reformatted to remove these reports. A dedicated Rust advisory scanner was unavailable; Clippy/tests and the npm/Gitleaks
checks above were run, without claiming a Rust advisory-database scan.

Firmware is distributed as complete project source and pinned build instructions,
not a binary/private flashing kit. The exact previous qualified firmware is preserved
locally, but matching old ELF/relink materials were unavailable. Fresh firmware builds
are not substituted for the historical hardware qualification. Audio, arbitrary-angle
rotation, touch controls and calibrated classic-ESP32 temperature are unavailable.
Sudden power loss and broader filesystem/platform behavior remain qualification limits.
All three authoritative Muse documents were kept byte-for-byte unchanged.
