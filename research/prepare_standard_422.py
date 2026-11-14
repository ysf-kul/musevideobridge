#!/usr/bin/env python3
"""Bounded standard 4:2:2 research encoding; no UART, source edits or card access."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from prepare_chroma_candidates import command, digest, jpeg_info, raw_frame_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qscale", type=int, required=True)
    parser.add_argument("--decoder-verifier", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.qscale <= 31:
        parser.error("qscale must be 2..31")
    root = Path(__file__).resolve().parents[1]
    source, output = args.source.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = digest(source)
    frame_size = 320 * 240 * 3
    base = ("fps=30/1,scale=320:240:flags=bicubic:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2:reset_sar=1:out_range=full:out_color_matrix=bt601")
    reference = output / "ffmpeg-420-quantization-reference.jpg"
    reference_args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                      "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
                      "-vf", base + ",format=yuvj420p", "-c:v", "mjpeg",
                      "-q:v", str(args.qscale), "-qmin", str(args.qscale), "-qmax",
                      str(args.qscale), "-huffman", "default", "-threads", "1",
                      "-frames:v", "1", "-f", "image2pipe", "pipe:1"]
    command(reference_args, output, "quantization-reference", stdout_file=reference)
    _, tables = jpeg_info(reference.read_bytes())
    positions = [(row, total - row) for total in range(15)
                 for row in (range(7, -1, -1) if total % 2 == 0 else range(8))
                 if 0 <= total - row < 8]
    table_path = output / "quantization.txt"
    with table_path.open("x") as table_file:
        for key in sorted(tables):
            natural = [0] * 64
            for value, (row, column) in zip(tables[key], positions):
                natural[row * 8 + column] = value
            for row in range(8):
                table_file.write(" ".join(map(str, natural[row * 8:(row + 1) * 8])) + "\n")
            table_file.write("\n")
    cjpeg = ["cjpeg", "-strict", "-baseline", "-dct", "int", "-qtables", str(table_path),
             "-qslots", "0,0,0", "-sample", "2x1,1x1,1x1"]
    source_args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i",
                   str(source), "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", base,
                   "-pix_fmt", "rgb24", "-threads", "1", "-f", "rawvideo", "pipe:1"]
    stream_path = output / "standard-422.mjpeg"
    frames, maximum, encode_seconds = 0, 0, 0.0
    conversion_started = time.monotonic()
    with (output / "encode.log").open("wb") as log, stream_path.open("xb") as stream:
        source_process = subprocess.Popen(source_args, stdout=subprocess.PIPE, stderr=log)
        try:
            while True:
                data = bytearray()
                while len(data) < frame_size:
                    block = source_process.stdout.read(frame_size - len(data))
                    if not block:
                        break
                    data.extend(block)
                if not data:
                    break
                if len(data) != frame_size:
                    raise RuntimeError("Short RGB source frame")
                started = time.monotonic()
                encoded = subprocess.run(cjpeg, input=b"P6\n320 240\n255\n" + data,
                                         stdout=subprocess.PIPE, stderr=log, timeout=15)
                encode_seconds += time.monotonic() - started
                if encoded.returncode:
                    raise RuntimeError("cjpeg failed")
                info, actual_tables = jpeg_info(encoded.stdout)
                if actual_tables != tables or info["sampling"] != ["0x21", "0x11", "0x11"]:
                    raise RuntimeError("Quantization/sampling differs from requested profile")
                if len(encoded.stdout) > 49152:
                    raise RuntimeError("JPEG exceeds existing frame bound")
                maximum = max(maximum, len(encoded.stdout))
                stream.write(encoded.stdout)
                frames += 1
            code = source_process.wait(timeout=30)
            if code or frames != 360:
                raise RuntimeError(f"Source exit {code}, frames {frames}; expected 360")
        finally:
            if source_process.poll() is None:
                source_process.kill()
                source_process.wait()
    conversion_seconds = time.monotonic() - conversion_started
    packed, _ = command([str(root / "target/debug/examples/pack_jpeg_research"),
                         str(stream_path), str(output / "container"), "30"], output, "pack")
    packed = json.loads(packed)
    if not packed.get("research_jpeg_chroma") or packed["header"]["max_jpeg_bytes"] != maximum:
        raise RuntimeError("Packer must use research-jpeg-chroma; maximum must match")
    decoded_hash = raw_frame_hash(stream_path, output, "decoded-rgb")
    native_text, native_command = command([str(args.decoder_verifier.resolve()), str(stream_path)],
                                          output, "native-decoder")
    native = json.loads(native_text)
    stats = output / "full-rgb-psnr.txt"
    graph = (f"[0:v:0]{base},format=rgb24,format=gbrp[reference];"
             "[1:v:0]format=rgb24,format=gbrp[candidate];"
             f"[reference][candidate]psnr=stats_file={stats}")
    quality_args = ["ffmpeg", "-hide_banner", "-nostdin", "-threads", "1", "-i", str(source),
                    "-threads", "1", "-f", "mjpeg", "-framerate", "30", "-i", str(stream_path),
                    "-filter_complex_threads", "1", "-filter_complex", graph, "-an", "-sn", "-dn",
                    "-f", "null", "-"]
    _, quality_command = command(quality_args, output, "full-rgb-quality")
    quality_log = (output / "full-rgb-quality.log").read_text()
    match = re.findall(r"PSNR r:([^ ]+) g:([^ ]+) b:([^ ]+) average:([^ ]+) min:([^ ]+) max:([^\s]+)", quality_log)
    if not match or len(stats.read_text().splitlines()) != frames:
        raise RuntimeError("Incomplete full-video RGB comparison")
    quality = dict(zip(("r_db", "g_db", "b_db", "average_db", "minimum_frame_db", "maximum_frame_db"),
                       map(float, match[-1])))
    if digest(source) != before:
        raise RuntimeError("Source changed")
    report = {"scope": "host_only_encoding_and_real_native_codec_not_device_FPS_or_screen_quality",
              "source": str(source), "source_sha256_before": before, "source_sha256_after": digest(source),
              "source_fps": 30, "encoded_fps": 30, "resolution": [320, 240], "frames": frames,
              "format": "standard_YCbCr_422_baseline_JPEG", "quantization_reference_ffmpeg_qscale": args.qscale,
              "sampling": ["0x21", "0x11", "0x11"], "cjpeg_argv_per_frame": cjpeg,
              "source_rgb_argv": source_args, "bounded_rgb_source_bytes": frame_size,
              "cjpeg_total_call_seconds": encode_seconds, "conversion_seconds": conversion_seconds,
              "jpeg_stream_bytes": stream_path.stat().st_size,
              "maximum_jpeg_bytes": maximum, "container": packed,
              "container_path": str(output / "container/video.mvb"), "audio_in_output": False,
              "decoded_rgb_stream": decoded_hash, "native_decoder": native,
              "native_decoder_command": native_command, "whole_video_rgb_psnr": quality,
              "quality_command": quality_command, "hardware_tested": False,
              "required_host_feature": "research-jpeg-chroma",
              "required_firmware_flag": "MVB_RESEARCH_JPEG_CHROMA"}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
