"""Sanitizer checks for packed ROM JPEG strips; no hardware port is opened."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")

SOURCE = r"""
#include "rom_jpeg.hpp"
#include <cassert>
#include <cstdio>
#include <memory>
#include <string>
#include <vector>

class Clock : public mvb::ProfileClock {
public:
  uint64_t ticks = 0;
  uint64_t now() override { return ticks++; }
};

class Canvas : public mvb::Painter {
public:
  static constexpr uint16_t untouched = 0xd15a;
  Clock &clock;
  std::vector<uint16_t> pixels = std::vector<uint16_t>(320 * 240, untouched);
  std::vector<unsigned> writes = std::vector<unsigned>(320 * 240);
  unsigned blocks = 0, rows = 0, failures = 0;
  bool reject = false;
  explicit Canvas(Clock &c) : clock(c) {}
  bool row(int x, int y, const uint16_t *p, int n) override {
    ++rows;
    clock.ticks += 7;
    if (reject) { ++failures; return false; }
    assert(p && x >= 0 && y >= 0 && n > 0 && x + n <= 320 && y < 240);
    for (int i = 0; i < n; ++i) {
      pixels[y * 320 + x + i] = p[i];
      ++writes[y * 320 + x + i];
    }
    return true;
  }
  bool block(int x, int y, const uint16_t *p, int w, int h, int stride) override {
    ++blocks;
    clock.ticks += 11;
    if (reject) { ++failures; return false; }
    assert(p && x >= 0 && y >= 0 && w > 0 && h > 0 && stride >= w);
    assert(x + w <= 320 && y + h <= 240);
    for (int row = 0; row < h; ++row)
      for (int column = 0; column < w; ++column) {
        size_t index = (y + row) * 320 + x + column;
        pixels[index] = p[row * stride + column];
        ++writes[index];
      }
    return true;
  }
  void clear(mvb::Rect) override { assert(false); }
  void waiting(bool, bool, const char *) override { assert(false); }
};

uint8_t red(int x, int y) { return uint8_t(x * 37 + y * 13); }
uint8_t green(int x, int y) { return uint8_t(x * 11 + y * 71 + 83); }
uint8_t blue(int x, int y) { return uint8_t(x * 73 + y * 19 + 197); }
uint16_t expected_color(int x, int y, bool swapped) {
  uint16_t color = uint16_t(((red(x, y) >> 3) << 11) |
                            ((green(x, y) >> 2) << 5) | (blue(x, y) >> 3));
  return swapped ? uint16_t((color & 255) * 256 + color / 256) : color;
}

// An exactly sized allocation with an unaligned beginning catches use of the
// nominal MCU width instead of the packed right-edge rectangle width.
bool submit(mvb::Rgb888StripCollector &collector, int left, int top,
            int width, int height) {
  const size_t offset = 7;
  const size_t length = size_t(width) * height * 3;
  std::unique_ptr<uint8_t[]> bytes(new uint8_t[offset + length]);
  uint8_t *rgb = bytes.get() + offset;
  for (int y = 0; y < height; ++y)
    for (int x = 0; x < width; ++x) {
      const size_t at = (size_t(y) * width + x) * 3;
      rgb[at] = red(left + x, top + y);
      rgb[at + 1] = green(left + x, top + y);
      rgb[at + 2] = blue(left + x, top + y);
    }
  bool ok = collector.accept(unsigned(left), unsigned(left + width - 1),
                              unsigned(top), unsigned(top + height - 1), rgb);
  std::fill(rgb, rgb + length, 0); // ROM reuses callback memory immediately.
  return ok;
}

unsigned feed(mvb::Rgb888StripCollector &collector, int width, int height,
              int mcu_width, int mcu_height) {
  unsigned count = 0;
  for (int y = 0; y < height; y += mcu_height)
    for (int x = 0; x < width; x += mcu_width) {
      assert(submit(collector, x, y, std::min(mcu_width, width - x),
                    std::min(mcu_height, height - y)));
      ++count;
    }
  assert(collector.complete() && !collector.failed());
  return count;
}

