"""Actual engine, renderer, codec leases and worker against delayed host SPI.

This validates cold-start ownership/fallback/defaults, never ESP performance.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jpeg_warmup_test import CXX, ROOT, SOURCE as WARM_SOURCE, replace_once

SOURCE = WARM_SOURCE[:WARM_SOURCE.index("int main(int argc, char **argv)")]
SOURCE = "bool fail_prefetch_config = false, fail_codec_open = false;\n" + SOURCE
SOURCE = replace_once(SOURCE, "++opens; *h = new Codec{*c}; return JPEG_ERR_OK;",
                      "++opens; if (fail_codec_open) return JPEG_ERR_NO_MEM; *h = new Codec{*c}; return JPEG_ERR_OK;")
SOURCE = replace_once(SOURCE, "assert(width == 16 && height == 16);",
                      "assert(width > 0 && width <= 320 && height > 0 && height <= 240);")
SOURCE = replace_once(SOURCE, "extern \"C\" jpeg_error_t jpeg_dec_get_process_count(jpeg_dec_handle_t, int *n) {\n  *n = 2; return JPEG_ERR_OK;\n}",
                      "extern \"C\" jpeg_error_t jpeg_dec_get_process_count(jpeg_dec_handle_t h, int *n) { *n = static_cast<Codec *>(h)->height / 8; return JPEG_ERR_OK; }")
SOURCE = replace_once(SOURCE, "if (c->block == 2) { c->partial = false; borrowed = nullptr; borrowed_size = 0; }",
                      "if (c->block == c->height / 8) { c->partial = false; borrowed = nullptr; borrowed_size = 0; }")
MAIN = r'''
class FastRuntime : public Runtime {
public:
  explicit FastRuntime(Clock &c) : Runtime(c) {}
  uint32_t configured_idle_yield_us() const override { return adaptive_loop.idle_yield_us; }
};
std::vector<uint8_t> media(unsigned fps = 25) {
  auto bytes = video_bytes(); mvb::Rate rate{fps, 1};
  mvb::put32(bytes.data() + 20, fps);
  mvb::put64(bytes.data() + 32, rate.pts(40));
  const size_t index = mvb::get64(bytes.data() + 48);
  for (size_t n = 0; n < 40; ++n)
    mvb::put64(bytes.data() + index + n * 24 + 8, rate.pts(n));
  mvb::put32(bytes.data() + 72, mvb::crc32(bytes.data() + index, 40 * 24));
  mvb::put32(bytes.data() + 76, 0);
  mvb::put32(bytes.data() + 76, mvb::crc32(bytes.data(), 96));
  return bytes;
}
JsonDocument defaults(Requests &r) { return r.command("{\"method\":\"get_playback_defaults\"}"); }
JsonDocument display(TftPainter &p) { JsonDocument doc; p.info(doc.to<JsonObject>()); return doc; }
void cleanup(TftPainter &p) {
  mvb::RenderOptions polling; assert(p.configure(polling));
  assert(dma_allocations.empty() && pending_transfers.empty() && !transaction_open);
}
void check_cold_clears(mvb::Rect region) {
  for (int y = 0; y < 240; ++y)
    for (int x = 0; x < 320; ++x) {
      const bool inside = x >= region.x && x < region.x + region.w &&
                          y >= region.y && y < region.y + region.h;
      assert(pixel_rectangle_clears[y * 320 + x] == unsigned(!inside));
      if (!inside) assert(gram[y * 320 + x] == TFT_BLACK);
    }
}
void boot(const std::string &fault) {
  Clock clock; auto bytes = media();
  if (fault == "crc") bytes[96 + 16 + 3] ^= 1;
  Storage storage(clock, bytes, true); FastRuntime runtime(clock); Output output;
  TftPainter p; p.begin(); codec_clock = &clock;
  if (fault == "direct_oom") free_heap = 60000; // DMA3 cannot reserve, DMA2 can.
  if (fault == "dma_oom") free_heap = 50000;
  fail_prefetch_config = fault == "prefetch";
  fail_codec_open = fault == "codec";
  {
    auto engine = std::make_unique<mvb::Engine>(storage, p, runtime, output);
    engine->begin(); Requests r(*engine, output);
    if (fault == "crc") {
      for (unsigned n = 0; n < 1000; ++n) {
        engine->tick();
        if (r.command("{\"method\":\"get_status\"}")["result"]["state"] == "error") break;
      }
      assert(warm_status(r)["result"]["frames"] == 0 && !borrowed);
      assert(std::all_of(pixel_rectangle_clears.begin(), pixel_rectangle_clears.end(),
                        [](unsigned n) { return n == 0; }));
      assert(r.command("{\"method\":\"get_status\"}")["result"]["error"] == "crc_mismatch");
    } else {
      ready(*engine, r); auto chosen = defaults(r); auto opt = chosen["result"];
      check_cold_clears({152, 112, 16, 16});
      assert(opt["idle_yield_us"] == 1000000);
      assert(opt["display_options_available"] == true);
      assert(opt["display_spi_hz"] == 40000000 && opt["display_command_spi_hz"] == 20000000);
      assert(opt["display_big_endian"] == true);
      assert(r.command("{\"method\":\"get_status\"}")["result"]["target_fps"]["numerator"] == 25);
      assert(r.command("{\"method\":\"get_startup_profile\"}")["result"]["available"] == false);
      auto hardware = display(p);
      if (fault == "direct_oom") {
        assert(opt["display_mode"] == "dma" && opt["display_zero_copy"] == false);
        assert(opt["display_fallback"] == "direct_dma_unavailable_using_copied_dma");
        assert(hardware["dma_buffer_slots"] == 2 && hardware["copied_dma_submissions"].as<unsigned>() > 0);
      } else if (fault == "dma_oom") {
        assert(opt["display_mode"] == "blocks" && opt["display_zero_copy"] == false);
        assert(opt["display_fallback"] == "dma_unavailable_using_blocks");
        assert(hardware["dma_buffer_slots"] == 0);
      } else {
        assert(opt["display_mode"] == "dma" && opt["display_zero_copy"] == true);
        assert(opt["display_fallback"].isNull());
        assert(hardware["dma_buffer_slots"] == 3);
      }
      if (fault == "codec") {
        assert(opt["decoder"] == "jpegdec" && opt["warmup_selected"] == false);
        assert(opt["decoder_fallback"] == "new_jpeg_prepare_failed");
        assert(hardware["zero_copy_direct_submissions"] == 0 && hardware["copied_dma_submissions"].as<unsigned>() > 0);
      } else {
        assert(opt["decoder"] == "new_jpeg");
        if (fault != "direct_oom" && fault != "dma_oom") {
          assert(allocations == 0); // Header preflight never reserves redundant RGB output.
          assert(hardware["zero_copy_direct_submissions"].as<unsigned>() > 0);
          assert(warm_status(r)["result"]["decoder_owned_bytes"].as<unsigned>() == sizeof(mvb::NewJpeg));
        }
      }
      assert(opt["prefetch_enabled"] == (fault != "prefetch"));
      if (fault == "prefetch") {
        assert(opt["warmup_selected"] == false);
        assert(opt["prefetch_fallback"] == "prefetch_memory_or_worker_unavailable");
      }
      auto benchmark = warm_status(r);
      assert(measureJson(benchmark) < 2048);
#ifdef MVB_ALIGN_COMPRESSED_RECORDS
      static_assert(alignof(mvb::Engine) >= 16, "Heap engine must retain record alignment");
      assert(benchmark["result"]["input_alignment"]["fixed_record_mod16"] == 0);
      assert(benchmark["result"]["input_alignment"]["current_payload_mod16"] == 0);
#endif
      assert(hardware["dma_ownership_errors"] == 0);
      assert(r.command("{\"method\":\"pause\"}")["ok"] == true);
      assert(display(p)["zero_copy_active_leases"] == 0);
    }
    assert(storage.original_bytes() == bytes);
  }
  assert(!borrowed && display(p)["zero_copy_active_leases"] == 0);
  cleanup(p);
}
// Only the checked SOF dimensions change here. The fake codec generates its
// pixel oracle; this fixture does not claim real expanded entropy decoding.
std::vector<uint8_t> geometry_media(unsigned width, unsigned height) {
  auto payload = jpeg_with_comment(40);
  for (size_t k = 0; k + 8 < payload.size(); ++k)
    if (payload[k] == 0xff && payload[k + 1] == 0xc0) {
      payload[k + 5] = uint8_t(height >> 8); payload[k + 6] = uint8_t(height);
      payload[k + 7] = uint8_t(width >> 8); payload[k + 8] = uint8_t(width);
      break;
    }
  auto bytes = container_bytes({payload});
  mvb::put16(bytes.data() + 16, width); mvb::put16(bytes.data() + 18, height);
  mvb::put32(bytes.data() + 76, 0);
  mvb::put32(bytes.data() + 76, mvb::crc32(bytes.data(), 96));
  return bytes;
}
void roi() {
  for (const auto &size : {std::pair<unsigned, unsigned>{320, 240}, {304, 224}, {320, 184}}) {
    std::fill(pixel_rectangle_clears.begin(), pixel_rectangle_clears.end(), 0);
    std::fill(pixel_writes.begin(), pixel_writes.end(), 0);
    Clock clock; const auto bytes = geometry_media(size.first, size.second);
    Storage storage(clock, bytes); FastRuntime runtime(clock); Output output;
    TftPainter p; p.begin(); codec_clock = &clock;
    const mvb::Rect region{int((320-size.first)/2), int((240-size.second)/2), int(size.first), int(size.second)};
    {
      auto engine = std::make_unique<mvb::Engine>(storage, p, runtime, output);
      engine->begin(); Requests r(*engine, output); ready(*engine, r);
      check_cold_clears(region);
      for (int y = 0; y < 240; ++y)
        for (int x = 0; x < 320; ++x) {
          const bool inside = x >= region.x && x < region.x+region.w && y >= region.y && y < region.y+region.h;
          assert(pixel_writes[y*320+x] == unsigned(inside));
        }
      if (size.first == 320 && size.second == 240)
        assert(std::all_of(pixel_rectangle_clears.begin(), pixel_rectangle_clears.end(), [](unsigned n) { return n == 0; }));
      assert(display(p)["dma_ownership_errors"] == 0);
    }
    cleanup(p);
  }
  // The same helper safely clears odd clipped borders without touching them.
  for (const mvb::Transform &t : {mvb::Transform{-5, -3, 17, 13, 0, false},
                                 mvb::Transform{315, 235, 17, 13, 0, false},
                                 mvb::Transform{150, 113, 17, 13, 0, false}}) {
    TftPainter p; p.begin();
    std::fill(pixel_rectangle_clears.begin(), pixel_rectangle_clears.end(), 0);
    const auto region = mvb::clipped(t);
    mvb::clear_difference(p, {0, 0, 320, 240}, region); check_cold_clears(region);
  }
}
void lifetime() {
  Clock clock; Storage storage(clock, media(), true); FastRuntime runtime(clock); Output output;
  TftPainter p; p.begin(); codec_clock = &clock;
  {
    auto engine = std::make_unique<mvb::Engine>(storage, p, runtime, output);
    engine->begin(); Requests r(*engine, output); ready(*engine, r);
    for (unsigned n = 0; n < 20; ++n) {
      clock.advance(40000); engine->tick();
      assert(defaults(r)["result"]["display_zero_copy"] == true);
      assert(display(p)["dma_ownership_errors"] == 0);
    }
    assert(r.command("{\"method\":\"pause\"}")["ok"] == true);
    assert(r.command("{\"method\":\"set_fps\",\"numerator\":7,\"denominator\":1}")["ok"] == true);
    for (unsigned n = 0; n < 8; ++n) {
      assert(r.command("{\"method\":\"select_video\",\"sha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}")["ok"] == true);
      ready(*engine, r);
      assert(defaults(r)["result"]["decoder"] == "new_jpeg");
      assert(r.command("{\"method\":\"get_status\"}")["result"]["target_fps"]["numerator"] == 7);
      assert(r.command("{\"method\":\"restart\"}")["ok"] == true); engine->tick();
      assert(warm_status(r)["result"]["frames"] == 1);
    }
    assert(r.command("{\"method\":\"begin_upload\",\"sha256\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"size\":136,\"container_version\":1}")["ok"] == true);
    assert(display(p)["zero_copy_active_leases"] == 0 && !borrowed);
    assert(defaults(r)["result"]["decoder"] == "jpegdec");
  }
  cleanup(p);
}
int main(int argc, char **argv) {
  assert(argc == 2); std::string fault = argv[1];
  if (fault == "lifetime") lifetime(); else if (fault == "roi") roi(); else boot(fault);
}
'''


@unittest.skipUnless(CXX, "A C++ compiler is required")
class FastDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dependency = ROOT / "firmware/.pio/libdeps/cyd_ili9341"
        if not (dependency / "JPEGDEC/src/JPEGDEC.cpp").is_file():
            raise unittest.SkipTest("Install pinned PlatformIO dependencies")
        cls.directory = tempfile.TemporaryDirectory(prefix="muse-fast-defaults-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
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
        sdk = (ROOT / "tests/fixtures/dma_direct_fake_sdk.hpp").read_text()
        sdk = replace_once(sdk, "struct Storage {", "struct DisplayStorageInfo {")
        (directory / "dma_direct_fake_sdk.hpp").write_text(sdk)
        source = (ROOT / "firmware/src/main.cpp").read_text()
        policy = source[source.index("struct AdaptiveLoopDiagnostics {"):source.index("} adaptive_loop;") + len("} adaptive_loop;")]
        painter = source[source.index("class TftPainter :"):source.index("class EspRuntime :")]
        prefix = "#include <ArduinoJson.h>\n#include <algorithm>\n#include <cstdint>\n"
        (directory / "driver.cpp").write_text(prefix + policy + SOURCE + '#include "dma_direct_fake_sdk.hpp"\n' + painter + MAIN)
        cls.executables = {}
        for aligned in (False, True):
            executable = directory / ("aligned" if aligned else "normal")
            command = [CXX, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-pthread",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-DMVB_ENABLE_NEW_JPEG",
                       "-DMVB_FRAME_PREFETCH", "-DMVB_FRAME_RECORD_READ", "-DMVB_JPEG_FIRST_BLOCK_WARMUP",
                       "-DMVB_QUALIFIED_DEFAULTS", "-DMVB_QUALIFIED_FAST_DEFAULTS", "-DMVB_JPEG_DMA_DIRECT_OUTPUT",
                       "-DMVB_DMA_QUEUE_DEPTH=2", "-DCONFIG_ESP_TASK_WDT_TIMEOUT_S=5", "-DMVB_JPEG_MAX_BUFFERED_PIXELS=5120",
                       "-D__LINUX__", "-DNO_SIMD", "-include", str(ROOT / "firmware/include/jpeg_portable.hpp"),
                       "-I", str(directory), "-I", str(ROOT / "firmware/include"),
                       "-I", str(dependency / "ArduinoJson/src"), "-I", str(dependency / "JPEGDEC/src"),
                       str(directory / "driver.cpp"), str(dependency / "JPEGDEC/src/JPEGDEC.cpp"),
                       "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-sign-compare", "-Wno-type-limits",
                       "-o", str(executable)]
            if aligned:
                command.append("-DMVB_ALIGN_COMPRESSED_RECORDS")
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.executables[aligned] = executable

    def scenario(self, name, aligned=False):
        result = subprocess.run([str(self.executables[aligned]), name], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cold_boot_selects_direct_and_25fps_without_manual_setup_or_redundant_strip(self):
        self.scenario("normal")

    def test_three_slot_memory_failure_falls_back_to_two_copied_slots(self):
        self.scenario("direct_oom")

    def test_all_dma_memory_failure_falls_back_to_blocks_with_endian_preserved(self):
        self.scenario("dma_oom")

    def test_codec_or_prefetch_failure_keeps_checked_playback(self):
        self.scenario("codec")
        self.scenario("prefetch")

    def test_crc_failure_never_presents_a_frame(self):
        self.scenario("crc")

    def test_warmup_reload_restart_and_upload_release_all_borrowed_inputs_and_leases(self):
        self.scenario("lifetime")

    def test_optional_aligned_heap_engine_keeps_fixed_and_payload_pointer_aligned(self):
        self.scenario("normal", aligned=True)
        self.scenario("lifetime", aligned=True)

    def test_cold_start_clears_only_borders_full_frame_never_clears_and_odd_clips_are_safe(self):
        self.scenario("roi")


if __name__ == "__main__":
    unittest.main()
