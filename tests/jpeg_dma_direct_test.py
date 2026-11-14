"""Actual TftPainter + NewJPEG lease adapter against delayed host peripherals.

SPI, codec bytes and time are substituted. These are ownership/pixel oracles;
physical SDK pointer switching, frame speed and image stability need hardware.
"""
import configparser
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from new_jpeg_test import CXX, ROOT, SOURCE as CODEC_SOURCE

SOURCE = CODEC_SOURCE[:CODEC_SOURCE.index("void reset() {")] + "#endif\n"
SOURCE += '#include "dma_direct_fake_sdk.hpp"\n'
MAIN = r'''
mvb::RenderOptions options(bool direct = true) {
  mvb::RenderOptions opt;
  opt.mode = mvb::RenderMode::Dma;
  opt.big_endian = true;
  opt.zero_copy = direct;
  opt.spi_hz = 40000000;
  opt.command_spi_hz = 20000000;
  return opt;
}
JsonDocument info(TftPainter &p) {
  JsonDocument doc; p.info(doc.to<JsonObject>()); return doc;
}
void reset_gram() {
  std::fill(gram.begin(), gram.end(), 0);
  std::fill(pixel_writes.begin(), pixel_writes.end(), 0);
}
void check_gram(int w, int h, const mvb::Transform &t, unsigned seed,
                unsigned writes = 1) {
  int rw = t.rotation % 180 ? h : w, rh = t.rotation % 180 ? w : h;
  for (int y = 0; y < 240; ++y)
    for (int x = 0; x < 320; ++x) {
      const int dx = x - t.x, dy = y - t.y;
      bool inside = dx >= 0 && dy >= 0 && dx < t.width && dy < t.height;
      uint16_t expected = 0;
      if (inside) {
        int u = (2 * dx + 1) * rw / (2 * t.width);
        int v = (2 * dy + 1) * rh / (2 * t.height), sx = u, sy = v;
        if (t.rotation == 90) { sx = v; sy = h - 1 - u; }
        if (t.rotation == 180) { sx = w - 1 - u; sy = h - 1 - v; }
        if (t.rotation == 270) { sx = w - 1 - v; sy = u; }
        expected = color(sx, sy) + seed;
      }
      assert(gram[y * 320 + x] == expected);
      assert(pixel_writes[y * 320 + x] == (inside ? writes : 0));
    }
}
void cleanup(TftPainter &p) {
  mvb::RenderOptions polling; assert(p.configure(polling));
  assert(dma_allocations.empty() && pending_transfers.empty());
  assert(!transaction_open && !fake_bus_owned);
}
void frames(bool direct, bool warm) {
  for (int width : {8, 304, 320})
    for (int height : {8, 24, 224, 240}) {
      TftPainter p; p.begin(); assert(p.configure(options(direct)));
      reset_gram(); Clock clock; mvb::TileRenderer renderer(p);
      renderer.source_width = width; renderer.source_height = height;
      renderer.transform = {(320-width)/2, (240-height)/2, width, height, 0, false};
      assert(renderer.direct_output_eligible() == direct);
      {
        mvb::NewJpeg decoder;
        std::vector<uint8_t> current = fixture(width, height, 1);
        assert(decoder.prepare(current.data(), current.size(), renderer, clock, true, true));
        // Header parsing acquires no output lease; it runs before begin_image.
        assert(info(p)["zero_copy_active_leases"] == 0);
        for (unsigned frame = 0; frame < 100; ++frame) {
          p.begin_image(mvb::clipped(renderer.transform));
          if (frame && warm) {
            assert(decoder.resume_first_block(current.data(), current.size(), renderer, clock, true));
          } else if (frame) {
            assert(decoder.prepare(current.data(), current.size(), renderer, clock, true, true));
          }
          assert(decoder.decode());
          assert(!pending_transfers.empty());
          auto next = fixture(width, height, uint8_t(frame + 2));
          if (warm) {
            assert(decoder.warm_first_block(next.data(), next.size(), renderer, clock, true));
            assert(decoder.cached_blocks() == 1);
          }
          assert(p.end_frame());
          check_gram(width, height, renderer.transform, frame + 1, frame + 1);
          const auto diagnostic = info(p);
          assert(diagnostic["zero_copy_active_leases"] == (direct && warm ? 1 : 0));
          assert(diagnostic["dma_pending"] == 0 && diagnostic["dma_ownership_errors"] == 0);
          assert(diagnostic["dma_buffers_aligned"] == true);
          assert(diagnostic["dma_buffer_slots"] == (direct ? 3 : 2));
          assert(decoder.output_capacity() == (direct ? 0 : size_t(width) * 16 * 2));
          assert(diagnostic["zero_copy_direct_submissions"].as<unsigned>() ==
                 (direct ? (frame + 1) * unsigned((height + 15) / 16) : 0));
          assert(diagnostic["copied_dma_submissions"].as<unsigned>() ==
                 (direct ? 0 : (frame + 1) * unsigned((height + 15) / 16)));
          assert(diagnostic["dma_submissions"] == diagnostic["dma_completions"]);
          current = std::move(next); // Completed current input can be recycled.
        }
        if (warm) {
          if (direct)
            assert(!p.begin_integrity_check()); // Warm lease must first be aborted.
          assert((!p.configure(options(false))) == direct);
          assert(decoder.discard_warmup());
        }
        assert(info(p)["zero_copy_active_leases"] == 0);
      }
      cleanup(p);
    }
}
void integrity() {
  TftPainter p; p.begin(); assert(p.configure(options()));
  Clock clock; mvb::TileRenderer renderer(p);
  renderer.source_width = 320; renderer.source_height = 240;
  renderer.transform = {0, 0, 320, 240, 0, false};
  auto bytes = fixture(320, 240, 31);
  mvb::NewJpeg decoder;
  assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, true));
  p.begin_image({0, 0, 320, 240}); assert(decoder.decode() && p.end_frame());
  const auto before = gram; const auto write_counts = pixel_writes;
  assert(p.begin_integrity_check() && !renderer.direct_output_eligible());
  assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, true));
  assert(decoder.output_capacity() == 10240); // Independent reference pixels.
  p.begin_image({0, 0, 320, 240}); assert(decoder.decode() && p.end_frame());
  auto result = p.end_integrity_check();
  assert(result.available && result.pixels_checked == 76800 && !result.pixels_mismatched);
  assert(gram == before && pixel_writes == write_counts);
  // Returning from the paused probe frees the owned strip once, then leases.
  assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, true));
  assert(decoder.output_capacity() == 0);
  p.begin_image({0, 0, 320, 240}); assert(decoder.decode() && p.end_frame());
  cleanup(p);
}
void fallback() {
  for (int variant = 0; variant < 6; ++variant) {
    TftPainter p; p.begin(); auto opt = options();
    if (variant == 5) { opt.zero_copy = false; opt.big_endian = false; }
    assert(p.configure(opt)); reset_gram();
    Clock clock; mvb::TileRenderer renderer(p);
    renderer.source_width = renderer.source_height = 24;
    renderer.transform = {20, 30, 24, 24, 0, false};
    if (variant == 0) renderer.transform.width = 48;
    if (variant == 1) renderer.transform.height = 48;
    if (variant == 2) renderer.transform.rotation = 90;
    if (variant == 3) renderer.transform.x = -5;
    if (variant == 4) renderer.native_blocks = false;
    assert(!renderer.direct_output_eligible());
    auto bytes = fixture(24, 24, 19); mvb::NewJpeg decoder;
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, opt.big_endian));
    p.begin_image(mvb::clipped(renderer.transform)); assert(decoder.decode() && p.end_frame());
    check_gram(24, 24, renderer.transform, 19);
    assert(info(p)["zero_copy_direct_submissions"] == 0);
    assert(info(p)["copied_dma_submissions"].as<unsigned>() > 0);
    cleanup(p);
  }
}
void failures() {
  for (int allocation = 0; allocation < 3; ++allocation) {
    TftPainter p; p.begin(); dma_allocation_calls = 0; fail_allocation_at = allocation;
    assert(!p.configure(options()));
    assert(dma_allocations.empty() && pending_transfers.empty() && !fake_bus_owned);
  }
  fail_allocation_at = -1;
  { TftPainter p; p.begin(); free_heap = 60000;
    assert(!p.configure(options())); free_heap = 200000;
    assert(dma_allocations.empty()); }
  for (bool queue_failure : {false, true}) {
    TftPainter p; p.begin(); assert(p.configure(options()));
    Clock clock; mvb::TileRenderer renderer(p);
    renderer.source_width = renderer.source_height = 24;
    renderer.transform = {0, 0, 24, 24, 0, false};
    auto bytes = fixture(24, 24); mvb::NewJpeg decoder;
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, true));
    p.begin_image({0, 0, 24, 24});
    if (queue_failure) fail_queue = true; else faults.process_error = 0;
    assert(!decoder.decode());
    fail_queue = false; faults = {};
    const bool completed = p.end_frame();
    assert(completed == !queue_failure);
    assert(info(p)["zero_copy_active_leases"] == 0);
    cleanup(p);
  }
  // Wrong token never releases the legitimate warmed strip.
  TftPainter p; p.begin(); assert(p.configure(options()));
  mvb::PixelLease lease; assert(p.acquire_output_lease(5120, lease));
  auto stale = lease; ++stale.token;
  assert(!p.release_output_lease(stale));
  assert(info(p)["zero_copy_active_leases"] == 1);
  assert(!p.configure(options(false)));
  assert(p.release_output_lease(lease) && !lease);
  assert(info(p)["dma_ownership_errors"].as<unsigned>() == 1);
  uint16_t pixel = 0; assert(!p.block(0, 0, &pixel, 1, 1, 1));
  cleanup(p);
}
class InvalidLeasePainter : public TftPainter {
  mvb::PixelLease original_{};
  bool small_;
public:
  explicit InvalidLeasePainter(bool small) : small_(small) {}
  bool acquire_output_lease(size_t pixels, mvb::PixelLease &lease) override {
    if (!TftPainter::acquire_output_lease(pixels, lease)) return false;
    original_ = lease;
    if (small_) lease.capacity_pixels = pixels - 1;
    else ++lease.pixels;
    return true;
  }
  bool release_output_lease(mvb::PixelLease &lease) override {
    assert(lease.token == original_.token && lease.slot == original_.slot);
    lease = original_;
    return TftPainter::release_output_lease(lease);
  }
};
void invalid_alignment() {
  for (bool small : {false, true}) {
    InvalidLeasePainter p(small); p.begin(); assert(p.configure(options()));
    Clock clock; mvb::TileRenderer renderer(p);
    renderer.source_width = renderer.source_height = 24;
    renderer.transform = {0, 0, 24, 24, 0, false};
    auto bytes = fixture(24, 24); mvb::NewJpeg decoder;
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, true));
    const unsigned before = processes;
    p.begin_image({0, 0, 24, 24}); assert(!decoder.decode() && p.end_frame());
    assert(processes == before); // Reject before the binary codec can write.
    assert(info(p)["zero_copy_active_leases"] == 0);
    cleanup(p);
  }
}
void unknown_completion() {
  TftPainter p; p.begin(); assert(p.configure(options()));
  Clock clock; mvb::TileRenderer renderer(p);
  renderer.source_width = 320; renderer.source_height = 240;
  renderer.transform = {0, 0, 320, 240, 0, false};
  auto bytes = fixture(320, 240); mvb::NewJpeg decoder;
  assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, true));
  p.begin_image({0, 0, 320, 240}); fail_completion = true;
  assert(!decoder.decode() && !p.end_frame());
  const auto state = info(p);
  assert(state["dma_completion_unconfirmed_reset_required"] == true);
  assert(state["dma_pending"] == 2 && state["zero_copy_active_leases"] == 0);
  assert(dma_allocations.size() == 3 && pending_transfers.size() == 2);
  mvb::PixelLease lease; assert(!p.acquire_output_lease(5120, lease));
  assert(!p.configure(options(false)) && !p.begin_integrity_check());
  const auto before = gram;
  p.clear({0, 0, 320, 240}); p.waiting(true, true, nullptr);
  assert(gram == before && dma_allocations.size() == 3);
  // Simulate an explicit peripheral/system reset outside the product adapter.
  // The product must retain these buffers until a genuine reset, not guess.
  pending_transfers.clear(); transaction_open = false; fake_bus_owned = false;
  fail_completion = false;
  while (!dma_allocations.empty()) heap_caps_free(dma_allocations.begin()->first);
}
void disabled() {
  TftPainter p; p.begin(); assert(!mvb::NewJpeg::direct_output_available());
  assert(!p.configure(options())); assert(dma_allocations.empty());
  mvb::PixelLease lease; assert(!p.acquire_output_lease(5120, lease));
  frames(false, true);
}
int main(int argc, char **argv) {
  assert(argc == 2); std::string scenario = argv[1];
  if (scenario == "direct") frames(true, false);
  else if (scenario == "warm") frames(true, true);
  else if (scenario == "copy") frames(false, true);
  else if (scenario == "integrity") integrity();
  else if (scenario == "fallback") fallback();
  else if (scenario == "failures") failures();
  else if (scenario == "alignment") invalid_alignment();
  else if (scenario == "unknown") unknown_completion();
  else if (scenario == "disabled") disabled();
  else return 2;
}
'''


