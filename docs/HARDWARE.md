# Hardware research and verification status

Research date: 2026-11-11. Reference IDs resolve in [SOURCES.md](SOURCES.md).

Current physical observations were added on 2026-11-13–14. The connected CYD now has
real application-only firmware updates following a complete verified flash backup,
native Linux USB-to-installed-SD transfer and size/SHA-256/playback acceptance.
TFT readback identifies ILI9341; actual 40-MHz pixel transfers pass sampled playback
and separate full-screen GRAM checks, while tested actual 80 MHz corrupts output.
Runtime reports 240-MHz CPU and no PSRAM. The final selected image sustained
29.79923446 actual FPS at full 320×240 over five active minutes. All 25 separate
full-region checks passed. The owner reports clean, smooth playback and accepted
29.80 FPS as the stopping point for this investigation.
See [REAL_HARDWARE_PERFORMANCE_REPORT.md](REAL_HARDWARE_PERFORMANCE_REPORT.md) for
configuration-specific timing/heap results, completed repeated endurance and
qualification of the selected final image. The selected build is
`cyd_optimized_input_aligned`, with automatic direct DMA/one-block warmup,
40-MHz pixels,20-MHz commands and115200 UART. The optional SD-first-record
preload was measured and rejected; fast startup clears only outside the video
at the existing media-clock anchor.
Earlier phase-specific failures below are retained as historical evidence.

## Evidence levels

**Owner-confirmed** identifies the current product target, not electrical testing.
**Source-checked** means a cited drawing, datasheet or compatible configuration was
actually read. **Observed here** means a command in this phase produced the result.
**Historical** means a different local project reported it; it is not a new test.
**Unverified** means a physical measurement or exact revision match is still needed.

The mandatory request for verified specifications can be met for the published
reference design, but not for every component on the owner's unit. Unknown PCB
revision and independent electrical pin tracing remain explicit limitations;
controller identity and runtime RAM/PSRAM now have separate physical evidence.

## Current unit versus reference design

| Property | Current unit evidence | Source-checked reference / implication |
| --- | --- | --- |
| Model | Owner: ESP32-2432S028R | CYD reference describes this exact 2.8-inch model [H1] |
| Connectors | Owner: **one USB connector** | Start research with original-board configuration, not CYD2USB |
| PCB revision | Unknown | Original drawing **V0.1** is a drawing revision, not the owner's PCB revision |
| Touch | Owner: resistive | Original LCM schematic identifies XPT2046 [H3] |
| Pixels | 320×240 landscape configured and 76800-pixel TFT-memory readback verified | Original-compatible configurations use native 240×320; landscape is 320×240 [H4, H6] |
| LCD controller | Observed ID readback `0x9341`, working ILI9341 rendering/GRAM reads | Original config selects `ILI9341_2_DRIVER`; CYD2USB selects ST7789 [H4, H5] |
| SoC | ROM observed ESP32-D0WD-V3, revision 3.1, dual-core/240 MHz capability, 40 MHz crystal | Original schematic labels ESP32-WROOM-32; dual Xtensa LX6 up to 240 MHz [H2, E1] |
| Flash | ROM observed manufacturer `0x20`, device `0x4016`, detected **4 MB**, strap voltage 3.3 V | Original MCU sheet labels 25Q32: 32 Mbit = 4 MiB; actual manufacturer differs and is not assumed from that drawing [H2] |
| SRAM | Runtime heap measured per configuration; warmup trial free/minimum/largest block 35276/28844/30708 bytes | ESP32: 520 KiB on-chip data/instruction SRAM, plus 16 KiB RTC SRAM [E1]; total is not a framebuffer budget |
| PSRAM | Runtime `psram_bytes:0`, `free_psram_bytes:0` | No PSRAM shown on original sheets; reference WROOM/CYD profile expects none [H2, H6] |
| SD card | Owner: 4 GB installed; runtime card 3967811584 bytes, FAT total 3957325824; real transfer/hash/read tests | SD SPI driver/buffering/clock trials are recorded in the hardware report; power-loss qualification remains incomplete |
| USB bridge | Observed VID:PID `1a86:7523`, CH340-family descriptor | CH340C is on original MCU sheet; VID/PID cannot establish package subtype [H2] |
| Port | `/dev/ttyUSB0`; full backup and native application/video transfer completed | Actual serial reset-line release and checked ACKs were tested; Windows COM runtime is still unverified |

The community board matrix distinguishes original R / Rv2 ILI9341 and Rv3 ST7789.
These are community classifications, not a manufacturer-issued identification rule.
A connector count narrows the candidate; it cannot conclusively identify silicon.
No S3/C3, capacitive-touch, 3.5-inch or RGB-parallel CYD pinout is applicable.

## Schematics actually inspected

Both original manufacturer schematic images were opened at full resolution:

