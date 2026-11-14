"""Shared-engine decoder and independent SPI option tests; host-only adapters."""

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

class ConfiguredPainter : public Painter {
public:
  mvb::RenderOptions configured;
  int configurations = 0;
  using Painter::Painter;
  bool configure(const mvb::RenderOptions &next) override {
    configured = next; ++configurations; return true;
  }
};

void check(const std::vector<uint8_t> &bytes, bool row_fail, bool completion_fail) {
  Clock clock; Storage storage(clock, bytes); Runtime runtime(clock);
  ConfiguredPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin();
  Requests requests(engine, output);
  for (int tries = 0; tries < 10000; tries++) {
    engine.tick();
    auto status = requests.command("{\"method\":\"get_status\"}");
    if (!status["result"]["frame_index"].isNull()) break;
    assert(status["result"]["state"] != "error");
  }
  assert(requests.command("{\"method\":\"pause\"}")["ok"] == true);
  auto configure = requests.command("{\"method\":\"set_render_options\",\"mode\":\"dma\",\"big_endian\":false,\"spi_hz\":80000000}");
  assert(configure["ok"] == true);
  assert(configure["result"]["requested_command_spi_hz"] == 40000000);
  assert(painter.configured.spi_hz == 80000000 && painter.configured.command_spi_hz == 40000000);
  configure = requests.command("{\"method\":\"set_render_options\",\"mode\":\"blocks\",\"big_endian\":true,\"spi_hz\":40000000,\"command_spi_hz\":10000000}");
  assert(configure["ok"] == true && painter.configured.command_spi_hz == 10000000);
  const int before = painter.configurations;
  for (const char *cmd : {
      "{\"method\":\"set_render_options\",\"mode\":\"dma\",\"big_endian\":false,\"spi_hz\":40000000,\"command_spi_hz\":12000000}",
      "{\"method\":\"set_render_options\",\"mode\":\"dma\",\"big_endian\":false,\"spi_hz\":40000000,\"command_spi_hz\":\"20\"}",
      "{\"method\":\"set_render_options\",\"mode\":\"dma\",\"big_endian\":false,\"spi_hz\":123}"})
    assert(requests.command(cmd)["error"]["code"] == "invalid_parameter");
  assert(requests.command("{\"method\":\"set_render_options\",\"mode\":\"legacy\",\"big_endian\":false,\"spi_hz\":20000000,\"command_spi_hz\":80000000}")["error"]["code"] == "unsupported_configuration");
  assert(painter.configurations == before);
  assert(requests.command("{\"method\":\"set_render_options\",\"mode\":\"legacy\",\"big_endian\":false}")["ok"] == true);
  assert(painter.configured.spi_hz == 20000000 && painter.configured.command_spi_hz == 20000000);

  auto decoder = requests.command("{\"method\":\"set_decoder_options\",\"max_mcus\":32,\"native_blocks\":true}");
  assert(decoder["ok"] == true && decoder["result"]["decoder"] == "jpegdec");
  assert(decoder["result"]["max_mcus_applies"] == true);
  for (const char *name : {"rom", "new_jpeg"}) {
    std::string command = "{\"method\":\"set_decoder_options\",\"decoder\":\"" + std::string(name) + "\",\"max_mcus\":32,\"native_blocks\":true}";
    assert(requests.command(command.c_str())["error"]["code"] == "unsupported_configuration");
    assert(requests.command("{\"method\":\"get_benchmark\"}")["result"]["decoder"] == "jpegdec");
  }
  assert(requests.command("{\"method\":\"set_decoder_options\",\"decoder\":\"unknown\",\"max_mcus\":32,\"native_blocks\":true}")["error"]["code"] == "invalid_parameter");
  decoder = requests.command("{\"method\":\"set_decoder_options\",\"decoder\":\"tjpgd_fast\",\"max_mcus\":32,\"native_blocks\":true}");
#ifdef MVB_ENABLE_TJPGD_FAST
  assert(decoder["ok"] == true && decoder["result"]["decoder"] == "tjpgd_fast");
  assert(decoder["result"]["max_mcus_applies"] == false);
  assert(decoder["result"]["decoder_workspace_bytes"].as<size_t>() >= 20480);
  assert(requests.command("{\"method\":\"get_benchmark\"}")["result"]["decoder"] == "tjpgd_fast");
  painter.fail_row = row_fail; painter.fail_completion = completion_fail;
  assert(requests.command("{\"method\":\"restart\"}")["ok"] == true);
  engine.tick();
  auto status = requests.command("{\"method\":\"get_status\"}");
  auto measured = requests.command("{\"method\":\"get_benchmark\"}");
  if (row_fail || completion_fail) {
    assert(status["result"]["state"] == "error");
    assert(status["result"]["error"] == "display_failed");
    assert(measured["result"]["frames"] == 0);
  } else {
    assert(status["result"]["state"] == "playing" && measured["result"]["frames"] == 1);
    auto stages = requests.command("{\"method\":\"get_pipeline_profile\"}");
    assert(stages["result"]["color_conversion"] == "fused_with_jpeg_decode");
    assert(stages["result"]["average_us"]["decoder_strip_copy_nested"].as<double>() > 0);
    assert(requests.command("{\"method\":\"set_decoder_options\",\"max_mcus\":8,\"native_blocks\":false}")["result"]["decoder"] == "tjpgd_fast");
    decoder = requests.command("{\"method\":\"set_decoder_options\",\"decoder\":\"jpegdec\",\"max_mcus\":32,\"native_blocks\":true}");
    assert(decoder["ok"] == true && decoder["result"]["decoder_workspace_bytes"] == 0);
  }
#else
  (void)row_fail; (void)completion_fail;
  assert(decoder["error"]["code"] == "unsupported_configuration");
#endif
}
int main(int argc, char **argv) {
  assert(argc == 2);
  std::ifstream file(argv[1], std::ios::binary); assert(file);
  std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(file)), {});
  check(bytes, false, false); check(bytes, true, false); check(bytes, false, true);
}
'''


@unittest.skipUnless(CXX, "A C++ compiler is required")
class DecoderOptionsTests(unittest.TestCase):
    def test_shared_engine_options_and_real_fast_decoder_failure_accounting(self):
        dependency = ROOT / "firmware/.pio/libdeps/cyd_ili9341"
        if not (dependency / "JPEGDEC/src/JPEGDEC.cpp").is_file():
            self.skipTest("Install the pinned PlatformIO dependencies")
        jpeg = (ROOT / "tests/fixtures/tjpgd_fast/edge-2x2.jpg").read_bytes()
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
        with tempfile.TemporaryDirectory(prefix="muse-decoder-options-") as directory:
            directory = Path(directory)
            adapters = (ROOT / "firmware/native/profile_tests.cpp").read_text()
            # Renamed main loses C++'s implicit return. Keep the existing host
            # adapters intact and add that return only to this temporary copy.
            before, after = adapters.rsplit("}", 1)
            (directory / "profile-adapters.hpp").write_text(before + "return 0;\n}" + after)
            (directory / "driver.cpp").write_text(SOURCE)
            (directory / "video.mvb").write_bytes(header + record + index)
            for enabled in [False, True]:
                with self.subTest(fast_decoder_enabled=enabled):
                    executable = directory / ("enabled" if enabled else "disabled")
                    command = [CXX, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
                               "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                               "-D__LINUX__", "-DNO_SIMD", "-include", str(ROOT / "firmware/include/jpeg_portable.hpp"),
                               "-I", str(ROOT), "-I", str(ROOT / "firmware/include"),
                               "-I", str(dependency / "ArduinoJson/src"), "-I", str(dependency / "JPEGDEC/src"),
                               str(directory / "driver.cpp"), str(dependency / "JPEGDEC/src/JPEGDEC.cpp"),
                               "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-sign-compare",
                               "-o", str(executable)]
                    if enabled:
                        command += ["-DMVB_ENABLE_TJPGD_FAST", str(ROOT / "firmware/src/tjpgd_fast.cpp")]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(executable), str(directory / "video.mvb")],
                                            capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
