#!/usr/bin/env python3
"""Prepare two native video-only sizes; no UART, flash, card or source edits."""
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

from prepare_chroma_candidates import command, digest


def decoded_rgb(stream, directory, width, height):
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "mjpeg",
            "-i", str(stream), "-map", "0:v:0", "-an", "-sn", "-dn", "-pix_fmt", "rgb24",
            "-f", "rawvideo", "pipe:1"]
    hashed, count, started = hashlib.sha256(), 0, time.monotonic()
    with (directory / "decoded-rgb.log").open("wb") as log:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=log)
        for block in iter(lambda: process.stdout.read(65536), b""):
            hashed.update(block)
            count += len(block)
        code = process.wait(timeout=60)
    if code or count != width * height * 3 * 360:
        raise RuntimeError("Incomplete full RGB stream decode")
    record = {"argv": argv, "exit_code": code, "decoded_rgb_bytes": count,
              "sha256": hashed.hexdigest(), "host_seconds": time.monotonic() - started}
    (directory / "decoded-rgb.command.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jpegdec-verifier", type=Path, required=True)
    parser.add_argument("--tjpgd-verifier", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    root = Path(__file__).resolve().parents[1]
    output.mkdir(parents=True, exist_ok=False)
    before = digest(source)
    profiles = []
    for width, height, fit in ((304, 224, "crop"), (288, 216, "contain")):
        directory = output / f"{width}x{height}"
        directory.mkdir()
        aspect = "increase" if fit == "crop" else "decrease"
        crop = f",crop={width}:{height}" if fit == "crop" else ""
        filtering = (f"fps=30/1,scale={width}:{height}:flags=bicubic:"
                     f"force_original_aspect_ratio={aspect}:force_divisible_by=2:"
                     f"reset_sar=1:out_range=full:out_color_matrix=bt601{crop},format=yuvj420p")
        stream = directory / "video.mjpeg"
        encoder = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:v:0",
                   "-an", "-sn", "-dn", "-vf", filtering, "-c:v", "mjpeg", "-q:v", "8",
                   "-qmin", "8", "-qmax", "8", "-huffman", "optimal", "-threads", "1",
                   "-f", "image2pipe", "pipe:1"]
        _, encoded = command(encoder, directory, "encode", stdout_file=stream)
        packed_text, packed_command = command([
            str(root / "target/debug/examples/pack_jpeg_research"), str(stream),
            str(directory / "container"), "30"], directory, "pack")
        packed = json.loads(packed_text)
        if packed.get("research_jpeg_chroma"):
            raise RuntimeError("Native 420 candidates must be checked with the default-mode packer")
        header = packed["header"]
        if [header["width"], header["height"], header["frames"]] != [width, height, 360]:
            raise RuntimeError("Unexpected encoded size/frame count")
        video = directory / "container/video.mvb"
        _, verified = command([str(root / "target/debug/muse-video-cli"), "verify", str(video),
                               "--lang", "en"], directory, "default-core-full-verify")
        native_results = {}
        for name, verifier in (("JPEGDEC", args.jpegdec_verifier),
                               ("software_TJpgDec", args.tjpgd_verifier)):
            result, invocation = command([str(verifier.resolve()), str(stream)], directory,
                                         f"native-{name}")
            result = json.loads(result)
            if result["frames"] != 360 or result["pixels_per_frame"] != width * height:
                raise RuntimeError("Unexpected native decoded pixel coverage")
            native_results[name] = {"result": result, "invocation": invocation}
        rgb = decoded_rgb(stream, directory, width, height)
        profile = {"width": width, "height": height, "fit": fit, "scaling": "bicubic",
                   "qscale": 8, "sampling": "420", "huffman": "optimal", "encoded_fps": 30,
                   "audio_in_output": False, "container": packed, "container_path": str(video),
                   "encode_command": encoded, "pack_command": packed_command,
                   "default_core_full_verify": verified, "native_results": native_results,
                   "decoded_rgb": rgb, "hardware_tested": False}
        manifest = {"schema_version": 1, "sha256": packed["sha256"], "header": header,
                    "research_only": True, "options": {"fps": {"numerator": 30, "denominator": 1},
                    "qscale": 8, "fit": "Crop" if fit == "crop" else "Contain", "width": width,
                    "height": height, "scaling": "bicubic"},
                    "research_encoder": {"encoder": "ffmpeg", "chroma": "420", "huffman": "optimal"}}
        (directory / "container/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (directory / "report.json").write_text(json.dumps(profile, indent=2) + "\n")
        profiles.append(profile)
        print(f"{width}x{height}: {header['file_bytes']} bytes, max JPEG {header['max_jpeg_bytes']}", flush=True)
    if digest(source) != before:
        raise RuntimeError("Source changed")
    report = {"scope": "host_native_size_encoding_and_decoder_checks_not_actual_device_FPS",
              "source": str(source), "source_fps": 30, "source_sha256_before": before,
              "source_sha256_after": digest(source), "profiles": profiles, "hardware_tested": False,
              "304x224_aspect_handling": "304x228 bicubic scale then central four-row crop; no stretching"}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
