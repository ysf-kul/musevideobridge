"""Guarded production boot/load defaults with actual engine/container/worker.

JPEG blocks and hardware configuration are host substitutes. This checks
selection, fallback, ownership and requested rates, not physical performance.
"""

import configparser
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jpeg_warmup_test import CXX, ROOT, SOURCE as WARM_SOURCE, replace_once

SOURCE = WARM_SOURCE[:WARM_SOURCE.index("int main(int argc, char **argv)")]
SOURCE = "bool fail_prefetch_config = false, fail_decoder_allocate = false;\n" + SOURCE
SOURCE = replace_once(SOURCE, "++allocations; assert(align == 16);",
                      "++allocations; assert(align == 16); if (fail_decoder_allocate && bytes != 10240) return nullptr;")
SOURCE = replace_once(SOURCE, 'assert(state["result"]["state"] != "error");',
                      'if (state["result"]["state"] == "error") { char json[2048]; serializeJson(state, json, sizeof(json)); std::fprintf(stderr, "%s\\n", json); } assert(state["result"]["state"] != "error");')
SOURCE += r'''
#include "unaligned.hpp"
class QualifiedPainter : public OwnedPainter {
public:
  std::vector<mvb::RenderOptions> configurations;
  bool fail_dma = false;
  mvb::RenderOptions actual_options;
  explicit QualifiedPainter(Clock &c) : OwnedPainter(c) {}
  bool configure(const mvb::RenderOptions &options) override {
    configurations.push_back(options);
    if (options.mode == mvb::RenderMode::Dma && fail_dma) return false;
    actual_options = options; be = options.big_endian; return true;
  }
  bool configured_render_options(mvb::RenderOptions &options) const override {
    options = actual_options; return true;
  }
  bool block(int x, int y, const uint16_t *p, int w, int h, int stride) override {
    // JPEGDEC's clipped odd-width edge can retain a wider source stride.
    if (w != stride) return Painter::block(x, y, p, w, h, stride);
    return OwnedPainter::block(x, y, p, w, h, stride);
  }
};
std::vector<uint8_t> prepared(unsigned fps = 30, bool aligned = true) {
  auto payload = aligned ? jpeg_with_comment(40)
                         : std::vector<uint8_t>(unaligned_jpeg, unaligned_jpeg + sizeof(unaligned_jpeg));
  std::vector<std::vector<uint8_t>> frames(12, payload);
  auto bytes = container_bytes(frames); mvb::Rate rate{fps, 1};
  mvb::put16(bytes.data() + 16, aligned ? 16 : 17);
  mvb::put16(bytes.data() + 18, aligned ? 16 : 13);
  mvb::put32(bytes.data() + 20, fps);
  mvb::put64(bytes.data() + 32, rate.pts(frames.size()));
  const size_t index = mvb::get64(bytes.data() + 48);
  for (size_t n = 0; n < frames.size(); ++n)
    mvb::put64(bytes.data() + index + n * 24 + 8, rate.pts(n));
  mvb::put32(bytes.data() + 72, mvb::crc32(bytes.data() + index, frames.size() * 24));
  mvb::put32(bytes.data() + 76, 0);
  mvb::put32(bytes.data() + 76, mvb::crc32(bytes.data(), 96));
  return bytes;
}
JsonDocument defaults(Requests &r) {
  return r.command("{\"method\":\"get_playback_defaults\"}");
}
void boot(const std::string &fault, unsigned fps = 30) {
  Clock clock; auto bytes = prepared(fps, fault != "unaligned");
  Storage storage(clock, bytes, true); Runtime runtime(clock);
  QualifiedPainter painter(clock); painter.fail_dma = fault == "dma";
  fail_prefetch_config = fault == "prefetch";
  fail_decoder_allocate = fault == "decoder";
  Output output;
  {
    mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
    ready(engine, r);
    auto reported = defaults(r); auto options = reported["result"];
    auto status = r.command("{\"method\":\"get_status\"}");
    assert(status["result"]["state"] == "playing");
#ifdef MVB_QUALIFIED_DEFAULTS
    assert(options["enabled"] == true);
    assert(painter.configurations.size() == (fault == "dma" ? 2 : 1));
    for (const auto &c : painter.configurations)
      assert(c.spi_hz == 40000000 && c.command_spi_hz == 20000000 && c.big_endian);
    assert(painter.configurations[0].mode == mvb::RenderMode::Dma);
    assert(status["result"]["target_fps"]["numerator"] == fps);
    assert(options["target_fps_source"] == "encoded_video");
    assert(options["warmup_blocks"] == 1);
    auto benchmark = warm_status(r);
    assert(benchmark["result"]["frames"] == 1);
    assert(benchmark["result"]["decoder_pipeline"]["cache_owned_bytes"] == 0);
    if (fault == "decoder" || fault == "unaligned") {
      assert(options["decoder"] == "jpegdec" && options["warmup_selected"] == false);
      assert(options["decoder_fallback"] == (fault == "decoder" ? "new_jpeg_prepare_failed" : "new_jpeg_requires_multiple_of_eight"));
    } else {
      assert(options["decoder"] == "new_jpeg" && options["decoder_fallback"].isNull());
    }
    if (fault == "prefetch") {
      assert(options["prefetch_enabled"] == false && options["warmup_selected"] == false);
      assert(options["prefetch_fallback"] == "prefetch_memory_or_worker_unavailable");
    } else {
      assert(options["prefetch_enabled"] == true && options["prefetch_fallback"].isNull());
    }
    if (fault == "dma") {
      assert(options["display_fallback"] == "dma_unavailable_using_blocks");
      assert(painter.configurations.back().mode == mvb::RenderMode::Blocks);
    } else assert(options["display_fallback"].isNull());
    if (fault.empty()) {
      assert(options["warmup_selected"] == true);
      assert(benchmark["result"]["decoder_pipeline"]["cached"] == true);
      assert(defaults(r)["result"]["warmup_selected"] == true);
      assert(warm_status(r)["result"]["decoder_pipeline"]["cached"] == true);
    }
#else
    assert(options["enabled"] == false && painter.configurations.empty());
    assert(options["decoder"] == "jpegdec" && options["prefetch_enabled"] == false);
    assert(options["warmup_selected"] == false);
    assert(status["result"]["target_fps"]["numerator"] == 10);
#endif
    assert(painter.begin_calls == painter.end_calls && !frame_open);
    assert(storage.original_bytes() == bytes);
  }
  assert(!borrowed && opens == closes && allocations == frees + unsigned(fault == "decoder"));
}
void reload(Storage &storage, Clock &clock, mvb::Engine &engine, Requests &r,
            unsigned fps, bool aligned = true) {
  assert(r.command("{\"method\":\"pause\"}")["ok"] == true);
  const_cast<std::vector<uint8_t> &>(storage.original_bytes()) = prepared(fps, aligned);
  assert(r.command("{\"method\":\"select_video\",\"sha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}")["ok"] == true);
  clock.advance(100000); ready(engine, r);
}
void targets_and_reload() {
  Clock clock; Storage storage(clock, prepared(), true); Runtime runtime(clock);
  QualifiedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  ready(engine, r);
  assert(r.command("{\"method\":\"set_fps\",\"numerator\":31,\"denominator\":1}")["error"]["code"] == "invalid_parameter");
  reload(storage, clock, engine, r, 20);
  assert(r.command("{\"method\":\"get_status\"}")["result"]["target_fps"]["numerator"] == 20);
  assert(r.command("{\"method\":\"set_fps\",\"numerator\":7,\"denominator\":1}")["ok"] == true);
  reload(storage, clock, engine, r, 30);
  assert(r.command("{\"method\":\"get_status\"}")["result"]["target_fps"]["numerator"] == 7);
  assert(defaults(r)["result"]["target_fps_source"] == "explicit_control");
  // An incompatible new video must never inherit the previous NewJPEG codec.
  reload(storage, clock, engine, r, 15, false);
  assert(defaults(r)["result"]["decoder"] == "jpegdec");
  assert(r.command("{\"method\":\"get_status\"}")["result"]["target_fps"]["numerator"] == 7);
  for (unsigned n = 0; n < 8; ++n) {
    reload(storage, clock, engine, r, 30, true);
    assert(defaults(r)["result"]["decoder"] == "new_jpeg");
    assert(r.command("{\"method\":\"restart\"}")["ok"] == true);
    engine.tick();
    assert(warm_status(r)["result"]["frames"] == 1);
  }
  auto upload = r.command("{\"method\":\"begin_upload\",\"sha256\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"size\":136,\"container_version\":1}");
  assert(upload["ok"] == true && upload["result"]["state"] == "receiving");
  assert(defaults(r)["result"]["decoder"] == "jpegdec");
  assert(!borrowed && !frame_open);
}
void corrupt_first_frame() {
  Clock clock; auto bytes = prepared(); bytes[96 + 16 + 3] ^= 1;
  Storage storage(clock, bytes, true); Runtime runtime(clock);
  QualifiedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  JsonDocument status;
  for (unsigned n = 0; n < 1000; ++n) {
    engine.tick(); status = r.command("{\"method\":\"get_status\"}");
    if (status["result"]["state"] == "error") break;
  }
  assert(status["result"]["state"] == "error");
  assert(status["result"]["error"] == "crc_mismatch");
  assert(warm_status(r)["result"]["frames"] == 0 && opens == 0 && !borrowed);
}
void render_option_parser() {
  Clock clock; Storage storage(clock, prepared(), true); Runtime runtime(clock);
  QualifiedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  ready(engine, r);
  const size_t configurations = painter.configurations.size();
  for (const char *bad : {"0", "1", "-1", "\"true\"", "{}", "[]"}) {
    std::string request = "{\"method\":\"set_render_options\",\"mode\":\"dma\",\"big_endian\":true,\"zero_copy\":" + std::string(bad) + "}";
    assert(r.command(request.c_str())["error"]["code"] == "invalid_parameter");
    assert(painter.configurations.size() == configurations);
  }
  assert(!mvb::NewJpeg::direct_output_available());
  assert(r.command("{\"method\":\"set_render_options\",\"mode\":\"dma\",\"big_endian\":true,\"zero_copy\":true}")["error"]["code"] == "unsupported_configuration");
  assert(painter.configurations.size() == configurations);
  auto accepted = r.command("{\"method\":\"set_render_options\",\"mode\":\"dma\",\"big_endian\":true,\"spi_hz\":40000000,\"command_spi_hz\":20000000,\"zero_copy\":false}");
  assert(accepted["ok"] == true && accepted["result"]["zero_copy"] == false);
  assert(!painter.configurations.back().zero_copy);
  assert(warm_status(r)["result"]["decoder_pipeline"]["cached"] == false);
  accepted = r.command("{\"method\":\"set_render_options\",\"mode\":\"blocks\",\"big_endian\":true}");
  assert(accepted["ok"] == true && accepted["result"]["zero_copy"] == false);
  assert(!painter.configurations.back().zero_copy);
}
int main(int argc, char **argv) {
  assert(argc == 2); std::string fault = argv[1];
  if (fault == "targets") targets_and_reload();
  else if (fault == "render_parser") render_option_parser();
  else if (fault == "crc") corrupt_first_frame();
  else if (fault == "fps25") boot("", 25);
  else boot(fault == "normal" ? "" : fault);
}
'''


