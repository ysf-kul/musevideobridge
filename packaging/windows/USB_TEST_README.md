# Muse Video Bridge — Windows USB and playback test kit

Extract the **complete ZIP** into a writable folder before running anything. The
desktop app requires Windows 10/11 x64 and the installed Microsoft WebView2 Runtime.
Keep the bundled tools and DLLs beside the app. This is an unsigned private test kit.

The ZIP contains:

- `Portable-App/Muse Video Bridge.exe` — rebuilt desktop app.
- `Portable-App/tools/muse-video-cli.exe` — command-line USB verification tool.
- `CYD-Flasher/` — checked current firmware, backup-first flash helper and evidence.
- `Run-Device-Tests.cmd` — English diagnostic runner that saves command output,
  transfer errors, playback measurements and your physical-screen observations.
- `Test-Media/SourceVideo.mp4` — detailed synthetic 640×480 source, 12 seconds long.
- `Test-Media/Baseline-q8-10fps.mvb` — historical 10-FPS baseline, not the new default.
- `Test-Media/Balanced-q8-15fps.mvb` — qscale 8, 15 FPS, area-scaling candidate.
- `Test-Media/Best-Quality-q4-15fps.mvb` — qscale 4, 15 FPS, Lanczos candidate.
- `Test-Media/Smooth-Playback-q10-20fps.mvb` — qscale 10, 20 FPS, area-scaling
  candidate; 2,101,312 bytes. All four use 320×240 and 4:2:0 chroma.

## 1. Install the firmware with a backup

This firmware targets the **original one-USB ESP32-2432S028R**, with a 2.8-inch
320×240 resistive ILI9341 display, classic ESP32 and 4 MB flash. Check the actual
board variant before using the kit; different controllers and pin assignments
require a matching build. Touch input is unavailable in this build.

Close the desktop app and all serial monitors. Connect the board directly to the
Windows PC. Double-click `Flash-Firmware-English.cmd`, or open a terminal in the
extracted combined ZIP folder and run:

```powershell
.\CYD-Flasher\Flash-CYD.cmd -Language en -ApplicationOnly
```

Select the board's **actual COM port** from the list and follow the English prompts.
The combined Performance kit's launcher identifies the chip, checks the firmware
images, saves a complete 4 MB flash backup with SHA-256, and compares the existing
partition table byte-for-byte with this build. It then writes and verifies only
`firmware.bin` at app0 / 0x10000. An incomplete backup or mismatched layout stops
before writing. This mode always requires a full backup and cannot use SkipBackup.
`Flash-Performance-English.cmd` is an explicit launcher for the same update.

Backups stay in `CYD-Flasher/Backups/`. Retain them privately for recovery; do not
include them in a shared test report. The application-only update preserves
bootloader, partition table, boot selection, NVS, app1 and filesystem partitions.
It does not format the SD card. Confirm that the new firmware is actually running
after reset. `Flash-Full-Firmware-English.cmd` retains the older four-image workflow
for an explicitly reviewed full installation. Do not use it to bypass a partition
mismatch when preserving existing flash data; there is no automatic full fallback.

The installed firmware reports version 0.2.1. The package provenance records
the package's `provenance.json` build revision; the included firmware hashes identify
this build.
The manifest records the PlatformIO build environment and compiler optimization;
the hardware report labels baseline `-Os` and performance `-O3` measurements separately.
The new Performance package uses the optimized original-CYD profile: actual
40-MHz pixel data, 20-MHz commands, SD20, aligned owned DMA strips, compressed
prefetch and one-block Espressif JPEG warmup for supported native geometry.
Playback's initial target follows the prepared file's encoded FPS. Legacy geometry
uses the compatible fallback; transforms retain the slower checked path. This
profile was measured on the owner's Linux-connected CYD; Windows runtime/COM
behavior still requires the owner test. The two-block experiment is not the default.

## 2. Transfer from the desktop app

**Leave the microSD card inside the ESP32 throughout the USB tests.**

1. Double-click `Start-App.cmd` (or start `Portable-App/Muse Video Bridge.exe`) and
   choose English in settings.
2. Select the actual USB serial COM port and connect. Record any exact connection
   error before retrying.
3. Select `Test-Media/SourceVideo.mp4`, convert it with the first-launch defaults
   (25 encoded FPS, qscale 8, bicubic, baseline 4:2:0, optimized Huffman tables), and
   choose **Send over USB**.
4. Wait through sending and device verification. Progress counts acknowledged
   bytes; the final success message is **Transfer verified by the device.**
5. Confirm playback on the LCD. Test pause, resume, stop, restart and seek.
6. Convert a different clip or change an encoding option to create a new digest.
   Start its upload, cancel partway through, and resend the **same prepared file**.
   Explicit cancellation must remove only that confirmed upload's incomplete
   files; resending starts at zero. A cancelled partial must not become a ready
   video. A completed file can be reused
   immediately and will not exercise cancellation.
7. Separately test interruption by disconnecting USB during sending, then
   reconnecting and sending the same prepared file. Record whether the device
   rebooted, the recovery offset and the final verification result.

Do not open the CLI or another serial monitor while the desktop holds the COM
port. The device verifies the completed SD file's size, SHA-256, container data and
JPEG frames before publishing it. Keep any error code and detail text; a progress
bar reaching the total byte count alone does not establish success.

## 3. Optional command-line evidence