- MCU sheet V0.1, **2022-07-15** [H2]: USB Micro-B → CH340C → UART0;
  ESP32/LCM 3.3 V supplies, 25Q32 flash, RGB LED, SD socket, boot/reset circuitry.
- LCM sheet V0.1, **2022-07-09** [H3]: ESP32-WROOM-32, TFT connector,
  backlight transistor, XPT2046 with pull-up on IRQ, resistive panel and amplifier.

The TFT connector drawing does **not** explicitly identify the panel controller.
ILI9341 is corroborated by compatible configurations [H4, H6], not printed on the
LCM schematic. There is no matched schematic for the owner's unknown PCB revision.
The manufacturer's archived drawings are linked, not bundled or relicensed.

## Source-checked reference pin configuration

These GPIO numbers match the original MCU/LCM drawings, PINS.md and the
original-compatible configuration. Physical wiring on the current unit is unverified.

| Subsystem | Signal | ESP32 GPIO | Configuration / qualification |
| --- | --- | ---: | --- |
| TFT | SCK | 14 | HSPI / SPI2_HOST |
| TFT | MOSI / SDI | 13 | ESP32 → LCD |
| TFT | MISO / SDO | 12 | LCD reads may be absent/unusable; also a boot-strapping pin |
| TFT | CS | 15 | Active low; boot-strapping pin |
| TFT | DC / RS | 2 | Data/command; boot-strapping pin |
| TFT | Backlight | 21 | Active high per reference setup; PWM later if needed |
| TFT | Reset | EN/reset net | No independent GPIO; library reset value `-1` |
| SD | SCK | 18 | VSPI / SPI3_HOST |
| SD | MOSI / CMD | 23 | Separate from TFT bus |
| SD | MISO / DAT0 | 19 | Separate from TFT bus |
| SD | CS / DAT3 | 5 | Active low; boot-strapping pin |
| Touch | CLK | 25 | Dedicated physical wires, **not** the TFT bus |
| Touch | MOSI / DIN | 32 | Dedicated physical wires |
| Touch | MISO / DOUT | 39 | Input-only; no internal pull-up |
| Touch | CS | 33 | Hold high when unused |
| Touch | IRQ / PENIRQ | 36 | Input-only; external pull-up shown |
| UART0 | TX | 1 | CH340 RX |
| UART0 | RX | 3 | CH340 TX |
| Boot | BOOT | 0 | Used by ROM programming circuitry |
| Other | RGB R/G/B | 4 / 16 / 17 | Active low in original reference |
| Other | LDR | 34 | Input-only; not a temperature sensor |
| Other | Speaker DAC/amp | 26 | Audio not required by this product |

LCD and SD occupy the classic ESP32's two available general-purpose SPI hosts.
Touch has different CLK/MOSI/MISO wiring. Initial firmware should hold touch CS
high; if local touch controls are added, use a qualified software-SPI adapter with
bounded polling (for example 20–30 Hz), or explicitly schedule safe host remapping.
Do not instantiate another VSPI object on different pins and assume three independent
hardware buses. The smartdisplay reference uses SPI3 for touch; that arrangement
must not be copied while simultaneously reserving SPI3 for this application's SD.

Start LCD tests at **20 MHz**, then qualify **40 MHz**. The community setup's
55 MHz setting is not a verified electrical limit. Start SD identification at the
driver's initialization speed, then **10 MHz** operation; qualify 20 MHz if stable.
Set all pins explicitly. Do not use an SDMMC 4-bit default: the reference socket is
used in SPI mode. No dedicated card-detect GPIO is established by these drawings;
safe mount attempts while idle can detect insertion, and read failures handle removal.
Never reinitialize or poll-remount storage during an active transfer/read.

## Historical Phase 1 read-only inspection

`lsusb` identified the CH340-family adapter. `/dev/serial/by-id/` pointed to
`/dev/ttyUSB0`. The node was `root:uucp`, mode 0660; the session user was not in
`uucp`. No process was reported by the available `fuser` check. esptool **4.9.0**
was already present in the local PlatformIO tool directory.

After the owner authorized read-only identification, this exact ROM-only operation
was attempted using existing tools:

```sh
python3 -m esptool \
  --chip esp32 --port /dev/ttyUSB0 --baud 115200 \
  --before default_reset --after hard_reset --no-stub \
  --connect-attempts 1 flash_id
```

Result: **exit 2, errno 13, Permission denied** before opening the serial port.
No ROM query, reset or RAM stub occurred. A non-interactive privilege availability
check also failed because sudo required a password. No permission, group, driver,
device configuration or persistent data was changed. No repeated port probing was
performed after the error.

When access is provided through the owner's normal environment, esptool v4's
`chip_id` and `flash_id` with `--no-stub` can identify silicon and flash; v5 uses
hyphenated command/reset names. They may temporarily reset the board but do not
need flash writes. Do not run `write_flash`, `erase_flash`, `write_flash_status`,
`write_mem`, `load_ram`, or voltage override commands in this phase.

