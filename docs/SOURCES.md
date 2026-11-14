# Sources and provenance

External sources accessed **2026-11-11**. Sources were fetched/read, not merely
listed as suggestions. Hardware drawings were visually opened at full resolution.
No source can establish an unknown installed component without revision matching.
Links are bibliographic references; original manufacturer drawings are not relicensed.

## Hardware

| ID | Source actually inspected | What it supports / limits |
| --- | --- | --- |
| H1 | [CYD README](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/0564a140c76de76ef34a8b7115044e7c6c852695/README.md) | Exact ESP32-2432S028R name, 2.8-inch 320×240 resistive module, SD and USB |
| H2 | [Original MCU schematic image](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/0564a140c76de76ef34a8b7115044e7c6c852695/OriginalDocumentation/5-Schematic/ESP32-2432S028-MCU.jpg) | V0.1, 2022-07-15, CH340C, 25Q32, SD SPI, power/reset. A mirrored original drawing, not a matched current PCB |
| H3 | [Original LCM schematic image](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/0564a140c76de76ef34a8b7115044e7c6c852695/OriginalDocumentation/5-Schematic/ESP32-2432S028-LCM.jpg) | V0.1, 2022-07-09, WROOM-32, LCD signals/reset, XPT2046 wires and backlight. Does not label LCD controller |
| H4 | [Original CYD TFT configuration](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/0564a140c76de76ef34a8b7115044e7c6c852695/DisplayConfig/User_Setup.h) | ILI9341_2 driver and GPIOs; community-compatible profile, not physical detection |
| H5 | [Explicit CYD2USB TFT configuration](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/0564a140c76de76ef34a8b7115044e7c6c852695/DisplayConfig/CYD2USB/User_Setup.h) | ST7789, BGR/inversion-off and GPIOs; alternate revision, not the owner's one-USB identity |
| H6 | [Original R board definition](https://github.com/rzeldent/platformio-espressif32-sunton/blob/a96fa42a56ac0613c1c46d706fe35a5dc99795c6/esp32-2432S028R.json) | Independent ILI9341/pin/flash corroboration. Its touch-on-SPI3 assignment cannot be copied while reserving that host for SD |
| H7 | [Board compatibility matrix](https://github.com/rzeldent/esp32-smartdisplay/blob/38e9ebabe3e4044159c4e95192d432607ed70588/BOARD_MATRIX.md) | Community R/Rv2/Rv3 distinctions; not a manufacturer revision certificate |
| H8 | [CYD PINS.md](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/0564a140c76de76ef34a8b7115044e7c6c852695/PINS.md) | HSPI LCD, VSPI SD, separate touch pins and input-only GPIOs |

An attempted [LCDWiki 2.8-inch ESP32 module URL](https://www.lcdwiki.com/2.8inch_ESP32_Module)
returned **HTTP 404**. It was not used as verified manufacturer documentation.
The CYD archive README explicitly says its OriginalDocumentation folder is outside
the repository author's licensing authority. Cite the images rather than vendoring them.

## ESP32 and telemetry

| ID | Source | Application |
| --- | --- | --- |
| E1 | [Espressif ESP32 datasheet](https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf) | Dual Xtensa LX6, clock, SRAM/RTC memory and mappings, SPI/GPIO constraints |
| E2 | [ESP-IDF 5.5.1 heap allocation](https://docs.espressif.com/projects/esp-idf/en/v5.5.1/esp32/api-reference/system/mem_alloc.html) and [heap diagnostics](https://docs.espressif.com/projects/esp-idf/en/v5.5.1/esp32/api-reference/system/heap_debug.html) | Capability-specific heap totals/free/largest/low-water; total SRAM is not free heap |
| E3 | [ESP32 SoC capabilities](https://github.com/espressif/esp-idf/blob/v5.5.1/components/soc/esp32/include/soc/soc_caps.h) and [temperature-driver build condition](https://github.com/espressif/esp-idf/blob/v5.5.1/components/esp_driver_tsens/CMakeLists.txt) | Classic ESP32 lacks the supported temperature-driver capability; driver is conditional |
| E4 | [Arduino-ESP32 3.3.12 misc HAL](https://github.com/espressif/arduino-esp32/blob/3.3.12/cores/esp32/esp32-hal-misc.c) | Classic temperatureRead uses an explicitly undocumented ROM function; not calibrated sensor acceptance |
| E5 | [ESP-IDF FreeRTOS SMP reference](https://docs.espressif.com/projects/esp-idf/en/v5.5.1/esp32/api-reference/system/freertos_idf.html) | Scheduler/runtime accounting considerations; CPU-load instrumentation must be enabled/tested |
| E6 | [ESP-IDF SD SPI host](https://docs.espressif.com/projects/esp-idf/en/v5.5.1/esp32/api-reference/peripherals/sdspi_host.html) | Explicit SPI host/pin configuration, initialization and actual clock APIs |
| E7 | [esptool basic commands](https://docs.espressif.com/projects/esptool/en/latest/esp32/esptool/basic-commands.html) | Chip/flash identification versus writing/erasing; version-specific CLI names |

## Media and libraries

| ID | Source | Application |
| --- | --- | --- |
| J1 | [JPEGDEC README](https://github.com/bitbank2/JPEGDEC/blob/32132971bcc032b5b14f1e1e968c242d8d986854/README.md), [header](https://github.com/bitbank2/JPEGDEC/blob/32132971bcc032b5b14f1e1e968c242d8d986854/src/JPEGDEC.h), [license](https://github.com/bitbank2/JPEGDEC/blob/32132971bcc032b5b14f1e1e968c242d8d986854/LICENSE) | Baseline Huffman, callbacks, native reduction and working buffers; Apache-2.0. Full progressive decoding not assumed |
| J2 | [TJpg_Decoder README](https://github.com/Bodmer/TJpg_Decoder/blob/master/README.md) | Alternative ChaN small-memory decoder; not benchmarked here |
| J3 | [LovyanGFX README](https://github.com/lovyan03/LovyanGFX/blob/master/README.md) and [license](https://github.com/lovyan03/LovyanGFX/blob/master/license.txt) | Alternative display/bus layer; inherited notices need audit |
| J4 | [TFT_eSPI library metadata](https://github.com/Bodmer/TFT_eSPI/blob/master/library.json) and [license notices](https://github.com/Bodmer/TFT_eSPI/blob/master/license.txt) | 2.5.43 metadata, compatible drivers, MIT/BSD/inherited obligations |
| V1 | [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html), [formats](https://ffmpeg.org/ffmpeg-formats.html), [codecs](https://ffmpeg.org/ffmpeg-codecs.html) | Scale/SAR/FPS/color processing, MJPEG/AVI capabilities; actual installed options were also checked |
| V2 | [Microsoft AVI RIFF reference](https://learn.microsoft.com/en-us/windows/win32/directshow/avi-riff-file-reference) and [FFmpeg AVI muxer source](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/avienc.c) | RIFF/list/index/padding structure; not proof that an embedded AVI parser is already implemented |
| V3 | [FFmpeg legal information](https://ffmpeg.org/legal.html) | Build-specific GPL/LGPL/external-library and redistribution obligations |
| D1 | [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/) | Real Linux/Windows webview/build requirements |
| D2 | [serialport-rs README](https://github.com/serialport/serialport-rs/blob/main/README.md) and [Cargo manifest](https://github.com/serialport/serialport-rs/blob/main/Cargo.toml) | Port APIs, optional libudev, Windows implementation, MPL-2.0; main alpha is not a stable pin |
| D3 | [Arduino-ESP32 3.3.12 release](https://github.com/espressif/arduino-esp32/releases/tag/3.3.12) | Actual current release candidate for compatibility testing |
| D4 | [PlatformIO v8.0.0 release](https://github.com/platformio/platform-espressif32/releases/tag/v8.0.0) and [pinned platform metadata](https://github.com/platformio/platform-espressif32/blob/v8.0.0/platform.json) | Current Arduino 3.3.12 mapping; package selection is not a completed firmware build |

A crates.io serialport API request returned **403**, and a GitHub latest-release
endpoint for serialport returned **404**. README/Cargo source was available; no
latest stable version is claimed from those failed endpoints. TJpg_Decoder LICENSE
and LICENSE.txt probes returned 404, so the alternative's complete notice audit
is deferred to the actual selected source version.

[The source snapshot register](evidence/source-snapshots.json) stores inspected
source URLs and SHA-256 for key successful downloads. Mutable HEAD/website URLs
can change; hashes identify this research snapshot, not supply-chain signatures.
Third-party source bodies/downloads remain outside the repository.

## Local sources and historical evidence

The required Muse documents were read completely and kept byte-for-byte unchanged.
Nearby project files were inspected, never modified. `../muse` provides actual shared
tokens and localization patterns. `../musedownloader` provides actual Tauri/Svelte/
Rust organization; `../musej` has the same downloader identity in its inspected files.

`../musevideobridgeold/docs/PHYSICAL_VERIFICATION.md` and `HARDWARE.md` were read as
**historical project reports**, not measurements performed in this task. They describe
dual-USB hardware, ROM ESP32-D0WD-V3 revision 3.1, 4 MiB flash, 8/10 FPS decode/draw
loops and a completely white display. Its later report lists approximately 12 ms
read, 29 ms decode and 34 ms draw work, with 231,076-byte minimum heap, while visible
playback still failed/pended. We did not rerun those tests or inspect private flash
backups and cannot establish whether the reported hardware is the current one-USB
board. Do not import its measurements as this project's verified specification.

The current owner explicitly confirms **one USB**, overriding the older report's
dual-USB assumption for this task. The portable architectural lesson is that software
draw counters, firmware build labels and successful SD reads do not prove a working
screen. This phase requires independent visible-panel acceptance later.