void oracle(const Canvas &canvas, int width, int height,
            const mvb::Transform &t, bool swapped) {
  const bool turned = t.rotation == 90 || t.rotation == 270;
  const int rotated_width = turned ? height : width;
  const int rotated_height = turned ? width : height;
  for (int y = 0; y < 240; ++y)
    for (int x = 0; x < 320; ++x) {
      const int dx = x - t.x, dy = y - t.y;
      const bool inside = dx >= 0 && dx < t.width && dy >= 0 && dy < t.height;
      uint16_t color = Canvas::untouched;
      if (inside) {
        const int u = ((2 * dx + 1) * rotated_width) / (2 * t.width);
        const int v = ((2 * dy + 1) * rotated_height) / (2 * t.height);
        int sx = u, sy = v;
        if (t.rotation == 90) { sx = v; sy = height - 1 - u; }
        if (t.rotation == 180) { sx = width - 1 - u; sy = height - 1 - v; }
        if (t.rotation == 270) { sx = width - 1 - v; sy = u; }
        assert(sx >= 0 && sx < width && sy >= 0 && sy < height);
        color = expected_color(sx, sy, swapped);
      }
      const size_t index = y * 320 + x;
      assert(canvas.pixels[index] == color);
      assert(canvas.writes[index] == unsigned(inside));
    }
}

void native_and_edges() {
  for (bool swapped : {false, true})
    for (int width : {1, 17, 317, 320})
      for (int height : {1, 15, 239, 240})
        for (int mcu_height : {8, 16}) {
          Clock clock;
          Canvas canvas(clock);
          mvb::TileRenderer renderer(canvas);
          renderer.source_width = width;
          renderer.source_height = height;
          renderer.transform = {0, 0, width, height, 0, false};
          mvb::Rgb888StripCollector collector;
          assert(collector.begin(renderer, clock, width, height, true, swapped));
          assert(!collector.complete() && !collector.failed());
          const unsigned count = feed(collector, width, height, 16, mcu_height);
          assert(canvas.rows == 0);
          assert(canvas.blocks == unsigned((height + mcu_height - 1) / mcu_height));
          assert(collector.color_us() == count);
          assert(collector.draw_us() == 12 * canvas.blocks);
          oracle(canvas, width, height, renderer.transform, swapped);
        }
  Clock clock;
  Canvas canvas(clock);
  mvb::TileRenderer renderer(canvas);
  renderer.source_width = 18;
  renderer.source_height = 10;
  renderer.transform = {0, 0, 18, 10, 0, false};
  mvb::Rgb888StripCollector collector;
  assert(collector.begin(renderer, clock, 18, 10, true, false));
  assert(submit(collector, 0, 0, 16, 8));
  assert(!collector.complete() && canvas.blocks == 0 && !collector.failed());
  assert(submit(collector, 16, 0, 2, 8));
  assert(!collector.complete() && canvas.blocks == 1);
  assert(submit(collector, 0, 8, 16, 2));
  assert(!collector.complete() && canvas.blocks == 1);
  assert(submit(collector, 16, 8, 2, 2));
  assert(collector.complete() && canvas.blocks == 2);
  oracle(canvas, 18, 10, renderer.transform, false);
}

void transforms() {
  const int width = 18, height = 10;
  for (int rotation : {0, 90, 180, 270})
    for (int w : {1, 7, 18, 36, 640})
      for (int h : {1, 5, 10, 20, 480})
        for (int x : {-640, -5, 11, 319, 640})
          for (int y : {-480, -3, 7, 239, 480}) {
            Clock clock;
            Canvas canvas(clock);
            mvb::TileRenderer renderer(canvas);
            renderer.source_width = width;
            renderer.source_height = height;
            renderer.transform = {x, y, w, h, rotation, false};
            mvb::Rgb888StripCollector collector;
            const bool swapped = (w + h + rotation) % 2;
            assert(collector.begin(renderer, clock, width, height, true, swapped));
            feed(collector, width, height, 16, 8);
            oracle(canvas, width, height, renderer.transform, swapped);
          }
}

