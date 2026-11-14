"""Real container/engine/prefetch ownership with a host-only fake JPEG codec.

The codec substitute exercises state/lifetime/timing, never JPEG quality or ESP
performance. Only a temporary copied target gate changes for host compilation.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")

SOURCE = r'''
#include <atomic>
#include <chrono>
#include <thread>
#include <cstdlib>
#include <string>
#define main profile_main
#include "profile-adapters.hpp"
#undef main
#define main core_main
#include "core-adapters.hpp"
#undef main

std::atomic<const uint8_t *> borrowed{nullptr};
std::atomic<size_t> borrowed_size{0};
bool frame_open = false, fail_warm = false, fail_warm_second = false;
bool fail_cache_allocate = false;
unsigned frame_blocks = 0, warm_pending = 0, opens = 0, closes = 0;
unsigned processes = 0, allocations = 0, frees = 0;
Clock *codec_clock = nullptr;
struct Codec {
  jpeg_dec_config_t config;
  const uint8_t *input = nullptr;
  size_t length = 0;
  uint32_t crc = 0;
  unsigned width = 0, height = 0, block = 0;
  bool partial = false;
};
uint16_t color(unsigned x, unsigned y, uint32_t seed) {
  return uint16_t(1 + x * 31 + y * 521 + seed);
}
extern "C" jpeg_error_t jpeg_dec_open(jpeg_dec_config_t *c, jpeg_dec_handle_t *h) {
  ++opens; *h = new Codec{*c}; return JPEG_ERR_OK;
}
extern "C" jpeg_error_t jpeg_dec_close(jpeg_dec_handle_t h) {
  ++closes; borrowed = nullptr; borrowed_size = 0;
  delete static_cast<Codec *>(h); return JPEG_ERR_OK;
}
extern "C" void *jpeg_calloc_align(size_t bytes, int align) {
  ++allocations; assert(align == 16);
  if (fail_cache_allocate && bytes == 10240) return nullptr;
  return std::aligned_alloc(align, (bytes + align - 1) / align * align);
}
extern "C" void jpeg_free_align(void *p) {
  if (p) { ++frees; std::free(p); }
}
extern "C" jpeg_error_t jpeg_dec_parse_header(jpeg_dec_handle_t h,
    jpeg_dec_io_t *io, jpeg_dec_header_info_t *info) {
  auto *c = static_cast<Codec *>(h); assert(!c->partial);
  assert(codec_clock); codec_clock->advance(700);
  uint16_t width = 0, height = 0;
  assert(mvb::baseline_jpeg(io->inbuf, io->inbuf_len, width, height));
  assert(width == 16 && height == 16);
  c->input = io->inbuf; c->length = io->inbuf_len;
  c->crc = mvb::crc32(c->input, c->length);
  c->width = width; c->height = height; c->block = 0; c->partial = true;
  borrowed_size = c->length; borrowed = c->input;
  info->width = width; info->height = height;
  return JPEG_ERR_OK;
}
extern "C" jpeg_error_t jpeg_dec_get_outbuf_len(jpeg_dec_handle_t h, int *n) {
  *n = static_cast<Codec *>(h)->width * 8 * 2; return JPEG_ERR_OK;
}
extern "C" jpeg_error_t jpeg_dec_get_process_count(jpeg_dec_handle_t, int *n) {
  *n = 2; return JPEG_ERR_OK;
}
extern "C" jpeg_error_t jpeg_dec_process(jpeg_dec_handle_t h, jpeg_dec_io_t *io) {
  ++processes; auto *c = static_cast<Codec *>(h);
  assert(codec_clock); codec_clock->advance(900);
  assert(c->partial && mvb::crc32(c->input, c->length) == c->crc);
  assert(io->outbuf && reinterpret_cast<uintptr_t>(io->outbuf) % 16 == 0);
  if (!c->block && frame_open && frame_blocks == 2) {
    ++warm_pending;
    if (fail_warm) return JPEG_ERR_BAD_DATA;
  }
  if (c->block == 1 && frame_open && frame_blocks == 2 && fail_warm_second)
    return JPEG_ERR_BAD_DATA;
  for (unsigned y = 0; y < 8; ++y)
    for (unsigned x = 0; x < c->width; ++x) {
      uint16_t pixel = color(x, c->block * 8 + y, c->crc);
      const size_t at = (y * c->width + x) * 2;
      bool be = c->config.output_type == JPEG_PIXEL_FORMAT_RGB565_BE;
      io->outbuf[at] = uint8_t(be ? pixel >> 8 : pixel);
      io->outbuf[at + 1] = uint8_t(be ? pixel : pixel >> 8);
    }
  // SDK I/O fields may be mutable; resume must match the original input key.
  ++io->inbuf; --io->inbuf_len;
  io->out_size = c->width * 8 * 2;
  ++c->block;
  if (c->block == 2) { c->partial = false; borrowed = nullptr; borrowed_size = 0; }
  return JPEG_ERR_OK;
}

class OwnedPainter : public Painter {
public:
  bool be = false;
  std::vector<uint16_t> pending;
  const uint16_t *source = nullptr;
  uint32_t pending_crc = 0;
  unsigned ownership_checks = 0, source_changed = 0;
  explicit OwnedPainter(Clock &c) : Painter(c) { codec_clock = &c; }
  bool big_endian() const override { return be; }
  void begin_frame() override {
    assert(!frame_open); frame_open = true; frame_blocks = 0;
    pending.clear(); source = nullptr; Painter::begin_frame();
  }
  bool block(int x, int y, const uint16_t *p, int w, int h, int stride) override {
    assert(frame_open && stride == w);
    // Model the real private DMA strip: the borrowed output is copied before
    // returning, and the final owned strip remains pending until end_frame.
    pending.assign(p, p + w * h); source = p;
    pending_crc = mvb::crc32(reinterpret_cast<const uint8_t *>(pending.data()), pending.size() * 2);
    ++frame_blocks;
    return Painter::block(x, y, p, w, h, stride);
  }
  bool end_frame() override {
    assert(frame_open);
    if (!pending.empty()) {
      assert(mvb::crc32(reinterpret_cast<const uint8_t *>(pending.data()), pending.size() * 2) == pending_crc);
      ++ownership_checks;
      if (mvb::crc32(reinterpret_cast<const uint8_t *>(source), pending.size() * 2) != pending_crc)
        ++source_changed;
    }
    frame_open = false; return Painter::end_frame();
  }
};
void ready(mvb::Engine &engine, Requests &requests) {
  for (unsigned tries = 0; tries < 1000; ++tries) {
    engine.tick(); auto state = requests.command("{\"method\":\"get_status\"}");
    assert(state["result"]["state"] != "error");
    if (!state["result"]["frame_index"].isNull()) return;
  }
  assert(false);
}
void select(mvb::Engine &engine, Requests &r, unsigned blocks = 1) {
  ready(engine, r);
  assert(r.command("{\"method\":\"pause\"}")["ok"] == true);
  assert(r.command("{\"method\":\"set_decoder_options\",\"decoder\":\"new_jpeg\",\"max_mcus\":32,\"native_blocks\":true}")["ok"] == true);
  assert(r.command("{\"method\":\"set_prefetch_options\",\"enabled\":true}")["ok"] == true);
  std::string pipeline = "{\"method\":\"set_decoder_pipeline\",\"warmup\":true,\"blocks\":" + std::to_string(blocks) + "}";
  auto response = r.command(pipeline.c_str());
#ifdef MVB_JPEG_FIRST_BLOCK_WARMUP
  assert(response["ok"] == true);
  assert(response["result"]["blocks"] == blocks);
  assert(response["result"]["cache_owned_bytes"] == (blocks == 2 ? 10240 : 0));
#else
  assert(response["error"]["code"] == "unsupported_configuration");
#endif
  assert(r.command("{\"method\":\"restart\"}")["ok"] == true);
}
std::vector<uint8_t> video_bytes() {
  std::vector<std::vector<uint8_t>> payloads;
  for (unsigned n = 0; n < 40; ++n) payloads.push_back(jpeg_with_comment(40 + n));
  return container_bytes(payloads);
}
JsonDocument warm_status(Requests &r) {
  return r.command("{\"method\":\"get_benchmark\"}");
}
void successful_hits(bool be, unsigned blocks = 1) {
  Clock clock; Storage storage(clock, video_bytes(), true); Runtime runtime(clock);
  OwnedPainter painter(clock); painter.be = be; Output output;
  {
    mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
    select(engine, r, blocks);
    // Configuration parsed without processing; its partial handle was closed
    // before prefetch configuration. The first playback frame opens once.
    const unsigned initial_opens = opens + 1;
    for (unsigned n = 0; n < 10; ++n) {
      engine.tick();
      auto benchmark = warm_status(r);
      assert(benchmark["result"]["frames"] == n + 1);
#ifdef MVB_JPEG_FIRST_BLOCK_WARMUP
      auto warm = benchmark["result"]["decoder_pipeline"];
      assert(warm["cached"] == true && warm["hits"] == n);
      assert(warm["attempts"] == n + 1 && warm["errors"] == 0 && warm["aborts"] == 0);
      assert(opens == initial_opens && allocations == blocks);
      assert(warm["blocks"] == blocks && warm["cached_blocks"] == blocks);
      assert(warm["cache_owned_bytes"] == (blocks == 2 ? 10240 : 0));
      // Diagnostics preserve partial output/input state across calls.
      auto pipeline = r.command("{\"method\":\"get_pipeline_profile\"}");
      assert(pipeline["result"]["decoder_pipeline"]["cached"] == true);
      auto stages = pipeline["result"]["average_us"];
      // Both cached and foreground codec CPU remain credited to the presented
      // frame, even though first-block work was performed in previous service.
      assert(stages["jpeg_open"].as<double>() >= 700);
      assert(stages["jpeg_decode"].as<double>() >= 1800);
      assert(benchmark["result"]["average_decode_us"].as<double>() >= 2500);
      assert(warm["work_us_nested"].as<uint64_t>() >= (n + 1) * (700 + blocks * 900));
      if (blocks == 2 && n)
        assert(stages["decoder_strip_copy_nested"].as<double>() > 0);
      assert(r.command("{\"method\":\"get_status\"}")["ok"] == true);
      assert(r.command("{\"method\":\"get_serial_diagnostics\"}")["ok"] == true);
      assert(r.command("{\"method\":\"upload_status\"}")["ok"] == true);
#else
      assert(benchmark["result"]["decoder_pipeline"]["cached"] == false);
#endif
      clock.advance(100000);
    }
#ifdef MVB_JPEG_FIRST_BLOCK_WARMUP
    assert(warm_pending == 10 && painter.source_changed == 10);
    assert(processes == 20 + blocks); // Exactly two blocks/frame plus future cache.
#else
    assert(warm_pending == 0 && painter.source_changed == 0 && processes == 20);
#endif
    assert(painter.begin_calls == painter.end_calls && !frame_open);
  }
  assert(!borrowed && opens == closes && allocations == frees);
}
void invalidation(const std::string &method, unsigned blocks = 1) {
  Clock clock; Storage storage(clock, video_bytes(), true); Runtime runtime(clock);
  OwnedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  select(engine, r, blocks); engine.tick();
  assert(warm_status(r)["result"]["decoder_pipeline"]["cached"] == true);
  unsigned before = closes;
  assert(r.command(method.c_str())["ok"] == true);
  assert(closes == before + 1 && !borrowed);
  assert(warm_status(r)["result"]["decoder_pipeline"]["cached"] == false);
  assert(!frame_open);
  engine.tick();
  assert(storage.original_bytes() == video_bytes());
}
void invalidations(unsigned blocks = 1) {
  for (const char *command : {
      "{\"method\":\"pause\"}", "{\"method\":\"stop\"}", "{\"method\":\"restart\"}",
      "{\"method\":\"seek\",\"position_us\":500000}",
      "{\"method\":\"set_position\",\"x\":60,\"y\":40}",
      "{\"method\":\"set_rotation\",\"rotation_deg\":90}",
      "{\"method\":\"set_scale\",\"scale\":2}",
      "{\"method\":\"set_fps\",\"numerator\":7,\"denominator\":1}",
      "{\"method\":\"probe_pipeline\",\"stage\":\"sd\",\"repetitions\":1}",
      "{\"method\":\"probe_pipeline\",\"stage\":\"decode\",\"repetitions\":1}",
      "{\"method\":\"set_prefetch_options\",\"enabled\":false}",
      "{\"method\":\"set_decoder_options\",\"decoder\":\"jpegdec\",\"max_mcus\":32,\"native_blocks\":true}",
      "{\"method\":\"set_decoder_pipeline\",\"warmup\":false}",
      "{\"method\":\"begin_upload\",\"sha256\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"size\":136,\"container_version\":1}"})
    invalidation(command, blocks);
}
void misses(unsigned blocks = 1) {
  Clock clock; Storage storage(clock, video_bytes(), true); Runtime runtime(clock);
  OwnedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  select(engine, r, blocks); engine.tick(); clock.advance(600000); engine.tick();
  auto b = warm_status(r); auto warm = b["result"]["decoder_pipeline"];
  assert(b["result"]["frames"] == 2 && warm["misses"] == 1 && warm["aborts"] == 1);
  assert(warm["hits"] == 0 && warm["errors"] == 0);
  assert(opens == 3 && allocations == blocks);
}
void failures(bool display, bool read, unsigned blocks = 1, bool second = false) {
  Clock clock; Storage storage(clock, video_bytes(), true); Runtime runtime(clock);
  OwnedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  select(engine, r, blocks); painter.fail_completion = display;
  fail_warm = !display && !read && !second;
  fail_warm_second = second;
  if (read) {
    auto &bytes = const_cast<std::vector<uint8_t> &>(storage.original_bytes());
    auto payload = jpeg_with_comment(40);
    size_t next = 96 + 16 + (payload.size() + 3) / 4 * 4;
    bytes[next + 16 + 3] ^= 1;
  }
  engine.tick();
  auto status = r.command("{\"method\":\"get_status\"}");
  auto b = warm_status(r);
  assert(status["result"]["state"] == "error");
  assert(b["result"]["frames"] == (display ? 0 : 1));
  assert(painter.begin_calls == painter.end_calls && !frame_open && !borrowed);
  assert(b["result"]["decoder_pipeline"]["errors"] == unsigned(!display && !read));
  assert(status["result"]["error"] == (display ? "display_failed" : read ? "crc_mismatch" : "unsupported_jpeg"));
}
void allocation_failure() {
  Clock clock; Storage storage(clock, video_bytes(), true); Runtime runtime(clock);
  OwnedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  select(engine, r); engine.tick();
  fail_cache_allocate = true;
  auto failed = r.command("{\"method\":\"set_decoder_pipeline\",\"warmup\":true,\"blocks\":2}");
  assert(failed["error"]["code"] == "insufficient_memory");
  auto benchmark = warm_status(r);
  auto status = benchmark["result"]["decoder_pipeline"];
  assert(status["selected"] == true && status["blocks"] == 1 && status["cache_owned_bytes"] == 0);
  assert(!borrowed && !frame_open);
  clock.advance(100000); engine.tick();
  assert(r.command("{\"method\":\"get_status\"}")["result"]["state"] == "playing");
  assert(warm_status(r)["result"]["frames"] == 2);
  fail_cache_allocate = false;
  auto selected = r.command("{\"method\":\"set_decoder_pipeline\",\"warmup\":true,\"blocks\":2}");
  assert(selected["ok"] == true && selected["result"]["cache_owned_bytes"] == 10240);
  const unsigned allocated = allocations;
  assert(r.command("{\"method\":\"set_decoder_pipeline\",\"warmup\":true,\"blocks\":2}")["ok"] == true);
  assert(allocations == allocated);
  assert(r.command("{\"method\":\"set_decoder_pipeline\",\"warmup\":false}")["result"]["cache_owned_bytes"] == 0);
  for (const char *bad : {"0", "3", "-1", "\"two\""}) {
    std::string command = "{\"method\":\"set_decoder_pipeline\",\"warmup\":true,\"blocks\":" + std::string(bad) + "}";
    assert(r.command(command.c_str())["error"]["code"] == "invalid_parameter");
  }
}
void without_two_guard() {
  assert(!mvb::NewJpeg::two_block_warmup_available());
  Clock clock; Storage storage(clock, video_bytes(), true); Runtime runtime(clock);
  OwnedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  select(engine, r); engine.tick();
  unsigned allocated = allocations;
  auto response = r.command("{\"method\":\"set_decoder_pipeline\",\"warmup\":true,\"blocks\":2}");
  assert(response["error"]["code"] == "unsupported_configuration");
  auto benchmark = warm_status(r); auto pipeline = benchmark["result"]["decoder_pipeline"];
  assert(pipeline["blocks"] == 1 && pipeline["cache_owned_bytes"] == 0);
  assert(allocations == allocated && pipeline["selected"] == true);
  clock.advance(100000); engine.tick();
  assert(r.command("{\"method\":\"get_status\"}")["result"]["state"] == "playing");
}
int main(int argc, char **argv) {
  assert(argc == 2); std::string mode = argv[1];
  if (mode == "le") successful_hits(false);
  else if (mode == "be") successful_hits(true);
  else if (mode == "invalidation") invalidations();
  else if (mode == "miss") misses();
  else if (mode == "display_failure") failures(true, false);
  else if (mode == "read_failure") failures(false, true);
  else if (mode == "warm_failure") failures(false, false);
  else if (mode == "two_le") successful_hits(false, 2);
  else if (mode == "two_be") successful_hits(true, 2);
  else if (mode == "two_invalidation") invalidations(2);
  else if (mode == "two_miss") misses(2);
  else if (mode == "two_display_failure") failures(true, false, 2);
  else if (mode == "two_warm_failure") failures(false, false, 2, true);
  else if (mode == "allocation_failure") allocation_failure();
  else if (mode == "without_two") without_two_guard();
  else return 2;
}
'''


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise AssertionError(f"Expected exactly one copied-header adaptation: {before}")
    return source.replace(before, after)


@unittest.skipUnless(CXX, "A C++ compiler is required")
class JpegWarmupEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dependency = ROOT / "firmware/.pio/libdeps/cyd_ili9341"
        if not (dependency / "JPEGDEC/src/JPEGDEC.cpp").is_file():
            raise unittest.SkipTest("Install pinned PlatformIO dependencies")
        cls.directory = tempfile.TemporaryDirectory(prefix="muse-warmup-engine-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        # Actual product engine; only codec's platform availability is adapted
        # in a temporary header. Host prefetch stays the actual std::thread path.
        (directory / "engine.hpp").write_text((ROOT / "firmware/include/engine.hpp").read_text())
        header = (ROOT / "firmware/include/new_jpeg.hpp").read_text()
        gate = ("#if defined(MVB_ENABLE_NEW_JPEG) && defined(ARDUINO_ARCH_ESP32) &&             "
                + chr(92) + "\n    defined(CONFIG_IDF_TARGET_ESP32)")
        header = replace_once(header, gate, "#if defined(MVB_ENABLE_NEW_JPEG)")
        header = replace_once(header, '"../vendor/esp_new_jpeg/include/esp_jpeg_dec.h"',
                              '"' + str(ROOT / "firmware/vendor/esp_new_jpeg/include/esp_jpeg_dec.h") + '"')
        (directory / "new_jpeg.hpp").write_text(header)
        # Reuse real container fixtures and deterministic engine adapters.
        for name in ("core", "profile"):
            adapter = (ROOT / f"firmware/native/{name}_tests.cpp").read_text()
            before, after = adapter.rsplit("}", 1)
            adapter = before + "return 0;\n}" + after
            if name == "profile":
                adapter = replace_once(adapter, "uint64_t time = 10000000;", "std::atomic<uint64_t> time{10000000};")
                adapter = replace_once(adapter, "size_t read(uint8_t *data, size_t count) override {", """size_t read(uint8_t *data, size_t count) override {
    const uintptr_t active = reinterpret_cast<uintptr_t>(borrowed.load());
    const uintptr_t dest = reinterpret_cast<uintptr_t>(data);
    assert(!active || dest + count <= active || dest >= active + borrowed_size.load());
""")
                adapter = "extern std::atomic<const uint8_t *> borrowed;\nextern std::atomic<size_t> borrowed_size;\n" + adapter
            (directory / f"{name}-adapters.hpp").write_text(adapter)
        (directory / "driver.cpp").write_text(SOURCE)
        cls.executables = {}
        for guard in (True, False, "one"):
            executable = directory / ("warm" if guard is True else "one" if guard == "one" else "plain")
            command = [CXX, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-pthread",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-DMVB_ENABLE_NEW_JPEG",
                       "-DMVB_FRAME_PREFETCH", "-DMVB_FRAME_RECORD_READ", "-D__LINUX__", "-DNO_SIMD",
                       "-include", str(ROOT / "firmware/include/jpeg_portable.hpp"),
                       "-I", str(directory), "-I", str(ROOT / "firmware/include"),
                       "-I", str(dependency / "ArduinoJson/src"), "-I", str(dependency / "JPEGDEC/src"),
                       str(directory / "driver.cpp"), str(dependency / "JPEGDEC/src/JPEGDEC.cpp"),
                       "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-sign-compare", "-Wno-type-limits",
                       "-o", str(executable)]
            if guard:
                command.append("-DMVB_JPEG_FIRST_BLOCK_WARMUP")
            if guard is True:
                command.append("-DMVB_JPEG_TWO_BLOCK_WARMUP")
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.executables[guard] = executable

    def scenario(self, mode, guard=True):
        result = subprocess.run([str(self.executables[guard]), mode], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_owned_pending_dma_strip_survives_warmup_and_exact_cached_blocks(self):
        self.scenario("le")
        self.scenario("be")

    def test_controls_probes_upload_and_prefetch_invalidate_before_reuse(self):
        self.scenario("invalidation")

    def test_timeline_miss_closes_before_compressed_slot_overwrite(self):
        self.scenario("miss")

    def test_failed_display_completion_never_counts(self):
        self.scenario("display_failure")

    def test_future_read_failure_preserves_completed_frame_count(self):
        self.scenario("read_failure")

    def test_future_warm_decode_failure_preserves_completed_frame_count(self):
        self.scenario("warm_failure")

    def test_disabled_guard_keeps_display_synchronous_without_lookahead(self):
        self.scenario("le", guard=False)

    def test_two_cached_blocks_keep_pending_dma_owned_and_exact_output_with_cpu_credit(self):
        self.scenario("two_le")
        self.scenario("two_be")

    def test_two_blocks_controls_probes_upload_and_prediction_miss_abort(self):
        self.scenario("two_invalidation")
        self.scenario("two_miss")

    def test_two_blocks_display_and_second_warm_failure_keep_correct_counts(self):
        self.scenario("two_display_failure")
        self.scenario("two_warm_failure")

    def test_two_blocks_allocation_failure_preserves_one_block_and_disable_releases_cache(self):
        self.scenario("allocation_failure")

    def test_production_one_block_build_rejects_two_without_allocating_or_breaking_playback(self):
        self.scenario("le", guard="one")
        self.scenario("without_two", guard="one")


if __name__ == "__main__":
    unittest.main()
