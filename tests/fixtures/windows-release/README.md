# Windows release smoke input

`smoke.mp4` is a generated 320×240 H.264 test pattern with ten video frames and no
audio. It contains no user media. The Windows release smoke workflow uses it to
check the actual bundled FFprobe, scaling/JPEG conversion and decode verification.

Generated with FFmpeg 9.0.2:

```sh
ffmpeg -f lavfi -i 'testsrc2=size=320x240:rate=25' -t 0.4 \
  -c:v libx264 -pix_fmt yuv420p -crf 28 -an -movflags +faststart smoke.mp4
```

The fixture is part of Muse's MIT-licensed tests; it is excluded from application
distributions. A passing workflow establishes Windows runner startup/conversion,
not physical USB or user-observed playback acceptance.