class QualifiedEnvironmentTests(unittest.TestCase):
    def test_production_inherits_qualified_flags_and_excludes_chroma_research(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "firmware/platformio.ini")
        self.assertEqual(config["platformio"]["default_envs"], "cyd_optimized_input_aligned")
        self.assertEqual(config["env:cyd_optimized_input_aligned"]["extends"], "env:cyd_optimized")
        self.assertEqual(config["env:cyd_optimized"]["extends"], "env:cyd_jpeg_warmup")
        flags = config["env:cyd_optimized_input_aligned"]["build_flags"]
        for _ in range(20):
            if "${" not in flags:
                break
            before, rest = flags.split("${", 1)
            reference, after = rest.split("}", 1)
            section, key = reference.rsplit(".", 1)
            flags = before + config[section][key] + after
        for flag in ("MVB_QUALIFIED_DEFAULTS", "MVB_FRAME_PREFETCH", "MVB_ENABLE_NEW_JPEG",
                     "MVB_JPEG_FIRST_BLOCK_WARMUP", "MVB_DMA_QUEUE_DEPTH=2",
                     "MVB_QUALIFIED_FAST_DEFAULTS", "MVB_JPEG_DMA_DIRECT_OUTPUT",
                     "MVB_ALIGN_COMPRESSED_RECORDS"):
            self.assertIn("-D" + flag, flags)
        self.assertNotIn("MVB_RESEARCH_JPEG_CHROMA", flags)
        self.assertNotIn("MVB_JPEG_TWO_BLOCK_WARMUP", flags)
        self.assertNotIn("MVB_FIRST_FRAME_PRELOAD", flags)
        self.assertIn("-DMVB_JPEG_TWO_BLOCK_WARMUP", config["env:cyd_jpeg_two_block_warmup"]["build_flags"])
        self.assertNotIn("MVB_QUALIFIED_DEFAULTS", config["env:cyd_jpeg_warmup"]["build_flags"])
        source = (ROOT / "firmware/src/main.cpp").read_text()
        self.assertIn("#ifdef MVB_QUALIFIED_DEFAULTS\n  uint32_t clock_hz_ = 20000000;\n#else\n  uint32_t clock_hz_ = 10000000;\n#endif", source)