The new `Run-Device-Tests.cmd` captures the following tests into a unique
`Device-Test-Results/` folder. It validates packaged CLI/media hashes and asks for
the desktop result separately, then runs one selected profile through the CLI.
Close the desktop app and other serial monitors first. Additional 30 FPS JPEG
candidates are included in refreshed kits; their filenames and options are listed
in `provenance.json`. These are measurement candidates, not qualified presets.
See `Portable-App/docs/WINDOWS_DEVICE_TESTS.md` for the runner and extended comparison.

Close the desktop first. Run these commands in PowerShell from the extracted
combined ZIP folder. **COM3 is only an example: replace it with your actual port.**

List ports and check device identity/status:

```powershell
& ".\Portable-App\tools\muse-video-cli.exe" ports --lang en
& ".\Portable-App\tools\muse-video-cli.exe" device --port COM3 --lang en
```

The following command measures the historical 10-FPS baseline; it cannot establish
maximum throughput. Render it across the display and collect an eight-second
sample with target 30 for historical comparison:

```powershell
& ".\Portable-App\tools\muse-video-cli.exe" device --port COM3 --lang en --upload ".\Test-Media\Baseline-q8-10fps.mvb" --benchmark-seconds 8 --target-fps 30 --display-full
```

The JSON output includes transfer confirmation, achieved FPS, average SD read,
decode and render times, `free_heap_bytes`, `minimum_free_heap_bytes` and serial
diagnostics. Save the complete output and any progress/error lines. The benchmark
pauses playback afterward.

Repeat with `Balanced-q8-15fps.mvb`, `Best-Quality-q4-15fps.mvb` and
`Smooth-Playback-q10-20fps.mvb`. Keep `--target-fps 30` for this first comparison.
The **encoded frame rate still caps achieved FPS**: a 10 FPS file cannot establish
the device's maximum rendering throughput. Compare read/decode/render times and
dropped frames alongside FPS. Full maximum-throughput qualification needs
additional files encoded at matching higher frame rates.

Then rerun at each file's normal target: 10 for Baseline, 15 for Balanced and Best
Quality, and 20 for Smooth Playback. Observe the actual LCD for each profile.
An uploaded file already present on the SD card may be verified and reused.
These prebuilt files can be tested with the CLI independently of the desktop's
matching-preview workflow. The Balanced file uses area scaling and the Best
Quality file uses Lanczos; the app's default remains bicubic.

For cancellation, first prepare a file with a new digest using the bundled tools.
Run the conversion once; its output **job folder** must not already exist. The
prepared file will be `video.mvb` inside that folder:

```powershell
& ".\Portable-App\tools\muse-video-cli.exe" convert ".\Test-Media\SourceVideo.mp4" ".\Test-Media\Cancellation-q7-10fps" --quality 7 --fps 10 --ffmpeg ".\Portable-App\tools\ffmpeg.exe" --ffprobe ".\Portable-App\tools\ffprobe.exe" --lang en
```

Cancel its upload after at least 32 KiB of acknowledged data:

```powershell
& ".\Portable-App\tools\muse-video-cli.exe" device --port COM3 --lang en --upload ".\Test-Media\Cancellation-q7-10fps\video.mvb" --cancel-after-bytes 32768
```

Expected cancellation returns a nonzero exit code and error code `cancelled`.
Resend that same prepared file without cancellation to start again at zero:

```powershell
& ".\Portable-App\tools\muse-video-cli.exe" device --port COM3 --lang en --upload ".\Test-Media\Cancellation-q7-10fps\video.mvb"
```

## 4. What to record

- Windows version, device/board variant, COM port and reported firmware build.
- Original and new desktop/firmware error codes and full detail text.
- File name, expected and device-confirmed size/digest, accepted and verified bytes.
- Achieved FPS, average SD read/decode/render times, free/minimum heap and serial
  overrun/drop/error figures from the benchmark output.
- What is visible on the physical display: fine detail, text, edges, color,
  compression artifacts and motion smoothness for each profile.
- Playback controls, cancellation, interruption, reconnect/recovery and final
  verification results.

First-launch conversion defaults are JPEG **qscale 8**, **25 encoded FPS**, native
**320×240 bounds**, bicubic scaling, **4:2:0** chroma and **optimized Huffman tables**.
Existing saved FPS, language and theme preferences remain unchanged. Lower qscale
means higher JPEG quality. Older Baseline/Balanced/Best Quality/Smooth Playback
files remain historical benchmark candidates; their names do not
establish measured smoothness or visually equivalent quality. Keep the
higher-quality option available while comparing the profiles on the 2.8-inch screen.

All prepared ESP32 files contain video frames only. The source MP4 is preserved;
source audio metadata may be displayed, but audio is excluded during ESP32
conversion, upload and playback. See `Portable-App/docs/FORMAT_AND_AUDIO_COMPARISON.md`
for the audio audit and measured output comparison.

Build and host/simulator verification passed. **Windows runtime, Windows flashing,
Windows USB end-to-end behavior and physical LCD quality still need direct owner
testing.** Linux/device results are documented separately and do not establish
Windows UART driver behavior or visible screen quality. A complete successful
desktop transfer, final verification and playback on the physical board are
required before claiming the Windows USB workflow works. This complete native
desktop workflow already passed on the Linux-connected physical CYD, including
final size/SHA-256 and playback; that does not prove Windows driver behavior.
The implementation,
original defects, device measurements and remaining qualification are documented in
`Portable-App/docs/REAL_HARDWARE_PERFORMANCE_REPORT.md`,
`CYD-Flasher/docs/USB_TRANSFER_AND_PLAYBACK_REPORT.md` and
`CYD-Flasher/docs/SERIAL_PROTOCOL.md`.
