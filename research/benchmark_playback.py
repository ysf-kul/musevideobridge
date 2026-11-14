#!/usr/bin/env python3
"""Compare validated encodings; optionally upload/benchmark using the real Rust client.

Host measurements never qualify ESP32 FPS or perceived display quality. Output must
be a new directory. No flashing, card formatting or existing-media deletion occurs.
"""
import argparse
import hashlib
import json
import os
import re
import statistics
import struct
import subprocess
import time
from pathlib import Path


PROFILES = [
    ("baseline", 8, 10, 320, 240, "bicubic"),
    ("best-quality-candidate", 4, 15, 320, 240, "lanczos"),
    ("q6-20fps", 6, 20, 320, 240, "area"),
    ("balanced-candidate", 8, 15, 320, 240, "area"),
    ("q8-20fps-area", 8, 20, 320, 240, "area"),
    ("q8-20fps-lanczos", 8, 20, 320, 240, "lanczos"),
    ("smooth-candidate", 10, 20, 320, 240, "area"),
    ("q12-20fps-comparison", 12, 20, 320, 240, "area"),
    ("288x216-comparison", 8, 20, 288, 216, "area"),
    ("q8-30fps", 8, 30, 320, 240, "area"),
    ("q4-30fps-lanczos", 4, 30, 320, 240, "lanczos"),
    ("q6-30fps-lanczos", 6, 30, 320, 240, "lanczos"),
    ("q8-30fps-lanczos", 8, 30, 320, 240, "lanczos"),
    ("q10-30fps-area", 10, 30, 320, 240, "area"),
    ("288x216-30fps", 8, 30, 288, 216, "lanczos"),
    ("256x192-30fps", 8, 30, 256, 192, "lanczos"),
    ("240x180-30fps", 8, 30, 240, 180, "lanczos"),
]