void invalid_inputs() {
  Clock clock;
  Canvas canvas(clock);
  mvb::TileRenderer renderer(canvas);
  renderer.source_width = 18;
  renderer.source_height = 10;
  renderer.transform = {0, 0, 18, 10, 0, false};
  mvb::Rgb888StripCollector collector;
  uint8_t pixel[3]{};
  assert(!collector.complete());
  assert(!collector.accept(0, 0, 0, 0, pixel));
  assert(collector.failed() && !collector.complete());
  for (unsigned width : {0u, 321u, ~0u}) {
    assert(!collector.begin(renderer, clock, width, 10, true, false));
    assert(collector.failed() && !collector.complete());
    assert(!collector.accept(0, 0, 0, 0, pixel));
  }
  for (unsigned height : {0u, 241u, ~0u}) {
    assert(!collector.begin(renderer, clock, 18, height, true, false));
    assert(collector.failed() && !collector.complete());
  }
  const unsigned bad[][4] = {
    {1, 0, 0, 0}, {0, 0, 1, 0}, {0, 18, 0, 0}, {0, 0, 0, 10},
    {0, 0, 1, 1}, {1, 1, 0, 0}, {~0u, ~0u, 0, 0}, {0, 0, ~0u, ~0u}
  };
  for (const auto &r : bad) {
    assert(collector.begin(renderer, clock, 18, 10, true, false));
    assert(!collector.accept(r[0], r[1], r[2], r[3], pixel));
    assert(collector.failed() && !collector.complete());
    assert(collector.color_us() == 0 && collector.draw_us() == 0);
  }
  assert(collector.begin(renderer, clock, 18, 10, true, false));
  assert(!collector.accept(0, 0, 0, 0, nullptr));
  assert(collector.failed());
  assert(collector.begin(renderer, clock, 18, 240, true, false));
  assert(!collector.accept(0, 0, 0, 16, pixel));
  assert(collector.failed());
  assert(collector.begin(renderer, clock, 18, 10, true, false));
  assert(submit(collector, 0, 0, 16, 8));
  assert(!collector.accept(16, 17, 0, 6, pixel)); // inconsistent MCU row height
  assert(collector.failed() && !collector.complete());
  assert(collector.begin(renderer, clock, 18, 10, true, false));
  assert(submit(collector, 0, 0, 16, 8));
  assert(!collector.accept(0, 15, 0, 7, pixel)); // duplicate rectangle
  assert(collector.failed() && !collector.complete());
  assert(canvas.blocks == 0 && canvas.rows == 0);
}

void draw_failures() {
  for (bool native : {false, true}) {
    Clock clock;
    Canvas canvas(clock);
    canvas.reject = true;
    mvb::TileRenderer renderer(canvas);
    renderer.native_blocks = native;
    renderer.source_width = 18;
    renderer.source_height = 10;
    renderer.transform = {0, 0, 18, 10, 0, false};
    mvb::Rgb888StripCollector collector;
    assert(collector.begin(renderer, clock, 18, 10, true, false));
    assert(submit(collector, 0, 0, 16, 8));
    assert(!submit(collector, 16, 0, 2, 8));
    assert(collector.failed() && !collector.complete());
    assert(canvas.failures == 1 && collector.draw_us() > 0);
    const unsigned attempted = canvas.blocks + canvas.rows;
    const uint64_t colors = collector.color_us();
    assert(!submit(collector, 0, 8, 16, 2));
    assert(canvas.blocks + canvas.rows == attempted && collector.color_us() == colors);
    for (unsigned written : canvas.writes) assert(written == 0);
  }
  Clock clock;
  Canvas canvas(clock);
  mvb::TileRenderer renderer(canvas); // invalid source dimensions make drawing fail
  mvb::Rgb888StripCollector collector;
  assert(collector.begin(renderer, clock, 1, 1, true, false));
  assert(!submit(collector, 0, 0, 1, 1));
  assert(collector.failed() && !collector.complete());
  assert(canvas.blocks == 0 && canvas.rows == 0);
}

void no_draw_and_reuse() {
  Clock clock;
  Canvas canvas(clock);
  canvas.reject = true;
  mvb::TileRenderer renderer(canvas);
  renderer.source_width = 317;
  renderer.source_height = 239;
  mvb::Rgb888StripCollector collector;
  assert(collector.begin(renderer, clock, 317, 239, false, false));
  unsigned count = feed(collector, 317, 239, 16, 16);
  assert(collector.color_us() == count && collector.draw_us() == 0);
  assert(canvas.blocks == 0 && canvas.rows == 0 && canvas.failures == 0);
  for (unsigned written : canvas.writes) assert(written == 0);
  assert(!submit(collector, 0, 0, 1, 1)); // extra data after a complete image
  assert(collector.failed() && !collector.complete());
  assert(collector.begin(renderer, clock, 18, 10, false, true));
  assert(collector.color_us() == 0 && collector.draw_us() == 0);
  assert(submit(collector, 0, 0, 16, 8));
  assert(!collector.complete() && !collector.failed());
  assert(collector.begin(renderer, clock, 1, 1, false, false));
  assert(collector.color_us() == 0 && collector.draw_us() == 0);
  feed(collector, 1, 1, 8, 8);
  assert(collector.color_us() == 1 && collector.draw_us() == 0);
}

void host_adapter() {
  static_assert(!mvb::RomJpeg::available(), "Host must not claim ESP32 ROM support");
  mvb::RomJpeg decoder;
  Clock clock;
  Canvas canvas(clock);
  mvb::TileRenderer renderer(canvas);
  uint8_t bytes[] = {0xff, 0xd8, 0xff, 0xd9};
  assert(!decoder.prepare(bytes, sizeof(bytes), renderer, clock, true, false));
  assert(!decoder.prepare(nullptr, 0, renderer, clock, false, false));
  assert(!decoder.decode());
  assert(decoder.draw_us() == 0 && decoder.color_us() == 0);
  assert(canvas.blocks == 0 && canvas.rows == 0);
}

