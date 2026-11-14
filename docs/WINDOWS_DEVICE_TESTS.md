# Direct Windows performance and USB evidence

Use the new combined owner-test ZIP and extract every file to a writable folder.
It includes the portable application, FFmpeg/FFprobe, checked CYD firmware and the
backup-first flasher. Windows 10/11 x64 needs the Microsoft WebView2 Runtime to run
the desktop app. Release the USB device from VMware before connecting on Windows.

Run `Flash-Performance-English.cmd` with the correct COM port. The new combined
kit's `Flash-Firmware-English.cmd` uses the same application-only procedure: read
a complete 4 MB flash backup, compare the existing partition table to this build,
then write and verify only app0 at 0x10000. Bootloader, boot selection, NVS, app1
and filesystem partitions stay untouched. A layout mismatch stops before writing;
there is no automatic full-flash fallback. Retain the backup privately. The helper
does not format the installed microSD card. Confirm the running firmware after
reset; an existing boot selection for another app slot is deliberately preserved.
See the flasher README for the matched board profile and restoration instructions.

Run `Start-App.cmd` and perform the actual product workflow with the SD card left
inside the CYD: select the included `Test-Media/SourceVideo.mp4`, convert, connect
the listed COM port, choose **Send over USB**, wait for final device verification,
and observe playback. Record exact errors before retrying. The application starts
in Turkish; its English preference and theme persist locally.

Close the app and serial monitors, then run `Run-Device-Tests.cmd`. It records your
desktop result as an owner observation, independently of the automated CLI test.
Select a prepared profile. The helper checks its SHA-256 and size, captures device
status and serial counters, transfers it through the same Rust protocol, uses the
prepared native dimensions, and records completed-frame playback statistics.
The kit's `provenance.json` supplies its exact decoder, render mode, byte order,
command/pixel clocks, prefetch setting, target FPS and application UART baud.
These settings are validated before the helper opens the selected port. Observe
the actual screen and enter motion/quality notes when prompted. It pauses playback
after the sample.

Logs are kept in a new `Device-Test-Results/` folder for every run. Each operation
has exact argument metadata, stdout JSON and raw stderr/progress. A failed operation
is retained and the helper exits with failure. Do not erase recovery files to work
around an error; the transfer client supports same-file checkpoint recovery.

For an extended comparison, open PowerShell in the extracted kit and run:

```powershell
.\Run-Device-Tests.ps1 -Port COM3 -Seconds 8 -AllProfiles
.\Run-Device-Tests.ps1 -Port COM3 -Seconds 8 -RenderMode dma -TftSpiHz 40000000 -BigEndian -NativeSize
.\Run-Device-Tests.ps1 -Port COM3 -Seconds 8 -FullSize
```

Replace COM3 with the actual listed port. The recorded application UART rate is
separate from the flasher's conservative 115200 baud. All-profile uploads can take
substantial time. The CLI operation duration includes verification and playback;
it must not be reported as UART sending time. Use explicitly reported transfer
metrics where present. File rate, requested rate and completed-draw rate are separate:
a file encoded at 10 FPS cannot demonstrate 30 actual FPS.

Named parameters override the recorded settings for deliberate comparisons.
`-Decoder` selects a codec, `-CommandSpiHz` independently selects the command
clock, and `-Prefetch:$false` disables read-ahead. `-Baud` is available when the
running firmware deliberately differs from the kit's compiled profile.
`-RenderMode legacy -TftSpiHz 20000000 -CommandSpiHz 20000000` restores the previous
display adapter; `blocks` uses continuous direct writes. The firmware reports
requested and observed divider clocks. Actual 80 MHz corrupted sampled video
pixels in the Linux hardware experiments and is rejected as clean playback.
DMA allocation failure is reported, never treated as successful DMA. None of
these runtime options persists in flash or changes the SD card format.

The additional 30 FPS candidates compare qscale 4/6/8, Lanczos versus area, and
native dimensions down to 240×180. Lower qscale means higher JPEG quality. Smaller
native images occupy a smaller region; scaling them to full screen can restore
the expensive TFT transfer and transformation work. Compare the same display size
when assessing image quality, and native size when isolating processing cost.
Research RGB565 streams are excluded from the kit's playable media because the
production MVB upload/player does not accept that format.

The host tests exercise package integrity, Windows argument escaping, simultaneous
stdout/stderr draining and failed-child exit handling. Linux PowerShell checks and
Windows cross-builds do not establish Windows runtime behavior or visible TFT
quality. A successful CLI transfer supplements the desktop acceptance test; the
required end-to-end product workflow remains desktop selection, USB transfer,
device verification and physical playback with the SD card inside the device.

When sharing evidence, include the operation logs and owner observations, but omit
private flash backups, unique device identifiers and unrelated personal media.
