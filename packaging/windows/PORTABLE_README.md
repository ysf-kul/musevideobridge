# Muse Video Bridge — Windows x64 portable test build

Extract the entire ZIP to a writable folder, then double-click
`Start-App.cmd` (or `Muse Video Bridge.exe`). No installer, Python, Node.js, Rust or administrator session
is needed. Windows 10/11 x64 and Microsoft Edge WebView2 Runtime are prerequisites;
install WebView2 from https://developer.microsoft.com/microsoft-edge/webview2/ if absent.
This is an unsigned private test build. Windows runtime has not been tested here.

The application starts in Turkish. Use its language selector for English and its
theme selector for light/dark/system. Preferences persist in `Data/config`, conversion
jobs in `Data/cache`, and Windows WebView2 profile data in `Data/cache/webview` beside
the executable. Move the whole folder to keep settings and prepared previews. Do not
run from inside the ZIP or a read-only directory. Data/cache is retained and can grow.

FFmpeg/FFprobe and their shared libraries are supplied in `tools/`. Default tool
settings resolve beside the executable; explicit user-selected paths take precedence.
Keep all DLLs with the tools. These are unmodified LGPL shared binaries with included
license, provenance and source/build references. Linux/development installs still use
external tools. Windows key workflows and tool execution need your runtime test.

## Quick test

1. Flash the separately supplied CYD kit first; close the app during flashing.
2. Select a local MP4. First-launch defaults are qscale 8, encoding 25 FPS, bicubic
   preserve-aspect scaling, baseline 4:2:0 and optimized Huffman tables. Existing
   saved FPS/language/theme choices remain unchanged. Smaller qscale means higher
   JPEG quality and usually bigger frames.
3. Convert and wait for verification. If a frame exceeds 48 KiB, explicitly choose
   lower quality and retry; the app does not change your choices.
4. Keep the FAT32 microSD card inside the CYD. Connect the device directly to
   Windows with a USB data cable. Select its listed COM port in the app and connect.
   Close the flasher and other serial monitors before opening the port.
5. Choose **Send over USB**, wait for acknowledged progress and final device
   verification, then inspect playback on the actual display. The app never formats
   the card. A completed transfer must be verified on your physical device before
   treating this test build as hardware-qualified.
6. Cancel another upload: only its confirmed incomplete files should be removed.
   Resend the same prepared file to start again at zero. Separately disconnect
   USB during another upload, reconnect and resend to test checkpoint recovery.
   Record the exact error code/details if any stage fails. A CH340
   driver is needed only if Windows does not expose the board's COM port.
7. Test pause/resume/stop/restart/seek, position, size, scale, 0/90/180/270° and target FPS.
   A change remains pending until firmware confirms it. Compare the reconstructed
   desktop preview against the actual display.

USB upload is the intended normal workflow and pauses playback while it owns the SD
card. It uses 115200 baud, so large files take time. Firmware must already be installed.
Mounted FAT32 export is also available with verification and Windows safe eject.
The desktop never flashes firmware automatically.
Converted ESP32 MVB files contain video frames only; no unused source audio is
encoded, stored or transferred. The original MP4 remains unchanged.

The separately supplied optimized-CYD Performance firmware uses 40-MHz TFT pixels,
20-MHz commands, SD20, checked DMA strips, compressed prefetch and one-block
Espressif JPEG warmup for supported native geometry. The initial playback target
follows the prepared file's encoded FPS; position/size/rotation/FPS controls remain
available. Legacy geometry and expensive transforms retain a compatible fallback.
These paths were measured on the Linux-connected CYD. Windows runtime, flashing
and USB driver behavior are still unverified by those Linux tests.

Observe actual FPS separately from requested FPS. Heap/CPU/SD values must come from
firmware; unsupported or stale values show unavailable. Classic ESP32 temperature,
arbitrary angles, MP4 decoding/audio on the device and a touch UI are unavailable.
Current-frame preview requires the matching local converted video, not just any MP4.

## Troubleshooting

- No window: verify WebView2 and extract the complete package. Keep it in a writable
  folder. Windows x64 is required; this is not an ARM64 build.
- Tools missing: keep `tools/` beside the executable; reset default tool settings or
  select trusted executable paths. All FFmpeg DLLs must remain together.
- Port in use: close the flasher/serial monitor. Select the actual listed port, not
  a hardcoded COM number. Reconnect after unplugging/restarting the board.
- White/blank TFT: firmware profile targets the original one-USB resistive ILI9341
  CYD. The owner's connected unit returned ILI9341 ID 0x9341 and passed actual 40-MHz
  output checks, but another PCB/controller needs its matching profile. Inspect
  markings instead of blindly changing drivers.
- Device statistics stop: stale state is unavailable; reconnect. A flash-verification
  success does not prove TFT/SD behavior.

`SHA256SUMS.txt` lists all package files. `provenance.json` records upstream archive
digests. Checksums detect corruption; they are not a code signature. This package is
for the owner's Windows qualification, not a signed or fully release-audited distribution.
See the supplied project documentation for limits and current verification evidence.