int main(int argc, char **argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  if (scenario == "native") native_and_edges();
  else if (scenario == "transforms") transforms();
  else if (scenario == "invalid") invalid_inputs();
  else if (scenario == "failures") draw_failures();
  else if (scenario == "reuse") no_draw_and_reuse();
  else if (scenario == "host") host_adapter();
  else return 2;
  std::puts("PASS: portable RGB888 strip behavior; no hardware timing claim.");
}
"""


@unittest.skipUnless(CXX, "A C++ compiler is required")
class RomJpegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="muse-rom-jpeg-tests-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = directory / "strips.cpp"
        source.write_text(SOURCE)
        cls.executable = directory / "strips"
        command = [CXX, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                   "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                   "-I", str(ROOT / "firmware/include"), str(source),
                   "-o", str(cls.executable)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def scenario(self, name):
        result = subprocess.run([str(self.executable), name], capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_strips_packed_odd_edges_and_both_byte_orders(self):
        self.scenario("native")

    def test_transform_pixels_and_clipping_against_independent_oracle(self):
        self.scenario("transforms")

    def test_bad_dimensions_order_bounds_and_partial_rows_fail_closed(self):
        self.scenario("invalid")

    def test_row_and_block_draw_failures_are_sticky_and_incomplete(self):
        self.scenario("failures")

    def test_decode_only_and_reuse_reset_state_without_drawing(self):
        self.scenario("reuse")

    def test_host_adapter_reports_rom_unavailable(self):
        self.scenario("host")


class RomAbiTests(unittest.TestCase):
    def test_classic_esp32_sdk_abi_and_direct_rom_symbols(self):
        packages = Path.home() / ".platformio/packages"
        compiler = Path(os.environ.get("MVB_XTENSA_CXX", str(
            packages / "toolchain-xtensa-esp-elf/bin/xtensa-esp32-elf-g++")))
        sdk = Path(os.environ.get("MVB_ESP32_SDK", str(
            packages / "framework-arduinoespressif32-esp32-libs")))
        if not compiler.is_file() or not (sdk / "qio_qspi/include/sdkconfig.h").is_file():
            self.skipTest("Install the project's classic ESP32 SDK/toolchain for the ABI check")
        source = r'''
#include "rom_jpeg.hpp"
#include <type_traits>
static_assert(mvb::RomJpeg::available(), "classic ESP32 ROM unavailable");
static_assert(sizeof(JRECT) == 8, "ROM rectangle ABI");
static_assert(sizeof(LONG) == 4 && sizeof(UINT) == 4 && sizeof(void*) == 4,
              "ROM word ABI");
static_assert(std::is_same<decltype(&jd_prepare),
  JRESULT (*)(JDEC*, UINT (*)(JDEC*, BYTE*, UINT), void*, UINT, void*)>::value,
  "prepare ABI");
static_assert(std::is_same<decltype(&jd_decomp),
  JRESULT (*)(JDEC*, UINT (*)(JDEC*, void*, JRECT*), BYTE)>::value, "decomp ABI");
bool run(mvb::RomJpeg &decoder, const uint8_t *bytes, size_t n,
         mvb::TileRenderer &renderer, mvb::ProfileClock &clock) {
  return decoder.prepare(bytes, n, renderer, clock, true, false) && decoder.decode();
}
'''
        with tempfile.TemporaryDirectory(prefix="muse-rom-abi-") as directory:
            directory = Path(directory)
            cpp = directory / "abi.cpp"
            cpp.write_text(source)
            obj = directory / "abi.o"
            command = [str(compiler), "-std=gnu++17", "-O3", "-Wall", "-Wextra",
                       "-Werror", "-fno-builtin-memcpy", "-DARDUINO_ARCH_ESP32",
                       "-I", str(ROOT / "firmware/include"),
                       "-I", str(sdk / "qio_qspi/include"),
                       "-I", str(sdk / "include/esp_rom/esp32/include"),
                       "-c", str(cpp), "-o", str(obj)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            nm = compiler.with_name(compiler.name.removesuffix("g++") + "nm")
            result = subprocess.run([str(nm), "-u", str(obj)], capture_output=True,
                                    text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            symbols = {line.split()[-1] for line in result.stdout.splitlines() if line.split()}
            self.assertIn("jd_prepare", symbols)
            self.assertIn("jd_decomp", symbols)


if __name__ == "__main__":
    unittest.main()