def run(argv, log, expected=0, timeout=600):
    started = time.monotonic()
    command = [str(x) for x in argv]
    try:
        result = subprocess.run(command, capture_output=True,
                                text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        log.write_text(decoded(error.stdout) + "\n" + decoded(error.stderr)
                       + f"\nProcess exceeded {timeout} seconds.\n")
        log.with_suffix(".command.json").write_text(json.dumps(
            {"argv": command, "timeout_seconds": timeout, "result": "timeout"}, indent=2) + "\n")
        raise
    log.write_text(result.stdout + "\n" + result.stderr)
    log.with_suffix(".command.json").write_text(json.dumps(
        {"argv": command, "exit_code": result.returncode,
         "elapsed_seconds": time.monotonic() - started}, indent=2) + "\n")
    if result.returncode != expected:
        raise RuntimeError(f"{argv[0]} exit {result.returncode}: {log}")
    return result, time.monotonic() - started


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def packets(path):
    # Files are first validated by the production core/decoder, including all
    # offsets/CRCs. Research only reads their bounded JPEG records afterward.
    with path.open("rb") as source:
        header = source.read(96)
        count = struct.unpack_from("<I", header, 28)[0]
        for _ in range(count):
            length = struct.unpack("<IIII", source.read(16))[0]
            if not 1 <= length <= 49152:
                raise ValueError("JPEG record outside production limits")
            data = source.read(length)
            if len(data) != length:
                raise ValueError("Short JPEG record")
            source.read((-length) % 4)
            yield data


def bandwidth_bounds(width, height, fps):
    """Calculated payload bounds only; these do not establish stable bus clocks."""
    frame_bytes = width * height * 2
    return {
        "rgb565_frame_bytes": frame_bytes,
        "raw_sd_bytes_per_second_required": frame_bytes * fps,
        "tft_payload_lower_bound_ms": {
            str(mhz): frame_bytes * 8 / (mhz * 1000) for mhz in (20, 40, 80)
        },
        "bound_scope": "calculated; excludes bus overhead; clocks unqualified on physical display",
    }


def raw_candidate(args, source, job, artifact, name, reference, canvas, output):
    """Export source RGB565 stripes for a future SD/TFT benchmark, without JPEG decode.

    The raw stream is deliberately outside production MVB v1 and USB upload. A
    sidecar states geometry, frame count, endianness and checksum. No framebuffer
    allocation or embedded playback capability is implied by this host export.
    """
    width, height = artifact["header"]["width"], artifact["header"]["height"]
    frames = artifact["header"]["frames"]
    fps = artifact["options"]["fps"]["numerator"] / artifact["options"]["fps"]["denominator"]
    bounds = bandwidth_bounds(width, height, fps)
    expected = frames * bounds["rgb565_frame_bytes"]
    row = {"format": "RGB565 big-endian sequential stream", "width": width,
           "height": height, "frames": frames, "encoded_fps": fps,
           "expected_file_bytes": expected, **bounds, "hardware_tested": False,
           "production_playback_supported": False, "actual_device_fps": None,
           "visible_quality": "not evaluated on physical display"}
    # Keep an opt-in research invocation from creating arbitrarily large raw files.
    if expected > 512 * 1024 * 1024:
        row["status"] = "skipped: raw export would exceed 512 MiB per profile"
        return row
    prefix = "bwdif=mode=send_frame," if artifact["input"]["interlaced"] else ""
    settings = artifact["options"]
    crop = f",crop={width}:{height}" if settings["fit"] == "Crop" else ""
    aspect = "increase" if crop else "decrease"
    rate = f"{settings['fps']['numerator']}/{settings['fps']['denominator']}"
    filter_value = (f"{prefix}fps={rate},scale={settings['width']}:{settings['height']}:"
                    f"flags={settings['scaling']}:force_original_aspect_ratio={aspect}:"
                    "force_divisible_by=2:reset_sar=1:out_range=full:out_color_matrix=bt601"
                    f"{crop},format=rgb565be")
    raw = job / "source.rgb565"
    _, elapsed = run([args.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
                      "-protocol_whitelist", "file,pipe", "-i", source,
                      "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", filter_value,
                      "-frames:v", frames, "-threads", "1", "-f", "rawvideo", raw],
                     output / f"{name}-raw-export.log")
    if raw.stat().st_size != expected:
        raise RuntimeError(f"Raw output size/count mismatch for {name}")
    raw_input = ["-f", "rawvideo", "-pixel_format", "rgb565be", "-video_size",
                 f"{width}x{height}", "-framerate", rate, "-i", raw]
    run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror", *raw_input,
         "-f", "null", "-"], output / f"{name}-raw-verify.log")
    sample = job / "raw-sample.png"
    run([args.ffmpeg, "-hide_banner", "-loglevel", "error", *raw_input,
         "-frames:v", "1", sample], output / f"{name}-raw-sample.log")
    ssim, _ = run([args.ffmpeg, "-hide_banner", "-i", sample, "-i", reference,
                   "-filter_complex", f"[0:v]{canvas}[a];[1:v]{canvas}[b];[a][b]ssim",
                   "-frames:v", "1", "-f", "null", "-"], output / f"{name}-raw-ssim.log")
    score = re.search(r"All:([0-9.]+)", ssim.stderr)
    row.update({"status": "host_export_verified", "file": raw.name,
                "file_bytes": expected, "sha256": digest(raw),
                "host_export_seconds": elapsed,
                "first_frame_ssim": float(score[1]) if score else None,
                "frame_layout": "contiguous rows; 2 bytes/pixel; RGB565 MSB first; no headers/padding",
                "sample": sample.name})
    (job / "source.rgb565.json").write_text(json.dumps(row, indent=2) + "\n")
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cli", type=Path, default=Path(
        "target/debug/muse-video-cli.exe" if os.name == "nt" else "target/debug/muse-video-cli"))
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable name or path")
    parser.add_argument("--ffprobe", default="ffprobe", help="FFprobe executable name or path")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--port", help="Explicit real ESP32 port, e.g. /dev/ttyUSB0")
    transport.add_argument("--simulator", type=Path, help="Host functional exercise, never ESP32 timing")
    parser.add_argument("--benchmark-seconds", type=int, default=8)
    parser.add_argument("--target-fps", type=int,
                        help="Requested device cap, 1..30; defaults to each profile's encoded FPS")
    parser.add_argument("--decoder-mcus", type=int, default=32)
    parser.add_argument("--row-render", action="store_true", help="Compare the previous row renderer")
    parser.add_argument("--render-mode", choices=("legacy", "blocks", "dma"),
                        help="Explicit firmware display strategy; optional DMA is not hardware-qualified")
    parser.add_argument("--big-endian", action="store_true",
                        help="Request JPEG output in TFT wire byte order for a display comparison")
    parser.add_argument("--tft-spi-hz", type=int, choices=(20000000, 40000000, 80000000),
                        help="Explicit TFT clock candidate; stability requires physical screen evaluation")
    parser.add_argument("--native-size", action="store_true",
                        help="Benchmark at prepared native dimensions instead of stretching to 320x240")
    parser.add_argument("--raw-rgb565", action="store_true",
                        help="Also export source RGB565 and bandwidth bounds; host only, not MVB playback/upload")
    parser.add_argument("--profiles", nargs="+", help="Optional subset of profile names")
    parser.add_argument("--test-recovery", action="store_true",
                        help="Cancel after confirmed bytes then resume the first profile")
    args = parser.parse_args()
    if args.port and os.name != "nt" and not Path(args.port).exists():
        raise SystemExit(f"Real device unavailable: {args.port}; no hardware tests run")
    if args.test_recovery and not (args.port or args.simulator):
        raise SystemExit("--test-recovery requires an explicit transport")
    if not 2 <= args.benchmark_seconds <= 600 or not 1 <= args.decoder_mcus <= 32:
        raise SystemExit("Benchmark seconds must be 2..600; decoder MCUs must be 1..32")
    if args.target_fps is not None and not 1 <= args.target_fps <= 30:
        raise SystemExit("Target FPS must be 1..30")
    cli = args.cli.resolve()
    tools = ["--ffmpeg", args.ffmpeg, "--ffprobe", args.ffprobe]
    source = args.input.resolve()
    if not source.is_file() or not cli.is_file():
        raise SystemExit("Input and rebuilt CLI must both exist")
    selected = [p for p in PROFILES if not args.profiles or p[0] in args.profiles]
    if not selected or (args.profiles and set(args.profiles) - {p[0] for p in PROFILES}):
        raise SystemExit("Unknown or empty profile selection")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"scope": "encoding comparison; physical quality requires owner evaluation",
              "hardware_tested": False, "source_sha256": digest(source),
              "default_changed": False, "status": "in_progress",
              "metric": "first-frame SSIM against qscale 2 Lanczos reference at matching FPS; aspect preserved on 320x240 canvas",
              "display_mode": "native prepared dimensions" if args.native_size else "aspect-fitted 320x240 destination",
              "requested_display_options": {"render_mode": args.render_mode,
                                             "big_endian": args.big_endian,
                                             "tft_spi_hz": args.tft_spi_hz},
              "profiles": [], "chroma_comparisons": []}
    report_path = output / "report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    references = {}
    canvas = ("scale=320:240:flags=lanczos:force_original_aspect_ratio=decrease:"
              "force_divisible_by=2:reset_sar=1,pad=320:240:(ow-iw)/2:(oh-ih)/2:black")
    save()
    for name, qscale, fps, width, height, scaling in selected:
        job = output / name
        result, elapsed = run([cli, "convert", source, job, "--quality", qscale,
                               "--fps", fps, "--width", width, "--height", height,
                               "--scaling", scaling, "--lang", "en"] + tools, output / f"{name}-convert.log")
        artifact = json.loads(result.stdout)
        if fps not in references:
            reference = output / f"reference-{fps}fps.jpg"
            # Match production FPS sampling before comparison. A 10 FPS filter
            # may choose a different source frame than a 20 FPS filter, so using
            # source frame zero for every profile would confound compression
            # quality with motion. Preserve source aspect instead of stretching.
            prefix = "bwdif=mode=send_frame," if artifact["input"]["interlaced"] else ""
            reference_filter = (
                f"{prefix}fps={fps},scale=320:240:flags=lanczos:"
                "force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1:"
                "out_range=full:out_color_matrix=bt601,format=yuvj420p"
            )
            run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", source,
                 "-map", "0:v:0", "-frames:v", "1", "-vf", reference_filter,
                 "-c:v", "mjpeg", "-q:v", "2", "-qmin", "2", "-qmax", "2",
                 "-huffman", "default", "-threads", "1", reference],
                output / f"reference-{fps}fps.log")
            references[fps] = reference
        reference = references[fps]
        video = job / "video.mvb"
        run([cli, "verify", video, "--lang", "en"] + tools, output / f"{name}-verify.log")
        lengths = []
        stream = job / "frames.mjpeg"
        with stream.open("xb") as encoded:
            for data in packets(video):
                lengths.append(len(data))
                encoded.write(data)
        avi = job / "comparison.avi"
        run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "mjpeg",
             "-framerate", fps, "-i", stream, "-c:v", "copy", avi], output / f"{name}-avi.log")
        copied = job / "avi-packets.mjpeg"
        run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", avi,
             "-map", "0:v:0", "-c:v", "copy", "-f", "mjpeg", copied],
            output / f"{name}-avi-packets.log")
        if digest(stream) != digest(copied):
            raise RuntimeError(f"AVI changed JPEG packet bytes for {name}")
        sample = job / "sample.jpg"
        run([cli, "frame", video, "0", sample, "--lang", "en"], output / f"{name}-frame.log")
        ssim, _ = run([args.ffmpeg, "-hide_banner", "-i", sample, "-i", reference,
                       "-filter_complex", f"[0:v]{canvas}[a];[1:v]{canvas}[b];[a][b]ssim",
                       "-frames:v", "1", "-f", "null", "-"], output / f"{name}-ssim.log")
        score = re.search(r"All:([0-9.]+)", ssim.stderr)
        row = {"name": name, "qscale": qscale, "encoded_fps": fps,
               "source_fps": artifact["input"]["original_fps"],
               "target_fps": args.target_fps if args.target_fps is not None else fps,
               "width": artifact["header"]["width"], "height": artifact["header"]["height"],
               "scaling": scaling, "chroma": "4:2:0", "file_bytes": video.stat().st_size,
               "sha256": digest(video), "avi_same_jpegs_bytes": avi.stat().st_size,
               "avi_jpeg_identity_verified": True,
               "container_overhead_bytes": video.stat().st_size - sum(lengths),
               "average_jpeg_bytes": statistics.mean(lengths), "max_jpeg_bytes": max(lengths),
               "conversion_seconds": elapsed, "first_frame_ssim": float(score[1]) if score else None,
               "actual_device_fps": None, "average_decode_us": None,
               "average_draw_us": None, "average_sd_read_us": None,
               "average_frame_fetch_us": None,
               "visible_quality": "not evaluated on physical display",
               "reference_frame": reference.name, "status": "encoding_verified"}
        row["bandwidth_bounds"] = bandwidth_bounds(row["width"], row["height"], fps)
        if args.raw_rgb565:
            row["raw_candidate"] = raw_candidate(args, source, job, artifact, name,
                                                   reference, canvas, output)
        report["profiles"].append(row)
        save()
        if args.port or args.simulator:
            # A legal 2 GiB video needs more than two days at 115200 baud.
            # Budget from the actual byte count rather than a fixed 24-hour cap.
            transfer_timeout = max(600, ((row["file_bytes"] * 10 + 115199) // 115200) * 3 + 600)
            row["transfer_process_timeout_seconds"] = transfer_timeout
            device = [cli, "device", "--lang", "en", "--upload", video]
            if args.port:
                device += ["--port", args.port]
            else:
                card = job / "simulator-card"
                card.mkdir()
                device += ["--simulator", args.simulator.resolve(), "--sd", card]
            if args.render_mode:
                device += ["--render-mode", args.render_mode]
            if args.big_endian:
                device += ["--big-endian"]
            if args.tft_spi_hz:
                device += ["--tft-spi-hz", args.tft_spi_hz]
            if args.test_recovery and name == selected[0][0]:
                if row["file_bytes"] <= 65536:
                    raise RuntimeError("Recovery test needs a prepared video larger than 64 KiB")
                # First cancel during receiving, then open a fresh client and
                # resume the validated device checkpoint without deleting it.
                cancelled, _ = run(device + ["--cancel-after-bytes", "32768"],
                                   output / f"{name}-cancel.log", expected=1, timeout=transfer_timeout)
                cancellation = json.loads(cancelled.stdout)
                if cancellation.get("error", {}).get("code") != "cancelled":
                    raise RuntimeError(f"Cancellation test failed: {cancellation}")
                row["cancellation"] = cancellation
            measured, _ = run(device + ["--benchmark-seconds", args.benchmark_seconds,
                                        "--target-fps", row["target_fps"],
                                        "--display-native" if args.native_size else "--display-full",
                                        "--decoder-mcus", args.decoder_mcus]
                             + (["--row-render"] if args.row_render else []),
                              output / f"{name}-device.log", timeout=transfer_timeout)
            measurement = json.loads(measured.stdout)
            simulated = measurement["hello"]["hardware"]["simulator"]
            if not isinstance(simulated, bool):
                raise RuntimeError("Transport did not identify itself as hardware or simulator")
            if args.port and simulated:
                raise RuntimeError("Physical port reported a simulator; refusing device claim")
            if measurement["upload"]["sha256"] != row["sha256"] or any(
                    s["content_sha256"] != row["sha256"] for s in measurement["status_samples"]):
                raise RuntimeError("Uploaded/played file identity did not match the local container")
            if not measurement["status_samples"] or measurement["benchmark"]["frames"] == 0:
                raise RuntimeError("Playback did not complete any measured frames")
            uploaded = measurement["upload"]
            if uploaded["state"] != "complete" or any(
                    uploaded[field] != row["file_bytes"] for field in
                    ("size", "accepted_offset", "checkpointed_offset", "verified_bytes")):
                raise RuntimeError("Device completion offsets/size were not fully confirmed")
            if simulated:
                final = card / "muse-video" / f"{row['sha256']}.mvb"
                if digest(final) != row["sha256"]:
                    raise RuntimeError("Simulator published-file readback hash mismatch")
                if (final.parent / ".upload.meta").exists():
                    raise RuntimeError("Simulator left an incomplete-transfer journal after completion")
            row["measurement"] = measurement
            row["status"] = "simulator_workflow_verified" if simulated else "device_workflow_reported"
            if not simulated:
                row["actual_device_fps"] = measurement["benchmark"]["actual_fps"]
                row["average_decode_us"] = measurement["benchmark"]["average_decode_us"]
                row["average_draw_us"] = measurement["benchmark"]["average_draw_us"]
                row["average_frame_fetch_us"] = measurement["benchmark"]["average_read_us"]
                pipeline = measurement.get("pipeline_profile", {})
                if pipeline.get("io_available"):
                    row["average_sd_read_us"] = pipeline.get("average_us", {}).get("sd_read")
                row["sd_read_measurement_scope"] = (
                    "isolated stdio read calls" if row["average_sd_read_us"] is not None
                    else "unavailable; frame fetch includes parsing, CRC and JPEG validation"
                )
                report["hardware_tested"] = True
            save()
        print(f"{name}: {row['file_bytes']} bytes, SSIM sample {row['first_frame_ssim']}", flush=True)

    # The current embedded safety profile deliberately accepts only 4:2:0.
    # Compare other encodings on the host without widening that validated contract.
    for chroma in ("yuvj422p", "yuvj444p"):
        avi = output / f"chroma-{chroma}.avi"
        run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", source,
             "-vf", f"fps=20,scale=320:240:flags=area:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1:out_range=full:out_color_matrix=bt601,format={chroma}",
             "-c:v", "mjpeg", "-q:v", "8", "-qmin", "8", "-qmax", "8",
             "-huffman", "default", "-threads", "1", "-an", avi], output / f"{chroma}.log")
        run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror", "-i", avi,
             "-map", "0:v:0", "-f", "null", "-"], output / f"{chroma}-decode.log")
        metadata, _ = run([args.ffprobe, "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=pix_fmt,width,height,nb_frames",
                           "-of", "json", avi], output / f"{chroma}-probe.log")
        decoded = json.loads(metadata.stdout)["streams"][0]
        report["chroma_comparisons"].append({"chroma": chroma, "file_bytes": avi.stat().st_size,
                                              "decoded_stream": decoded,
                                              "embedded_status": "unsupported by MVB v1 profile; host only"})
        save()
    report["status"] = "completed"
    save()
    summary = ["# Encoding and playback candidate comparison", "",
               "Hardware/display quality is unverified unless a physical measurement is recorded. "
               "RGB565 exports are research assets and are not accepted by the production MVB player or USB upload.", "",
               "| Profile | Resolution | Format | qscale | Encoded FPS | Target FPS | Actual device FPS | File bytes | First-frame SSIM |",
               "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in report["profiles"]:
        summary.append(f"| {row['name']} | {row['width']}×{row['height']} | MVB/JPEG 4:2:0 | "
                       f"{row['qscale']} | {row['encoded_fps']} | {row['target_fps']} | {row['actual_device_fps'] if row['actual_device_fps'] is not None else 'unmeasured'} | "
                       f"{row['file_bytes']} | {row['first_frame_ssim']} |")
        raw = row.get("raw_candidate")
        if raw and raw["status"] == "host_export_verified":
            summary.append(f"| {row['name']} source raw | {raw['width']}×{raw['height']} | RGB565 BE | — | "
                           f"{raw['encoded_fps']} | — | unmeasured | {raw['file_bytes']} | {raw['first_frame_ssim']} |")
    (output / "SUMMARY.md").write_text("\n".join(summary) + "\n")
    print(report_path)


if __name__ == "__main__":
    main()