## Physical qualification boundaries

1. Read PCB/module/flex markings and locate the matching schematic or vendor BOM.
2. ROM chip/revision/flash identification and runtime RAM/no-PSRAM measurements are
   complete. Independent electrical TFT/SD pin tracing remains unverified; omit
   unique IDs from public records.
3. Controller ID, RGB stripe reads, sampled video pixels and separate full-frame
   memory checks have been verified. The owner reports clean, smooth playback.
   Long-duration qualification remains separate from those checks. An all-zero/
   all-one ID read alone is inconclusive; do not cycle drivers blindly.
4. Runtime heap/largest block, card/filesystem size and read latency have been
   measured. Extended optimal-path memory stability and power-loss behavior remain
   to be qualified. No SD formatting was needed or performed.
5. Confirm all four touch corners only if touch is implemented; resistive calibration
   is panel-dependent. Verify SD and touch coexistence if both operate.

The older local project reports dual-USB hardware, 4 MiB flash and a white-screen
failure. The current owner's **one-USB confirmation supersedes that connector
assumption**. Its measurements cannot identify this unit or validate this phase.
The relevant lesson is to require visible display acceptance independently of draw
callback counts. Detailed historical metrics are isolated in [SOURCES.md](SOURCES.md).

## Limitations

No ordinary H.264/MP4 decoder or hardware acceleration is proposed on the ESP32.
No PSRAM is assumed. Internal SRAM is partly instruction memory/SDK allocations;
520 KiB is not a free framebuffer budget. USB is UART through a bridge, not native
USB mass storage. TFT scanout has no verified synchronization/tearing signal.
Temperature lacks a supported calibrated classic-ESP32 sensor path for this product.
SD removal, power loss, filesystem corruption, poor cards and marginal SPI clocks
can all interrupt playback. Treat GPIO as 3.3 V; do not change board wiring or straps
as part of software discovery.

## Historical Phase 3 integration and qualification before hardware access

The desktop enumerates ports, implements version-1 controls/upload and renders matching
converted frames. Native Linux selection/conversion and simulator/PTY tests passed.
The owner subsequently authorized firmware flashing and physical tests. A temporary
tty ACL allowed normal-user identification. esptool 4.9/5.4 ROM queries observed:

- ESP32-D0WD-V3 revision 3.1, dual cores, 240 MHz capability, 40 MHz crystal.
- Flash manufacturer `0x20`, device `0x4016`, detected 4 MB, 3.3 V strap voltage.

No unique device identifiers are published. These readings verify chip/flash, not the
display controller, PCB revision, GPIO wiring or application runtime clock/RAM.

Full 4 MB backup reads failed before any write: esptool 4.9 at 460800 received 3845
of 4096 expected bytes; esptool 5.4 at 115200 received 4077 of 4096. USB disconnected/
re-enumerated. A read-only smaller-block adapter retained upstream MD5 checking and
successfully read **64 KiB**, but the full read still failed (449 of 512 bytes). The
64 KiB sample is not a complete backup. A faster read probe stalled and was stopped.

No `write-flash`, full-chip erase or SD write was performed. A final ROM query with
hard reset returned the board to its existing application. Original `root:uucp` 0660
ACL was restored; no persistent group/udev change or device-configuration change was made.
VM flashing was stopped after these failures. Private logs/partial read stay outside
packages under ignored verification storage.

At the owner's request, a Windows backup-first flasher and portable desktop test build
are supplied. The flasher checks chip/4 MB and file hashes, requires the matched profile,
reads a full backup, writes the four PlatformIO images and compares flash. It uses
115200 baud, DIO/40 MHz/4 MB from the real build environment. Host tests verify fail-closed
behavior; neither Windows execution nor physical installation is claimed as tested.

The `cyd_ili9341` firmware build still uses TFT HSPI at 20 MHz and SD VSPI at 10 MHz,
without PSRAM or a touchscreen UI. It compiles with 36,884 bytes static RAM and
627,214 bytes application flash. Engine allocation is 74,352 bytes plus task stacks,
queues/SDK/SD/JSON allocations; static build size is not measured runtime free heap.
At 20 MHz, full-screen RGB565 pixel payload alone takes at least 61.44 ms. No sustained
physical FPS guarantee is available, especially for scaling/90° rotation.

Supported quarter-turn/size/position code and null temperature/unsupported measurements
are integrated in the UI. Idle-runtime CPU utilization is compiled but not physically
validated. See [BUILD_AND_RUN.md](BUILD_AND_RUN.md) for explicit installation instructions
and normal-user serial-access guidance; installation was not run. The remaining board,
card, UART/control/removal and visible TFT acceptance tests need a reliable direct hardware
connection. [WINDOWS_TESTING.md](WINDOWS_TESTING.md) provides the owner's Windows fallback;
[PHASE_3_REPORT.md](PHASE_3_REPORT.md) separates all verification levels.
