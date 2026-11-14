#!/usr/bin/env python3
"""Host-only Phase 1 encoding research; never opens serial ports or SD devices."""

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def run(arguments, commands):
    started = time.monotonic()
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=120)
    commands.append({"argv": arguments, "exit_code": result.returncode})
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return result.stdout, time.monotonic() - started


def jpeg_header(data):
    """Read JPEG segment headers, without mistaking entropy bytes for markers."""
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("Packet is not a complete JPEG")
    offset = 2
    while offset < len(data):
        if data[offset] != 255:
            raise ValueError("Invalid marker boundary")
        while data[offset] == 255:
            offset += 1
        marker = data[offset]
        offset += 1
        if marker == 0xDA:
            break
        length = int.from_bytes(data[offset:offset + 2], "big")
        if length < 2 or offset + length > len(data):
            raise ValueError("Invalid JPEG segment length")
        if marker in (0xC0, 0xC2):
            segment = data[offset + 2:offset + length]
            return {
                "sof": hex(marker),
                "precision": segment[0],
                "height": int.from_bytes(segment[1:3], "big"),
                "width": int.from_bytes(segment[3:5], "big"),
                "components": segment[5],
                "sampling": [hex(segment[7 + 3 * i]) for i in range(segment[5])],
            }
        offset += length
    raise ValueError("No supported SOF header")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = arguments.output or Path(".verification") / ("encoding-" + stamp)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    commands = []
    ffmpeg_version, _ = run(["ffmpeg", "-version"], commands)
    ffprobe_version, _ = run(["ffprobe", "-version"], commands)
    report = {
        "scope": "host-only synthetic MP4 and MJPEG AVI encoding research",
        "hardware_tested": False,
        "generated_at_utc": stamp,
        "host": platform.platform(),
        "ffmpeg": ffmpeg_version.splitlines()[0],
        "ffprobe": ffprobe_version.splitlines()[0],
        "container_model": "draft: 96-byte header, 16-byte frame records padded to 4, 24-byte index entries",
        "cases": [],
    }
    corpora = {
        "pattern": "testsrc2=size=640x360:rate=30:duration=4",
        "noise": "testsrc2=size=320x240:rate=30:duration=4,noise=alls=90:allf=t:all_seed=123",
    }
    try:
        for corpus, generator in corpora.items():
            source = output / (corpus + ".mp4")
            run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-f", "lavfi", "-i", generator, "-an", "-c:v", "libx264",
                 "-threads", "1", "-preset", "fast", "-crf", "18",
                 "-pix_fmt", "yuv420p", str(source)], commands)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            for fps in (10, 15):
                for quality in (4, 8, 12):
                    avi = output / f"{corpus}-{fps}fps-q{quality}.avi"
                    filters = (
                        f"fps={fps},scale=320:240:force_original_aspect_ratio=decrease:"
                        "force_divisible_by=2:reset_sar=1:out_range=full:"
                        "out_color_matrix=bt601,format=yuvj420p"
                    )
                    _, elapsed = run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
                        "-vf", filters, "-c:v", "mjpeg", "-q:v", str(quality),
                        "-huffman", "default", "-threads", "1", "-f", "avi", str(avi),
                    ], commands)
                    probe, _ = run([
                        "ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_packets", "-show_entries", "packet=pos,size,pts_time",
                        "-of", "json", str(avi),
                    ], commands)
                    packets = json.loads(probe)["packets"]
                    sizes = []
                    header = None
                    payload_hash = hashlib.sha256()
                    with avi.open("rb") as file:
                        for packet in packets:
                            size = int(packet["size"])
                            file.seek(int(packet["pos"]))
                            data = file.read(size)
                            current = jpeg_header(data)
                            if current["sof"] != "0xc0" or current["precision"] != 8:
                                raise ValueError("Encoder did not produce 8-bit baseline JPEG")
                            if current["sampling"] != ["0x22", "0x11", "0x11"]:
                                raise ValueError("Encoder did not produce YCbCr 4:2:0")
                            if header is not None and header != current:
                                raise ValueError("Frame dimensions/encoding changed")
                            header = current
                            sizes.append(size)
                            payload_hash.update(data)
                    if len(sizes) != 4 * fps:
                        raise ValueError("Unexpected frame count")
                    _, decode_seconds = run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
                        "-i", str(avi), "-f", "null", "-",
                    ], commands)
                    modeled_bytes = 96 + sum((16 + n + 3) // 4 * 4 for n in sizes) + 24 * len(sizes)
                    case = {
                        "corpus": corpus, "source_sha256": source_hash,
                        "fps": fps, "qscale": quality, "frames": len(sizes),
                        "jpeg_header": header, "jpeg_payload_sha256": payload_hash.hexdigest(),
                        "mean_frame_bytes": round(statistics.mean(sizes), 2),
                        "max_frame_bytes": max(sizes), "payload_bytes": sum(sizes),
                        "avi_bytes_measured": avi.stat().st_size,
                        "custom_bytes_modeled": modeled_bytes,
                        "conversion_seconds_host": round(elapsed, 4),
                        "decode_seconds_host": round(decode_seconds, 4),
                        "frame_bytes": sizes,
                        "pts_seconds": [float(p["pts_time"]) for p in packets],
                    }
                    report["cases"].append(case)
                    print(f"{corpus} {fps} FPS q={quality}: mean={case['mean_frame_bytes']} "
                          f"max={max(sizes)} AVI={avi.stat().st_size} modeled={modeled_bytes}", flush=True)
        report["result"] = "passed: all 12 AVI cases parsed and decoded on host"
    finally:
        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
    print("Evidence:", output)


if __name__ == "__main__":
    main()
