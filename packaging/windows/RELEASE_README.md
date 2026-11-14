# Muse Video Bridge — Windows x64 portable

Version 0.4.1 is a pre-release. Reference-device Linux USB transfer and queue
acceptance passed. Physical touch-button actions, calibration and visual acceptance
remain unverified; Windows physical USB still needs acceptance on that platform.

Extract the complete ZIP into a writable folder and open `Start-App.cmd` or
`Muse Video Bridge.exe`. Windows 10/11 x64 and Microsoft Edge WebView2 Runtime
are required. Install WebView2, if absent, from
https://developer.microsoft.com/microsoft-edge/webview2/ . No development tools
are required to run this package. This executable is unsigned. The optimized
Windows build and its package were verified from Linux during packaging. The
release notes record any later Windows runner startup/conversion smoke results;
Windows physical USB communication and interactive controls still require testing
on Windows.

The application starts in Turkish. Select English and light/dark/system theme in
the application. Settings are saved beside the executable in `Data/config`,
converted files and previews in `Data/cache`, and the WebView2 profile in
`Data/cache/webview`. Preserve the existing `Data` folder when upgrading and copy
it beside the new executable. Keep the entire folder together when moving it. Do not run
from inside the ZIP or a read-only folder.

FFmpeg and FFprobe are included in `tools/`, together with their replaceable shared
libraries. Keep all DLLs with these tools. This focused LGPL build supports MOV/MP4
and MJPEG input with available built-in decoders, including H.264, HEVC and MPEG-4.
Codecs requiring external libraries, such as AV1, are unavailable in these bundled
tools; select trusted compatible FFmpeg/FFprobe paths in settings if needed.

1. Install the matching CYD firmware using the repository's setup instructions:
   https://github.com/ysf-kul/musevideobridge/blob/main/docs/BUILD_AND_RUN.md .
   The hardware target is the original ESP32-2432S028R resistive 2.8-inch CYD with
   ILI9341 display and 4 MB flash. Inspect board markings and back up its flash
   before installation. The desktop does not flash firmware automatically.
2. Keep a FAT32 microSD card installed inside the CYD. Connect a USB data cable and
   select its detected COM port in the desktop application. A board-specific CH340
   driver may be needed if Windows does not expose a COM port. Close other serial
   applications before connecting.
3. Select a local MP4 and convert. First-launch defaults are JPEG qscale 8, 25 FPS
   and preserve-aspect scaling. Lower qscale increases quality and file size.
   Source MP4 files remain unchanged. The `.mvb` output contains video only.
4. Choose **Send over USB** and wait for acknowledged progress plus final
   size/SHA-256 verification. Matching 0.4.1 firmware uses 921600 baud; old firmware
   remains at 115200 until updated. Upload pauses
   playback while using the card. Cancellation removes the confirmed upload's
   incomplete files; interrupted transfers retain a checkpoint for retrying the
   same converted file. Neither the desktop nor the firmware formats the card.
5. Manage up to 12 prepared videos in the device queue: select, reorder, remove,
   and navigate next/previous. Choose Play Once, Loop Current, or Loop Queue.
   Use Add videos and Prepare all selected videos for a batch, then select prepared
   items and send them together or write them to a mounted FAT32 SD card.
   Reconnection refreshes actual device state. Firmware 0.4.1 saves queue order and
   mode on SD and uses 921600 baud. Updating only the desktop leaves 0.4.0 firmware
   at 115200 baud. The old rate remains available as a compatibility firmware build.
6. Tap the CYD to reveal Previous, Play/Pause, and Next controls for four seconds.
   Physical touch calibration and display appearance remain unverified.
7. Play, pause, resume, stop, restart and seek. Position, size, scale, quarter-turn
   rotation and target FPS are adjustable. The preview reconstructs matching local
   JPEG frames; it is not a screen capture. Unsupported telemetry is unavailable.

Previously measured on the Linux-connected reference device: 320×240 full-screen
playback sustained approximately 29.80 actual FPS over five active minutes, with
25 full-region TFT-memory checks reporting zero mismatches and zero DMA ownership
errors. These prior measurements do not establish Windows runtime or USB behavior.
The requested FPS and actual FPS are separate values. Audio, arbitrary rotation,
and calibrated temperature are unavailable. Version 0.4.0 measured 29.73 actual FPS
over 228 active seconds across 19 full-screen clips on the reference device,
excluding the initial overlay-affected clip. All 20 separate full-region readback
checks passed with zero mismatches and DMA ownership errors. This does not qualify
physical touch-button actions or display appearance.
The 0.4.1 firmware was application-only flashed and hash-verified on the reference
CYD. A 1,539,876-byte USB transfer confirmed approximately 28.8 kB/s, with measured
verification and readiness, on Linux. Earlier 115200 tests were roughly 9.9 kB/s;
these different-file measurements are not a Windows throughput guarantee. The
current FPS regression and its scope are in the 0.4.1 repair report. The arithmetic
baud increase is not a measured eightfold transfer-speed guarantee.

Documentation and source: https://github.com/ysf-kul/musevideobridge .
Downloads: https://github.com/ysf-kul/musevideobridge/releases .
`SHA256SUMS.txt` covers every package file. Checksums detect corruption and are not
a code signature. Muse is MIT licensed; third-party notices are in `licenses/`.
The same GitHub release supplies exact corresponding dependency/FFmpeg source and
rebuild instructions. FFmpeg DLLs and executables may be replaced independently.
