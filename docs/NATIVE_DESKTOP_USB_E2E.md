# Native desktop USB acceptance — 2026-11-13

The actual Linux Tauri/WebKit application successfully selected and converted an MP4,
connected to `/dev/ttyUSB0`, sent the prepared video through **Send over USB**, and
received complete size and SHA-256 verification from the ESP32. The microSD remained
inside the ESP32; mounted-card export was not used. The device then loaded the file
and completed its real JPEG/TFT rendering path, reporting advancing playback frames.

This is real serial/device evidence, not the simulator or browser test bridge.
The native test window used separate verification configuration/cache. Native mouse
and keyboard actions selected files and operated controls. A local WebKit inspector
read the existing snapshot IPC and button positions without supplying device state.

| Measurement | Result |
| --- | --- |
| Source / prepared video | 640×480 / 320×240; source and prepared FPS 30; JPEG qscale 8, bicubic |
| Prepared duration / frames | 12 seconds / 360 frames |
| File size | 3,327,948 bytes |
| Tested UART rate | 115200 baud |
| Confirmed data receiving time | 338.022 seconds |
| Confirmed data speed | 9,845.35 bytes/second |
| Device verification time | 37.830 seconds |
| Upload handshake through completion | 376.114 seconds |
| Accepted / checkpointed / verified bytes | All 3,327,948 |
| SHA-256 | `5cef59ed0bd6410d54d93932bd450402676b71fe563e9c8d50bbc47f89f1807e`, matching prepared artifact |
| Device after upload | Playing frame 18, matching digest, no device error, `simulator:false` |

The first native upload attempt reproduced `upload_conflict` at `begin_upload`, before
any payload bytes were confirmed. Existing recovery files were preserved. After the
digest-isolated journal fix was installed, this retry transferred the entire file
from offset zero. The earlier serial reset/RTS investigation and firmware changes
belong to the main investigation report.

After automatic playback, the native **Restart**, **Pause**, **Play**, **Stop**, and
**Disconnect** controls were exercised. The device reported playing frame 45, paused
frame 99, resumed frame 147, then stopped with no displayed frame and disconnected.
The serial file descriptor was released before handing the device back to the
benchmark run, and the test window was closed normally. No user settings were reset,
unrelated files removed, or card formatted.

The result also includes 14 response timeouts/retries and 41 rejected wire frames
over the **connection lifetime**, including earlier activity. These counters are not
transfer-only failure counts. The checksummed retry workflow still reached matching
full-file verification. The read-only snapshot observer restarted during upload;
the desktop transfer remained active throughout.

Stable actual FPS and perceived TFT quality require the separate sustained hardware
benchmarks. The first playing snapshot's actual FPS was still null, while requested
playback FPS was 10 and prepared FPS was 30. A desktop preview screenshot is a
reconstruction of reported frames, not a camera image of the TFT. No Windows runtime
claim is made from this Linux test.

Structured results and evidence paths are in
[native-desktop-usb-e2e.json](evidence/native-desktop-usb-e2e.json).

## Final optimized desktop preparation and acceptance — 2026-11-14

The latest native Linux app was rebuilt with
`npm run tauri -- build --debug --no-bundle`. Its SHA-256 is
`a511703a8a8673542cd624d803a0356d3a792657cfe6851bd86f0cd2cd723fd3`.
Fresh verification settings opened in Turkish. English was selected through the
native interface and persisted across a clean close and relaunch. Light mode at
1160×860 and dark mode at 600×700 were checked with the actual native window.

The native MP4 picker selected the same 640×480, 30 FPS source used in the earlier
acceptance test. Clicking **Convert** with the new first-launch 25 FPS/qscale 8
defaults produced 300 prepared 320×240 frames, with bicubic scaling and optimized
JPEG Huffman tables. The 12-second output is 2,444,120 bytes with SHA-256
`9c19f630b9b2205dc5c25932c64b377f9b811dd3023dc5d68be73928bdee7fb7`.
A repeated native selection/conversion after the process restarted produced the
same bytes and digest. Separate verification configuration/cache preserved user
settings and source media.