@unittest.skipUnless(CXX and shutil.which("ffmpeg"), "Compiler and FFmpeg required")
class QualifiedBootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dependency = ROOT / "firmware/.pio/libdeps/cyd_ili9341"
        if not (dependency / "JPEGDEC/src/JPEGDEC.cpp").is_file():
            raise unittest.SkipTest("Install pinned PlatformIO dependencies")
        cls.directory = tempfile.TemporaryDirectory(prefix="muse-qualified-defaults-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        (directory / "odd.ppm").write_bytes(b"P6\n17 13\n255\n" + bytes(17 * 13 * 3))
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(directory / "odd.ppm"),
                        "-frames:v", "1", "-c:v", "mjpeg", "-pix_fmt", "yuvj420p",
                        "-q:v", "8", "-huffman", "default", str(directory / "odd.jpg")], check=True, timeout=30)
        data = (directory / "odd.jpg").read_bytes()
        (directory / "unaligned.hpp").write_text("static const uint8_t unaligned_jpeg[] = {" + ",".join(map(str, data)) + "};\n")
        (directory / "engine.hpp").write_text((ROOT / "firmware/include/engine.hpp").read_text())
        header = (ROOT / "firmware/include/new_jpeg.hpp").read_text()
        gate = ("#if defined(MVB_ENABLE_NEW_JPEG) && defined(ARDUINO_ARCH_ESP32) &&             "
                + chr(92) + "\n    defined(CONFIG_IDF_TARGET_ESP32)")
        header = replace_once(header, gate, "#if defined(MVB_ENABLE_NEW_JPEG)")
        header = replace_once(header, '"../vendor/esp_new_jpeg/include/esp_jpeg_dec.h"',
                              '"' + str(ROOT / "firmware/vendor/esp_new_jpeg/include/esp_jpeg_dec.h") + '"')
        (directory / "new_jpeg.hpp").write_text(header)
        worker = (ROOT / "firmware/include/frame_prefetch.hpp").read_text()
        worker = replace_once(worker, "bool configure(size_t bytes) {",
                              "bool configure(size_t bytes) { if (::fail_prefetch_config) return false;")
        (directory / "frame_prefetch.hpp").write_text("extern bool fail_prefetch_config;\n" + worker)
        for name in ("core", "profile"):
            adapter = (ROOT / f"firmware/native/{name}_tests.cpp").read_text()
            before, after = adapter.rsplit("}", 1)
            adapter = before + "return 0;\n}" + after
            if name == "profile":
                adapter = replace_once(adapter, "uint64_t time = 10000000;", "std::atomic<uint64_t> time{10000000};")
            (directory / f"{name}-adapters.hpp").write_text(adapter)
        (directory / "driver.cpp").write_text(SOURCE)
        cls.executables = {}
        for guard in (True, False):
            executable = directory / ("qualified" if guard else "traditional")
            command = [CXX, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-pthread",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-DMVB_ENABLE_NEW_JPEG",
                       "-DMVB_FRAME_PREFETCH", "-DMVB_FRAME_RECORD_READ", "-DMVB_JPEG_FIRST_BLOCK_WARMUP",
                       "-D__LINUX__", "-DNO_SIMD", "-include", str(ROOT / "firmware/include/jpeg_portable.hpp"),
                       "-I", str(directory), "-I", str(ROOT / "firmware/include"),
                       "-I", str(dependency / "ArduinoJson/src"), "-I", str(dependency / "JPEGDEC/src"),
                       str(directory / "driver.cpp"), str(dependency / "JPEGDEC/src/JPEGDEC.cpp"),
                       "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-sign-compare", "-Wno-type-limits",
                       "-o", str(executable)]
            if guard:
                command.append("-DMVB_QUALIFIED_DEFAULTS")
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.executables[guard] = executable

    def scenario(self, name, guard=True):
        result = subprocess.run([str(self.executables[guard]), name], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_qualified_boot_selects_measured_options_and_encoded_target(self):
        self.scenario("normal")
        self.scenario("fps25")

    def test_dma_allocation_failure_retains_checked_block_rendering(self):
        self.scenario("dma")

    def test_prefetch_allocation_failure_retains_synchronous_decode(self):
        self.scenario("prefetch")

    def test_decoder_workspace_failure_retains_jpegdec(self):
        self.scenario("decoder")

    def test_odd_prepared_dimensions_use_real_jpegdec(self):
        self.scenario("unaligned")

    def test_explicit_target_persists_and_reload_restart_upload_ownership_remains_safe(self):
        self.scenario("targets")

    def test_traditional_build_without_guard_keeps_previous_defaults(self):
        self.scenario("normal", guard=False)

    def test_automatic_fallback_never_disables_container_crc_validation(self):
        self.scenario("crc")

    def test_zero_copy_parser_is_strict_and_disabled_by_default(self):
        self.scenario("render_parser")
        self.scenario("render_parser", guard=False)


if __name__ == "__main__":
    unittest.main()
