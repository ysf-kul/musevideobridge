# Muse Video Bridge — CYD Windows flashing kit

For a compatible existing Muse installation, `Flash-Performance-English.cmd`
uses the application-only update. It requires a complete 4 MB backup, compares
the saved partition table with the included one, then writes/verifies only app0
at 0x10000. Bootloader, partition table, boot selection, NVS, app1 and filesystem
partitions are preserved. A mismatch stops before writing. SkipBackup cannot be
used in this mode. Confirm the runtime firmware after reset, since a boot selection
for another app slot is preserved. Both `Flash-Firmware-English.cmd` and
`Flash-Performance-English.cmd` use this application-only procedure in the
standalone and combined kits. `Flash-Full-Firmware-English.cmd` selects the
separate four-image installation described below; it is not an automatic fallback
for a layout mismatch.

Target: **ESP32-2432S028R, 2.8-inch resistive, one USB connector**, original ILI9341
reference GPIOs. Firmware 0.2.1. The manifest records the build environment and
compiler optimization. Owner's chip is read-identified as ESP32-D0WD-V3,
revision 3.1, 4 MB flash, 240 MHz runtime CPU and no usable PSRAM. The connected
controller returned ILI9341 ID `0x9341`; configured pins and measured divider
clocks are in the included hardware report. PCB electrical tracing and visible
quality remain separate qualification. This kit is not for the dual-USB ST7789
or capacitive variants.

Windows 10/11 x64, Windows PowerShell 5.1+ and a working USB COM driver are required.
Python/PlatformIO/build tools are not required. Official Espressif esptool 5.6.0 is
included, together with firmware, SHA-256 manifest and source/license references.
The helper has host tests, but Windows execution and Windows physical flashing
remain untested. Linux/device evidence is recorded separately in the included
USB/playback investigation and does not validate this Windows helper.

## Update an existing compatible Muse installation

1. Extract the entire ZIP to a writable folder on Windows.
2. Connect the CYD directly to Windows with a good USB data cable (release it from
   VM USB passthrough). Close Muse Video Bridge and serial monitors.
3. Double-click `Flash-Firmware-English.cmd` for the English application-only
   update, or run `Flash-CYD.cmd -Language en -ApplicationOnly` in a terminal.
   The low-level `Flash-CYD.cmd` starts in Turkish and selects the older full
   workflow when ApplicationOnly is omitted.
4. Choose the actual listed COM port. The helper checks included hashes, identifies
   the classic ESP32 at 115200 baud and requires detected 4 MB flash.
5. Confirm the selected device by typing `FLASH`. The helper reads a complete 4 MB
   backup into `Backups/` before writing. A failed or incomplete read stops flashing.
   Keep backup files private; they can contain the prior firmware's saved data.
6. It compares the backed-up partition table byte-for-byte, then writes and
   verifies only `firmware.bin` at `0x10000` and resets the ESP32.
   Do not disconnect it while writing. An interrupted write may require rerunning.

The command runs PowerShell with execution policy bypass **for this process only**;
it does not change system policy. No full-chip erase, SD write/format, efuse change or
Wi-Fi setup is invoked. Flash writes necessarily erase only the affected flash sectors
before programming. Existing application firmware is overwritten. Unwritten flash
regions remain; no promise is made to migrate settings from an unrelated firmware.

## Explicit full installation

`Flash-Full-Firmware-English.cmd` uses the older four-image workflow below after
backup and confirmation. Review the existing board's layout before selecting it:
bootloader, partition table and boot selection are also written. It is intended
for a separately reviewed installation and does not preserve those regions.
Do not use it to bypass an application-only layout mismatch on a board whose
existing data must remain unchanged.

Only for that explicit full workflow, if you deliberately accept losing the existing firmware without a usable backup:
`Flash-CYD.cmd -Language en -SkipBackup`. This requires both `FLASH` and `SKIP`
confirmation and is never the default. Prefer resolving USB/read errors first.

| Address   | Included image            |
| --------- | ------------------------- |
| `0x1000`  | `firmware/bootloader.bin` |
| `0x8000`  | `firmware/partitions.bin` |
| `0xe000`  | `firmware/boot_app0.bin`  |
| `0x10000` | `firmware/firmware.bin`   |

These offsets/mode/frequency come from the actual PlatformIO environment, not another
CYD variant. `firmware-manifest.json` contains sizes and SHA-256; `SHA256SUMS.txt` also
covers helper/doc files. Checksums are integrity checks, not signatures.

## Physical acceptance

Without valid media the display must show:

```text
Muse Video Bridge
Waiting for video...
Insert SD card or send a video from your computer.
```

Check legible, correctly oriented text, background and actual connection/SD status.
Leave the microSD card inside the CYD. In the portable app, select an MP4, convert,
connect the listed COM port and choose **Send over USB**. Wait for device verification
before playback. Use the supplied app's current defaults for the first sample. Test pause/resume,
stop/restart, seek, X/Y, width/height/scale, four rotations and target FPS. Compare
actual TFT frame/colors/orientation with the reconstructed preview. Record measured
source/encoded/target rates, actual completed FPS, drops and heap at native/scaled/
rotated sizes, including a 30-FPS target. Temperature must
stay unavailable on this classic ESP32. No physical performance is promised by this kit.

Record Windows version, app/firmware versions, observed LCD behavior, card filesystem,
actual FPS/heap and any error output. Omit unique MAC addresses/private backup data.
Earlier VM backup reads lost bytes and USB re-enumerated. A subsequent complete
4 MB backup and application-only firmware updates succeeded; the Linux desktop
USB-to-installed-SD workflow and sampled TFT checks now have real device evidence.
These Linux results do not establish Windows execution. A successful Windows flash
verification alone still does not prove TFT rendering or SD playback.

## Connection and recovery

If connecting stalls, check Device Manager/CH340 driver, cable and port ownership;
try a direct USB port without a hub. If automatic reset cannot enter the ROM loader,
use the board's documented BOOT/RESET controls (if present); do not guess GPIO straps.
If backup fails, the helper stops before writing. If writing/verification fails, retain
the backup, fix the connection and rerun. Do not repeatedly erase the whole chip.

To restore a **complete 4 MB backup** after independently checking its `.sha256`, close
the app and run from this directory, substituting the real port and backup filename:

```powershell
.\tools\esptool.exe --chip esp32 --port COM3 --baud 115200 --before default-reset --after hard-reset write-flash --flash-mode keep --flash-freq keep --flash-size keep 0x0 .\Backups\YOUR-COMPLETE-BACKUP.bin
```

That manual restoration overwrites flash too. A partial read is not restorable full
firmware. The earlier 64 KiB VM read is not a full backup and is not included here.

Source copies for the project firmware/helpers and upstream esptool are included.
Build steps/library versions are in `docs/BUILD_AND_RUN.md`; upstream firmware dependency
references/notices are retained. Public-release license/relink/attribution review and
Windows/hardware qualification remain open. This is a private owner test kit.
