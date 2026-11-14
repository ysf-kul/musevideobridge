"""Actual-codec readback comparison purity/accounting; no physical TFT claims."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")

SOURCE = r'''
#define main existing_profile_main
#include "profile-adapters.hpp"
#undef main

class IntegrityPainter : public Painter {
  Clock &clock_;
  bool endian_;
  mvb::IntegrityProfile integrity_;
public:
  std::vector<uint16_t> memory = std::vector<uint16_t>(320 * 240, 0xabc0);
  bool armed = false, supported = true, fail_read = false, fail_end_frame = false;
  bool unavailable_result = false, omit_one = false;
  uint64_t write_calls = 0;
  unsigned arms = 0, disarms = 0;
  explicit IntegrityPainter(Clock &clock, bool endian) : Painter(clock), clock_(clock), endian_(endian) {}
  bool big_endian() const override { return endian_; }
  static uint16_t swapped(uint16_t value) { return uint16_t((value >> 8) | (value << 8)); }
  void begin_frame() override { if (!armed) Painter::begin_frame(); }
  bool end_frame() override { return armed ? !fail_end_frame : Painter::end_frame(); }
  bool row(int x, int y, const uint16_t *expected, int count) override {
    assert(x >= 0 && y >= 0 && y < 240 && count > 0 && x + count <= 320);
    if (!armed) {
      for (int pixel = 0; pixel < count; pixel++)
        memory[y * 320 + x + pixel] = endian_ ? swapped(expected[pixel]) : expected[pixel];
      write_calls++; return Painter::row(x, y, expected, count);
    }
    if (fail_read) return false;
    for (int pixel = 0; pixel < count; pixel++) {
      uint16_t colour = endian_ ? swapped(expected[pixel]) : expected[pixel];
      integrity_.pixels_checked++;
      if (colour != memory[y * 320 + x + pixel]) integrity_.pixels_mismatched++;
    }
    integrity_.read_us += uint64_t(count) + 7; clock_.advance(uint64_t(count) + 7);
    return true; // Mismatch is evidence, never an instruction to repaint.
  }
  bool begin_integrity_check() override {
    assert(!armed);
    if (!supported) return false;
    armed = true; arms++; integrity_ = {}; integrity_.available = true; return true;
  }
  mvb::IntegrityProfile end_integrity_check() override {
    assert(armed); armed = false; disarms++;
    if (unavailable_result) integrity_.available = false;
    if (omit_one && integrity_.pixels_checked) integrity_.pixels_checked--;
    return integrity_;
  }
};

void ready(mvb::Engine &engine, Requests &requests) {
  for (int i = 0; i < 1000; i++) {
    engine.tick(); auto status = requests.command("{\"method\":\"get_status\"}");
    assert(status["result"]["state"] != "error");
    if (!status["result"]["frame_index"].isNull()) return;
  }
  assert(false);
}

void verify(const std::vector<uint8_t> &bytes, bool endian) {
  Clock clock; Storage storage(clock, bytes); Runtime runtime(clock);
  IntegrityPainter painter(clock, endian); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin();
  Requests requests(engine, output); ready(engine, requests);
  const auto original = painter.memory; const auto writes = painter.write_calls;
  const unsigned frames = requests.command("{\"method\":\"get_benchmark\"}")["result"]["frames"];
  const auto pipeline = requests.command("{\"method\":\"get_pipeline_profile\"}");
  auto checked = requests.command("{\"method\":\"probe_pipeline\",\"stage\":\"frame_integrity\"}");
  assert(checked["ok"] == true && checked["result"]["passed"] == true);
  assert(checked["result"]["available"] == true && checked["result"]["complete"] == true);
  assert(checked["result"]["pixels_checked"] == 317 * 239);
  assert(checked["result"]["expected_pixels"] == 317 * 239);
  assert(checked["result"]["pixels_mismatched"] == 0 && checked["result"]["read_us"].as<uint64_t>() > 0);
  assert(checked["result"]["scope"] == "uncounted_actual_TFT_readback");
  assert(checked["result"]["region"]["x"] == 1 && checked["result"]["region"]["y"] == 0);
  assert(checked["result"]["region"]["width"] == 317 && checked["result"]["region"]["height"] == 239);
  assert(painter.memory == original && painter.write_calls == writes);
  assert(!painter.armed && painter.arms == 1 && painter.disarms == 1);
  assert(requests.command("{\"method\":\"get_status\"}")["result"]["state"] == "paused");
  assert(requests.command("{\"method\":\"get_benchmark\"}")["result"]["frames"] == frames);
  auto after = requests.command("{\"method\":\"get_pipeline_profile\"}");
  assert(after["result"]["frames"] == pipeline["result"]["frames"]);
  assert(after["result"]["average_us"]["frame"] == pipeline["result"]["average_us"]["frame"]);
  // Three corrupt pixels within the actual video rectangle, plus one outside.
  painter.memory[1] ^= 0x1; painter.memory[120 * 320 + 155] ^= 0x20;
  painter.memory[238 * 320 + 317] ^= 0x800; painter.memory[0] ^= 0x1;
  auto corrupted = painter.memory;
  checked = requests.command("{\"method\":\"probe_pipeline\",\"stage\":\"frame_integrity\",\"repetitions\":3}");
  assert(checked["ok"] == true && checked["result"]["complete"] == true && checked["result"]["passed"] == false);
  assert(checked["result"]["frames_checked"] == 3 && checked["result"]["pixels_checked"] == 3 * 317 * 239);
  assert(checked["result"]["pixels_mismatched"] == 9);
  assert(painter.memory == corrupted && painter.write_calls == writes && !painter.armed);
  assert(requests.command("{\"method\":\"get_benchmark\"}")["result"]["frames"] == frames);
  const auto arms = painter.arms;
  for (const char *command : {
      "{\"method\":\"probe_pipeline\",\"stage\":\"frame_integrity\",\"repetitions\":0}",
      "{\"method\":\"probe_pipeline\",\"stage\":\"frame_integrity\",\"repetitions\":5}",
      "{\"method\":\"probe_pipeline\",\"stage\":\"frame_integrity\",\"repetitions\":\"1\"}"})
    assert(requests.command(command)["error"]["code"] == "invalid_parameter");
  assert(painter.arms == arms && painter.memory == corrupted && painter.write_calls == writes);
  painter.supported = false;
  assert(requests.command("{\"method\":\"probe_pipeline\",\"stage\":\"frame_integrity\"}")["error"]["code"] == "unsupported_configuration");
  assert(painter.arms == arms && painter.disarms == arms && painter.memory == corrupted);
}

void failures(const std::vector<uint8_t> &bytes, unsigned failure) {
  Clock clock; Storage storage(clock, bytes); Runtime runtime(clock);
  IntegrityPainter painter(clock, false); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin();
  Requests requests(engine, output); ready(engine, requests);
  auto original = painter.memory; auto writes = painter.write_calls;
  auto frames = requests.command("{\"method\":\"get_benchmark\"}")["result"]["frames"].as<unsigned>();
  painter.fail_read = failure == 0; painter.fail_end_frame = failure == 1;
  painter.unavailable_result = failure == 2; painter.omit_one = failure == 3;
  auto checked = requests.command("{\"method\":\"probe_pipeline\",\"stage\":\"frame_integrity\"}");
  assert(checked["error"]["code"] == "integrity_check_failed");
  assert(!painter.armed && painter.arms == 1 && painter.disarms == 1);
  assert(painter.memory == original && painter.write_calls == writes);
  assert(requests.command("{\"method\":\"get_benchmark\"}")["result"]["frames"] == frames);
}

void unsupported_geometry(const std::vector<uint8_t> &bytes, unsigned configuration) {
  Clock clock; Storage storage(clock, bytes); Runtime runtime(clock);
  IntegrityPainter painter(clock, false); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin();
  Requests requests(engine, output); ready(engine, requests);
  const char *configuration_command = nullptr;
  switch (configuration) {
  case 0: configuration_command = "{\"method\":\"set_rotation\",\"rotation_deg\":180}"; break;
  case 1: configuration_command = "{\"method\":\"set_scale\",\"scale\":0.5}"; break;
  case 2: configuration_command = "{\"method\":\"set_position\",\"x\":-1,\"y\":0}"; break;
  case 3: configuration_command = "{\"method\":\"set_position\",\"x\":4,\"y\":0}"; break;
  case 4: configuration_command = "{\"method\":\"set_decoder_options\",\"max_mcus\":32,\"native_blocks\":false}"; break;
  default: configuration_command = "{\"method\":\"stop\"}"; break;
  }
  assert(requests.command(configuration_command)["ok"] == true);
  auto original = painter.memory; auto writes = painter.write_calls;
  auto frames = requests.command("{\"method\":\"get_benchmark\"}")["result"]["frames"].as<unsigned>();
  auto checked = requests.command("{\"method\":\"probe_pipeline\",\"stage\":\"frame_integrity\"}");
  assert(checked["error"]["code"] == "unsupported_configuration");
  assert(painter.arms == 0 && painter.disarms == 0 && painter.write_calls == writes && painter.memory == original);
  assert(requests.command("{\"method\":\"get_benchmark\"}")["result"]["frames"] == frames);
}
void unsupported_painter(const std::vector<uint8_t> &bytes) {
  Clock clock; Storage storage(clock, bytes); Runtime runtime(clock); Painter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin();
  Requests requests(engine, output); ready(engine, requests); int rows = painter.rows;
  assert(requests.command("{\"method\":\"probe_pipeline\",\"stage\":\"frame_integrity\"}")["error"]["code"] == "unsupported_configuration");
  assert(painter.rows == rows);
}
int main(int argc, char **argv) {
  assert(argc == 2); std::ifstream file(argv[1], std::ios::binary); assert(file);
  std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(file)), {});
  verify(bytes, false); verify(bytes, true);
  for (unsigned failure = 0; failure < 4; failure++) failures(bytes, failure);
  for (unsigned geometry = 0; geometry < 6; geometry++) unsupported_geometry(bytes, geometry);
  unsupported_painter(bytes);
}
'''


def container(jpeg):
    crc = zlib.crc32(jpeg)
    record = struct.pack("<IIII", len(jpeg), 0, crc, 0) + jpeg + bytes((-len(jpeg)) % 4)
    index = struct.pack("<QQII", 96, 0, len(jpeg), crc)
    header = bytearray(96)
    header[:8] = b"MVBJPG1\0"
    for offset, fmt, value in [(8, "H", 1), (10, "H", 96), (16, "H", 317),
                              (18, "H", 239), (20, "I", 30), (24, "I", 1),
                              (28, "I", 1), (32, "Q", 1000000 // 30), (40, "Q", 96),
                              (48, "Q", 96 + len(record)), (56, "Q", 96 + len(record) + len(index)),
                              (64, "I", len(jpeg)), (68, "H", 24), (70, "H", 16),
                              (72, "I", zlib.crc32(index))]:
        struct.pack_into("<" + fmt, header, offset, value)
    struct.pack_into("<I", header, 76, zlib.crc32(header))
    return header + record + index


@unittest.skipUnless(CXX, "A C++ compiler is required")
class FrameIntegrityTests(unittest.TestCase):
    def test_actual_codec_full_pixel_readback_is_uncounted_and_preserves_image(self):
        dependency = ROOT / "firmware/.pio/libdeps/cyd_ili9341"
        if not (dependency / "JPEGDEC/src/JPEGDEC.cpp").is_file():
            self.skipTest("Install the pinned PlatformIO dependencies")
        with tempfile.TemporaryDirectory(prefix="muse-integrity-") as directory:
            directory = Path(directory)
            adapters = (ROOT / "firmware/native/profile_tests.cpp").read_text()
            before, after = adapters.rsplit("}", 1)
            (directory / "profile-adapters.hpp").write_text(before + "return 0;\n}" + after)
            (directory / "driver.cpp").write_text(SOURCE)
            (directory / "video.mvb").write_bytes(container((ROOT / "tests/fixtures/tjpgd_fast/edge-2x2.jpg").read_bytes()))
            for coalesced in [False, True]:
                with self.subTest(coalesced_record=coalesced):
                    executable = directory / ("record" if coalesced else "separate")
                    command = [CXX, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
                               "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-D__LINUX__", "-DNO_SIMD",
                               "-include", str(ROOT / "firmware/include/jpeg_portable.hpp"),
                               "-I", str(ROOT / "firmware/include"), "-I", str(dependency / "ArduinoJson/src"),
                               "-I", str(dependency / "JPEGDEC/src"), str(directory / "driver.cpp"),
                               str(dependency / "JPEGDEC/src/JPEGDEC.cpp"), "-Wno-unused-parameter",
                               "-Wno-unused-variable", "-Wno-sign-compare", "-Wno-type-limits", "-o", str(executable)]
                    if coalesced:
                        command.append("-DMVB_FRAME_RECORD_READ")
                    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(executable), str(directory / "video.mvb")], capture_output=True, text=True, timeout=90)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