The final native workflow passed against the parent-flashed, hash-verified
`cyd_optimized_input_aligned` firmware: 665,888 bytes, SHA-256
`3de1a18fdd0eb993c6fb53a311317ebe62e2b82b07ec634f030d21496483343f`.
The desktop connected to `/dev/ttyUSB0`, cancelled an acknowledged upload, resent
from offset zero, verified the complete SD file, and observed automatic playback
of that same digest. The card remained inside the ESP32. Native tests did not
change the firmware's automatic decoder, DMA, display-clock or SD configuration.

| Final native measurement | Result |
| --- | --- |
| Source / prepared / requested FPS | 30 / 25 / 25 |
| Prepared resolution / frames / duration | 320×240 / 300 / 12 seconds |
| File size / accepted / checkpointed / verified bytes | All 2,444,120 |
| Tested UART rate / chunk size | 115200 baud / 1,024 bytes |
| Confirmed data receiving time / speed | 246.611625 seconds / 9,910.806 bytes/second |
| Final device verification time | 15.933069 seconds |
| Upload handshake through completion | 262.909877 seconds |
| Resumed offset | 0 |
| Verified digest | `9c19f630b9b2205dc5c25932c64b377f9b811dd3023dc5d68be73928bdee7fb7` |
| Automatic playback | Matching digest, playing frame 1/300, zero device errors |
| Normal playback telemetry | Approximately 25 actual FPS; sample median 24.997488 |

**Cancel** was queued after a snapshot confirmed 105,472 uploaded bytes. The last
sampled acknowledged progress before the terminal `cancelled` state was 124,928
bytes; the exact final SD cancellation offset was not separately recorded. Both
samples exceed 64 KiB. The host confirmed cancellation without an unconfirmed
cleanup warning, reported SD free space returned to its earlier value, and the
subsequent full transfer began at offset zero. Only this owned incomplete upload
was discarded; unrelated videos and recovery data were preserved.

After upload, **Restart**, **Pause**, **Resume**, size (160×120), X/Y position,
90°/180°/270° rotation, reset to the native 0° defaults, target FPS changes, **Stop**,
and **Disconnect** all produced the expected actual device states with no device
errors. Requested 15 FPS reported 14.997585 FPS while playing. The 25 FPS sample
range was 24.918728–25.497552: these short-window completed-frame telemetry values
include transient startup counts and repeated cached snapshots, not a separate
sustained speed benchmark. Exact-image endurance and physical pixel checks belong
to the hardware performance report.

The VM window needed restoration, and XTest pointer events then missed their
intended controls. File selection/conversion had already used the actual native
picker and buttons. The final transfer and live controls therefore used one local
WebKit inspector to click the **production DOM buttons** and dispatch their normal
input/change events. The existing Svelte → Tauri → serial handlers and real snapshot
IPC were used throughout; device state and backend transfer were not substituted.
Initial window/focus automation failures and GTK file-picker warnings are retained
in the verification logs. Initial serial discovery recovered from two ACK timeouts.
Final diagnostics contain two timeouts/retries and five rejected wire frames over
the connection lifetime, including discovery and cancellation activity; they are
not transfer-only failure counts.

Native disconnection released the serial descriptor. An actual `WM_DELETE_WINDOW`
close event then closed the test window and the native process exited. The command
runner reported app exit 143 and inspector exit 1 after WebKit closure; normal app
exit 0 was not demonstrated. These teardown statuses occurred after successful
verification and serial disconnection, while the controls harness itself exited 0.
No user settings were reset, unrelated files removed, or card formatted. This is
Linux native runtime validation; Windows runtime and independent camera verification
of every transform remain unverified.
Structured final results and evidence paths are recorded in
[final-native-desktop-usb-e2e.json](evidence/final-native-desktop-usb-e2e.json).