class ExperimentalEnvironmentTests(unittest.TestCase):
    def test_production_selects_qualified_direct_but_research_stays_controlled(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "firmware/platformio.ini")
        self.assertEqual(config["env:cyd_jpeg_dma_direct"]["extends"], "env:cyd_jpeg_warmup")
        self.assertIn("-DMVB_JPEG_DMA_DIRECT_OUTPUT",
                      config["env:cyd_jpeg_dma_direct"]["build_flags"])
        self.assertIn("-DMVB_JPEG_DMA_DIRECT_OUTPUT", config["env:cyd_optimized"]["build_flags"])
        self.assertIn("-DMVB_QUALIFIED_FAST_DEFAULTS", config["env:cyd_optimized"]["build_flags"])
        self.assertNotIn("MVB_QUALIFIED_FAST_DEFAULTS", config["env:cyd_jpeg_dma_direct"]["build_flags"])
        self.assertIn("bool zero_copy = false;", (ROOT / "firmware/include/profiling.hpp").read_text())


@unittest.skipUnless(CXX, "A C++ compiler is required")
class DirectDmaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dependency = ROOT / "firmware/.pio/libdeps/cyd_ili9341/ArduinoJson/src"
        if not dependency.is_dir():
            raise unittest.SkipTest("Install pinned PlatformIO dependencies")
        cls.directory = tempfile.TemporaryDirectory(prefix="muse-jpeg-dma-direct-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        (directory / "sdkconfig.h").write_text("#define CONFIG_IDF_TARGET_ESP32 1\n")
        source = (ROOT / "firmware/src/main.cpp").read_text()
        painter = source[source.index("class TftPainter :"):source.index("class EspRuntime :")]
        # The real class is copied unchanged; only surrounding ESP SDK objects
        # and jpeg_dec_* functions are replaced with delayed host substitutes.
        (directory / "driver.cpp").write_text(SOURCE + painter + MAIN)
        cls.executables = {}
        for guard in (True, False):
            executable = directory / ("direct" if guard else "ordinary")
            command = [CXX, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                       "-DARDUINO_ARCH_ESP32", "-DMVB_ENABLE_NEW_JPEG",
                       "-DMVB_JPEG_FIRST_BLOCK_WARMUP", "-DMVB_DMA_QUEUE_DEPTH=2",
                       "-I", str(directory), "-I", str(ROOT / "firmware/include"),
                       "-I", str(ROOT / "tests/fixtures"), "-I", str(dependency),
                       str(directory / "driver.cpp"), "-o", str(executable)]
            if guard:
                command.append("-DMVB_JPEG_DMA_DIRECT_OUTPUT")
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.executables[guard] = executable

    def scenario(self, name, guard=True):
        result = subprocess.run([str(self.executables[guard]), name],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_direct_buffer_and_descriptor_immutability_for_1200_frames(self):
        self.scenario("direct")

    def test_third_future_lease_survives_join_and_cached_strip_is_submitted_once(self):
        self.scenario("warm")

    def test_unselected_three_slot_build_keeps_two_slot_copy_and_warmup(self):
        self.scenario("copy")

    def test_integrity_uses_independent_output_and_never_overwrites_expected_pixels(self):
        self.scenario("integrity")

    def test_clipping_scaling_rotation_endianness_and_disabled_native_blocks_fall_back(self):
        self.scenario("fallback")

    def test_allocation_codec_queue_and_stale_token_failures_keep_ownership(self):
        self.scenario("failures")

    def test_disabled_guard_rejects_selection_and_keeps_qualified_copy_path(self):
        self.scenario("disabled", guard=False)

    def test_invalid_capacity_or_alignment_fails_before_codec_writes(self):
        self.scenario("alignment")

    def test_unconfirmed_completion_retains_all_buffers_and_requires_reset(self):
        self.scenario("unknown")


if __name__ == "__main__":
    unittest.main()
