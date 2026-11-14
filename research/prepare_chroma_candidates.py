#!/usr/bin/env python3
"""Host-only bounded JPEG encoding research. Never opens serial or mounts a card."""
import argparse
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(65536), b""):
            h.update(data)
    return h.hexdigest()


def command(args, output, name, *, stdout_file=None, required=True):
    started = time.monotonic()
    with (output / (name + ".log")).open("wb") as log:
        if stdout_file:
            with stdout_file.open("xb") as sink:
                result = subprocess.run(args, stdout=sink, stderr=log, timeout=300)
            text = None
        else:
            result = subprocess.run(args, stdout=subprocess.PIPE, stderr=log, timeout=300)
            text = result.stdout.decode("utf-8", errors="replace")
            log.write(result.stdout)
    record = {"argv": args, "exit_code": result.returncode,
              "elapsed_seconds": time.monotonic() - started}
    (output / (name + ".command.json")).write_text(json.dumps(record, indent=2) + "\n")
    if required and result.returncode:
        raise RuntimeError(f"{name} failed; inspect its retained log")
    return text, record


def jpeg_info(data):
    if data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        raise ValueError("Incomplete JPEG")
    offset, tables, info = 2, {}, {}
    while offset < len(data):
        if data[offset] != 255:
            raise ValueError("Invalid marker")
        while data[offset] == 255:
            offset += 1
        marker = data[offset]
        offset += 1
        length = int.from_bytes(data[offset:offset + 2], "big")
        payload = data[offset + 2:offset + length]
        if marker == 0xC0:
            info = {"sof": "0xc0", "precision": payload[0],
                    "height": int.from_bytes(payload[1:3], "big"),
                    "width": int.from_bytes(payload[3:5], "big"),
                    "sampling": [hex(payload[7 + i * 3]) for i in range(payload[5])]}
        if marker == 0xDB:
            pos = 0
            while pos < len(payload):
                spec = payload[pos]
                pos += 1
                if spec >> 4:
                    raise ValueError("Research table must be baseline 8-bit")
                tables[spec & 15] = list(payload[pos:pos + 64])
                pos += 64
        if marker == 0xDA:
            break
        offset += length
    return info, tables


