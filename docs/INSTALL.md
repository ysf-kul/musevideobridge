# Install Muse Video Bridge

Download desktop packages and SHA256SUMS.txt from the
[GitHub Releases page](https://github.com/ysf-kul/musevideobridge/releases).
Version 0.3.0 is an unsigned first public release. Compare the downloaded archive's
SHA-256 with SHA256SUMS.txt before extracting/installing it. Source is MIT licensed;
third-party notices and corresponding dependency sources are supplied separately.

## Linux x86-64

The AppImage uses the same system runtime dependencies listed below. Install
GTK3, WebKitGTK 4.1, libudev and FFmpeg/FFprobe 8+ before launching it. It uses
normal XDG configuration/cache directories under `dev.muse.videobridge`, outside
the read-only AppImage. Download and compare SHA-256, then run:

```sh
chmod +x Muse-Video-Bridge-0.3.0-Linux-x86_64.AppImage
./Muse-Video-Bridge-0.3.0-Linux-x86_64.AppImage
```

If the host does not allow FUSE mounts through `/dev/fuse`, use the runtime's
extraction fallback (extracts to a temporary directory, then starts the app):

```sh
./Muse-Video-Bridge-0.3.0-Linux-x86_64.AppImage --appimage-extract-and-run
```

The AppImage runtime's original licenses, exact corresponding sources, patches
and rebuild recipe accompany the release as `AppImage-runtime-sources.tar.gz`.
The application uses installed system GUI libraries and does not auto-download
FFmpeg. Distribution/runtime qualification is listed in
[RELEASE_VERIFICATION.md](RELEASE_VERIFICATION.md).

The portable tarball is runtime-tested on CachyOS (Arch-based Linux, GTK3 and
WebKitGTK 4.1). Other distributions are unverified. It contains an optimized native
executable, embedded frontend/fonts, desktop entry, icon, notices, and a launcher.
It does not contain or require Node, Rust, a development server, or project sources.

Install GTK3, WebKitGTK 4.1, libudev, and FFmpeg/FFprobe 8 or newer using your
distribution's package manager. The FFmpeg build must include MP4 decoding, the
MJPEG encoder and scale filter with `reset_sar`. FFmpeg 9.0.2 was tested.
This binary requires glibc 2.34 or newer; the installed graphical libraries may
have additional distribution-specific requirements. Older Linux releases should
build from source rather than treating this package as compatible.

```sh
sha256sum -c SHA256SUMS.txt --ignore-missing
tar -xzf Muse-Video-Bridge-0.3.0-Linux-x86_64.tar.gz
cd Muse-Video-Bridge-0.3.0-Linux-x86_64
./Start-App.sh
```

The tarball opts into portable mode: settings live in `Data/config` and converted
videos in `Data/cache` alongside the executable. Extract into a writable folder
and move the entire folder to preserve settings and jobs.

Debian and RPM packages are also built. Package contents/dependencies are checked,
but installation and runtime on Debian/Ubuntu/Fedora/openSUSE are **unverified**.
They declare GTK3/WebKitGTK 4.1, libudev/systemd and FFmpeg 8+ dependencies; those
packages may not exist under the same names/version in every distribution.
Use your package manager's local package installation command where compatible.
Installed packages use the normal XDG configuration/cache paths under
`dev.muse.videobridge`, rather than portable `Data` storage.

A graphical session is required. Some virtual-machine graphics configurations
need `GDK_BACKEND=x11 WEBKIT_DISABLE_DMABUF_RENDERER=1 LIBGL_ALWAYS_SOFTWARE=1`;
these are troubleshooting options, not general requirements.

## Windows x64

Windows 10/11 x64 and the official
[Microsoft WebView2 Evergreen Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
are required. The optimized MSVC cross-build and packaged resources are verified. The published
ZIP passed native window creation and bundled conversion/decode on a Windows Server
2025 AMD64 runner. **Windows 10/11 interactive use, rendered UI, settings persistence
and physical USB are not yet verified**. See
[the Windows audit](RELEASE_AUDIT_WINDOWS.md) for the narrower runner scope.

1. Compare SHA-256 with PowerShell: `Get-FileHash .\Muse-Video-Bridge-0.3.0-Windows-x64-portable.zip -Algorithm SHA256`.
2. Extract the complete ZIP to a writable folder.
3. Run `Start-App.cmd` or `Muse Video Bridge.exe`.
4. Keep `tools`, its FFmpeg DLLs, and `portable-mode.json` beside the application.

No Node, Rust, Python or development files are required. Conversion tools are
included; no tool download occurs at runtime. Settings/cache/WebView2 data are
stored in the adjacent `Data` directory. Move the entire folder to preserve them.
This unsigned portable app has no installer or signing certificate; Windows may
show an unsigned application warning.

## Firmware and hardware

Use the ESP32-2432S028R resistive 2.8-inch CYD with **ILI9341**, 320×240 display,
classic ESP32, 4 MB flash, a USB data cable and FAT32 microSD. Similar CYD revisions
may use a different display/controller/pin layout: check
[HARDWARE.md](HARDWARE.md) before flashing. The original reference pins are configured;
no universal compatibility with every CYD revision is claimed.

The release supplies firmware **source**, not a prebuilt firmware/flashing kit.
Install the pinned PlatformIO environment following [BUILD_AND_RUN.md](BUILD_AND_RUN.md).
The selected production environment is `cyd_optimized_input_aligned`, firmware
version 0.2.1. Preserve the device's existing firmware with a complete verified
4 MB backup before writing any image. Then use the documented PlatformIO/esptool
build/upload procedure and verify the installed image. Do not erase the whole chip
or overwrite a known-good backup. Newly rebuilt firmware is build-verified; previous
hardware measurements refer to the preserved qualified implementation/image.

A source setup example on Linux (use the selected `COM...` port and the venv's
`Scripts/python.exe` on Windows):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r research/platformio-requirements.txt
.venv/bin/python -m platformio run -d firmware -e cyd_optimized_input_aligned
# Install esptool 5.6.0 in that isolated environment if not already available.
.venv/bin/python -m pip install esptool==5.6.0
# Close the desktop and inspect the actual selected device first.
.venv/bin/python -m esptool --chip esp32 --port /dev/ttyUSB0 --baud 115200 read-flash 0x0 0x400000 cyd-original-backup.bin
.venv/bin/python -m esptool --chip esp32 --port /dev/ttyUSB0 --baud 115200 verify-flash 0x0 cyd-original-backup.bin
# Only proceed after backup/verification succeeded and the board profile matches.
.venv/bin/python -m platformio run -d firmware -e cyd_optimized_input_aligned --target upload --upload-port /dev/ttyUSB0
```

Keep the verified 4,194,304-byte backup in a safe place. Use a new backup filename
for later backups so an earlier good backup is never overwritten. PlatformIO may
write bootloader/partition images as well as the application; inspect its generated
flash layout before upload. These setup commands are not new physical verification.

Use an existing FAT32 microSD card with free space for the prepared video and upload
checkpoint. The previously tested card was 4 GB; capacity is not hardcoded. The
maximum `.mvb` file size is 2 GiB minus one byte. FAT16/exFAT are not qualified.
The application never formats the card. **Keep the card installed inside the CYD
through USB transfer and playback.**

## Connect, convert and play

1. Power the CYD and connect the data-capable USB cable.
2. On Linux, check the actual tty's group/session ACL (`ls -l /dev/ttyUSB*`, `id`).
   Your administrator should grant normal-user access through the distribution's
   supported mechanism; groups can be `dialout`, `uucp`, or another group.
   Do not run the GUI as root or grant world-writable access.
   Windows may require the trusted CH340 driver matching the board.
3. Close other serial monitors, select the CYD's actual port in Muse Video Bridge,
   and click **Connect**. Refresh ports if needed. Do not select unrelated devices.
4. Select MP4, set qscale/encoding FPS, and **Convert**. Defaults are qscale 8 / 25 FPS.
5. Click **Send over USB** and wait for acknowledged progress and full device size/
   SHA-256 verification. 115200-baud transfer is roughly 9.9 kB/s in prior testing,
   followed by verification time. Transfer pauses playback while writing.
6. The verified video becomes playable. Use play/pause/resume/stop/restart/seek and
   position/scale/quarter-turn rotation controls. Target FPS differs from actual FPS.
7. Explicit **Cancel** removes only that acknowledged upload's incomplete files.
   After a disconnect/failure, retry the same converted digest to recover a validated
   checkpoint. Connect/disconnect and missing-device errors are shown in the app.

Desktop preview uses local prepared JPEGs and reported frame position. Unknown
video hashes require matching local conversion; unavailable telemetry is labeled.
There is no audio playback, arbitrary-angle rotation, or calibrated ESP32 temperature.
See [release verification](RELEASE_VERIFICATION.md) for the precise tested scope.
