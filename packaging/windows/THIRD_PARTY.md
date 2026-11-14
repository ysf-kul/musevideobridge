# Private test-build notices and source references

Muse is an independent personal, AI-assisted project. These unsigned packages are
provided to the project owner for qualification. Muse's own public distribution
license is not yet selected; source code and locked build instructions are supplied.
This document is not a completed public-release license audit.

- **FFmpeg/FFprobe:** unmodified FFmpeg-Builds Windows x64 LGPL shared variant,
  n9.0.2-49-gc179411bbb-20261111. LGPL 3 or later, with build-dependent upstream
  libraries' own licenses. The app launches separate processes; DLLs remain replaceable
  beside the executables. `licenses/ffmpeg/` includes upstream LGPL text, incorporated
  GPLv3, and exact embedded configuration; `sources/ffmpeg-source.tar.gz` contains
  FFmpeg commit c179411bbb. Full upstream dependency/build recipes are at
  https://github.com/FFmpeg-Builds/FFmpeg-Builds and binaries at
  https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases . Do not treat these source
  references as a completed review of every statically linked upstream dependency.
- **Espressif esptool:** official Windows amd64 5.6.0 executable, GPL 2 or later.
  Upstream copyright/license/readme are in `licenses/esptool/` and the v5.6.0 source
  archive is in `sources/esptool-source.tar.gz` (flashing kit only).
  https://github.com/espressif/esptool/tree/v5.6.0 . The kit excludes efuse/erase tools.
- **Manrope:** SIL Open Font License; notice is `licenses/Manrope-OFL.txt` in the
  portable package and `public/fonts/OFL.txt` in project source.
- **Desktop:** Tauri/dialog (MIT/Apache-2.0), Svelte (MIT), serde/serde_json/sha2/CRC
  (MIT/Apache-2.0), serialport (MPL-2.0), windows-sys (MIT/Apache-2.0), plus lockfile
  transitive dependencies. See source Cargo/package lockfiles and Phase 2/3 reports.
  FFmpeg is not linked into the Muse executable; WebView2 is a separately installed
  Microsoft runtime. SDK/UCRT components use Microsoft's Windows terms.
- **Firmware:** Arduino-ESP32 3.3.12 (LGPL-2.1) and its ESP-IDF/SDK components,
  TFT_eSPI 2.5.43 (FreeBSD), JPEGDEC 1.8.5 (Apache-2.0), ArduinoJson 7.4.2 (MIT).
  Included library notices are in the flashing kit's `licenses/firmware/`; project
  source includes pinned PlatformIO config and the hash-checked JPEGDEC patch.
  Upstream sources: https://github.com/espressif/arduino-esp32/tree/3.3.12 ,
  https://github.com/Bodmer/TFT_eSPI , https://github.com/bitbank2/JPEGDEC ,
  https://github.com/bblanchon/ArduinoJson . The supplied project source alone does
  not represent all corresponding SDK source or a completed LGPL relinking review.
- **Experimental firmware decoders:** ChaN TJpgDec (2021 permissive notice),
  adapted from pinned Espressif `idf-extra-components` source, and Espressif
  `esp_new_jpeg` 1.0.2 for classic ESP32. The latter license grants use on
  Espressif products; it is not an unrestricted MIT license for other hardware.
  Original notices and exact source/archive provenance are included in
  `licenses/firmware/tjpgd_fast*` and `licenses/firmware/esp_new_jpeg*` in the
  flasher, and under `firmware/vendor/` in the supplied source ZIP. Which
  optional codecs are linked depends on the recorded PlatformIO environment.

Archive origin and SHA-256 are recorded in `provenance.json`; individual package
files are in `SHA256SUMS.txt`. Do not publish these owner-test kits as audited releases.
Public distribution needs the project's chosen license, complete upstream attribution/
corresponding-source/relink review, signing and Windows/physical qualification.