def raw_frame_hash(path, output, name):
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "mjpeg",
            "-i", str(path), "-map", "0:v:0", "-an", "-sn", "-dn",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    started, h, count = time.monotonic(), hashlib.sha256(), 0
    with (output / (name + ".log")).open("wb") as log:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=log)
        for data in iter(lambda: process.stdout.read(65536), b""):
            h.update(data)
            count += len(data)
        code = process.wait(timeout=60)
    record = {"argv": args, "exit_code": code, "elapsed_seconds": time.monotonic() - started,
              "decoded_rgb_bytes": count, "sha256": h.hexdigest()}
    (output / (name + ".command.json")).write_text(json.dumps(record, indent=2) + "\n")
    if code or count != 320 * 240 * 3 * 360:
        raise RuntimeError("Full bounded decoded stream verification failed")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source, output = args.source.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    original_hash = digest(source)
    cli = str(root / "target/debug/muse-video-cli")
    packer = str(root / "target/debug/examples/pack_jpeg_research")
    base_filter = ("fps=30/1,scale=320:240:flags=bicubic:force_original_aspect_ratio=decrease:"
                   "force_divisible_by=2:reset_sar=1:out_range=full:out_color_matrix=bt601")
    profiles = []
    for sampling, huffman in [("420", "default"), ("420", "optimal"),
                              ("422", "default"), ("444", "default")]:
        name = f"ffmpeg-{sampling}-q8-{huffman}"
        stream = output / (name + ".mjpeg")
        encoder = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-protocol_whitelist", "file,pipe", "-i", str(source),
                   "-map", "0:v:0", "-an", "-sn", "-dn", "-vf",
                   base_filter + f",format=yuvj{sampling}p", "-c:v", "mjpeg",
                   "-q:v", "8", "-qmin", "8", "-qmax", "8", "-huffman", huffman,
                   "-threads", "1", "-f", "image2pipe", "pipe:1"]
        _, encoded = command(encoder, output, name + "-encode", stdout_file=stream)
        profiles.append({"name": name, "encoder": "ffmpeg", "qscale": 8,
                         "chroma": sampling, "huffman": huffman,
                         "stream": str(stream), "encode_seconds": encoded["elapsed_seconds"]})
    # Pack first, then use the existing parser to extract its complete first JPEG.
    for profile in profiles:
        folder = output / profile["name"]
        packed, record = command([packer, profile["stream"], str(folder), "30"], output,
                                 profile["name"] + "-pack", required=False)
        profile["production_parser_accepted"] = record["exit_code"] == 0
        if profile["production_parser_accepted"]:
            packed = json.loads(packed)
            if packed.get("research_jpeg_chroma"):
                raise RuntimeError("This batch records default parser acceptance; build the packer without research-jpeg-chroma")
            profile.update(packed)
            command([cli, "frame", str(folder / "video.mvb"), "0", str(folder / "sample.jpg"),
                     "--lang", "en"], output, profile["name"] + "-frame")
        else:
            command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                     "-f", "mjpeg", "-i", profile["stream"], "-frames:v", "1", "-c:v", "copy",
                     str(folder / "sample.jpg")], output, profile["name"] + "-frame")
    _, tables = jpeg_info((output / profiles[0]["name"] / "sample.jpg").read_bytes())
    positions = [(row, total - row) for total in range(15)
                 for row in (range(7, -1, -1) if total % 2 == 0 else range(8))
                 if 0 <= total - row < 8]
    table_file = output / "ffmpeg-q8-quantization.txt"
    with table_file.open("x") as file:
        for key in sorted(tables):
            natural = [0] * 64
            for value, (row, col) in zip(tables[key], positions):
                natural[row * 8 + col] = value
            for row in range(8):
                file.write(" ".join(map(str, natural[row * 8:(row + 1) * 8])) + "\n")
            file.write("\n")
    cjpeg_profiles = []
    for chroma, sampling in [("420", "2x2,1x1,1x1"), ("422", "2x1,1x1,1x1"),
                             ("444", "1x1,1x1,1x1")]:
        name = f"libjpeg-{chroma}-ffmpeg-q8-tables"
        stream = output / (name + ".mjpeg")
        encoder = ["cjpeg", "-strict", "-baseline", "-dct", "int", "-qtables", str(table_file),
                   "-qslots", "0,0,0", "-sample", sampling]
        cjpeg_profiles.append({"name": name, "encoder": "libjpeg", "qscale": None,
                               "quantization_reference": "exact_ffmpeg_q8_tables",
                               "chroma": chroma, "huffman": "standard_default",
                               "encoder_argv_per_frame": encoder, "stream": str(stream),
                               "encode_seconds": 0.0})
    rgb_encoder = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source),
                   "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", base_filter,
                   "-pix_fmt", "rgb24", "-threads", "1", "-f", "rawvideo", "pipe:1"]
    with (output / "rgb-source.log").open("wb") as log:
        process = subprocess.Popen(rgb_encoder, stdout=subprocess.PIPE, stderr=log)
        files = [Path(profile["stream"]).open("xb") for profile in cjpeg_profiles]
        frame_bytes, frames, reference = 320 * 240 * 3, 0, None
        try:
            while True:
                data = bytearray()
                while len(data) < frame_bytes:
                    block = process.stdout.read(frame_bytes - len(data))
                    if not block:
                        break
                    data.extend(block)
                if not data:
                    break
                if len(data) != frame_bytes:
                    raise RuntimeError("Short raw source frame")
                if reference is None:
                    reference = bytes(data)
                ppm = b"P6\n320 240\n255\n" + data
                for profile, file in zip(cjpeg_profiles, files):
                    started = time.monotonic()
                    result = subprocess.run(profile["encoder_argv_per_frame"], input=ppm,
                                            stdout=subprocess.PIPE, stderr=log, timeout=15)
                    profile["encode_seconds"] += time.monotonic() - started
                    if result.returncode:
                        raise RuntimeError("cjpeg frame encode failed")
                    _, actual_tables = jpeg_info(result.stdout)
                    if actual_tables != tables:
                        raise RuntimeError("cjpeg did not preserve the exact reference quantization tables")
                    file.write(result.stdout)
                frames += 1
            code = process.wait(timeout=30)
            if code or frames != 360:
                raise RuntimeError(f"RGB source frames {frames}, exit {code}")
        finally:
            for file in files:
                file.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    (output / "rgb-source.command.json").write_text(json.dumps({"argv": rgb_encoder,
        "frames": frames, "exit_code": code, "bounded_frame_bytes": frame_bytes}, indent=2) + "\n")
    (output / "reference.rgb24").write_bytes(reference)
    for profile in cjpeg_profiles:
        folder = output / profile["name"]
        packed, record = command([packer, profile["stream"], str(folder), "30"], output,
                                 profile["name"] + "-pack", required=False)
        profile["production_parser_accepted"] = record["exit_code"] == 0
        if profile["production_parser_accepted"]:
            packed = json.loads(packed)
            if packed.get("research_jpeg_chroma"):
                raise RuntimeError("This batch records default parser acceptance; build the packer without research-jpeg-chroma")
            profile.update(packed)
            command([cli, "frame", str(folder / "video.mvb"), "0", str(folder / "sample.jpg"),
                     "--lang", "en"], output, profile["name"] + "-frame")
        else:
            command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                     "-f", "mjpeg", "-i", profile["stream"], "-frames:v", "1", "-c:v", "copy",
                     str(folder / "sample.jpg")], output, profile["name"] + "-frame")
    profiles += cjpeg_profiles
    template = json.loads((root / ".verification/session-2026-11-13/physical-desktop/cache/"
                          "dev.muse.videobridge/job-1794581868091/manifest.json").read_text())
    for profile in profiles:
        folder = output / profile["name"]
        info, _ = jpeg_info((folder / "sample.jpg").read_bytes())
        profile["jpeg_header"] = info
        if profile["production_parser_accepted"]:
            _, verified = command([cli, "verify", str(folder / "video.mvb"), "--lang", "en"], output,
                                   profile["name"] + "-verify")
            profile["full_ffmpeg_decode_exit"] = verified["exit_code"]
        profile["decoded_rgb_stream"] = raw_frame_hash(Path(profile["stream"]), output,
                                                       profile["name"] + "-rgb-stream")
        raw = output / (profile["name"] + ".rgb24")
        command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i",
                 str(folder / "sample.jpg"), "-frames:v", "1", "-pix_fmt", "rgb24",
                 "-f", "rawvideo", "pipe:1"], output, profile["name"] + "-first-rgb", stdout_file=raw)
        decoded = raw.read_bytes()
        if len(decoded) != frame_bytes:
            raise RuntimeError("Short first-frame RGB comparison")
        mse = sum((a - b) ** 2 for a, b in zip(reference, decoded)) / len(reference)
        profile["first_frame_rgb_psnr_db"] = math.inf if mse == 0 else 10 * math.log10(255 ** 2 / mse)
        if profile["production_parser_accepted"]:
            manifest = json.loads(json.dumps(template))
            manifest["sha256"], manifest["header"] = profile["sha256"], profile["header"]
            manifest["research_encoder"] = {key: profile.get(key) for key in
                                            ["encoder", "qscale", "quantization_reference", "chroma", "huffman"]}
            (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    profiles[1]["decoded_rgb_identical_to_default_huffman"] = (
        profiles[1]["decoded_rgb_stream"]["sha256"] == profiles[0]["decoded_rgb_stream"]["sha256"])
    if digest(source) != original_hash:
        raise RuntimeError("Source hash changed")
    report = {"scope": "host_only_actual_video_encodings_no_hardware_FPS_or_display_quality_claim",
              "source": str(source), "source_sha256_before": original_hash,
              "source_sha256_after": digest(source), "source_fps": 30, "encoded_fps": 30,
              "resolution": [320, 240], "frames": 360, "audio_in_output": False,
              "quality_metric_scope": "first_RGB_frame_PSNR_against_bicubic_source_no_perceived_quality_claim",
              "profiles": profiles}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(output / "report.json")


if __name__ == "__main__":
    main()
