"""Real software decoder/color/strip checks; never opens a hardware port."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "firmware/vendor/tjpgd_fast"
FIXTURES = ROOT / "tests/fixtures/tjpgd_fast"
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")
CC = os.environ.get("CC") or shutil.which("clang") or shutil.which("gcc")

REFERENCE = r'''
#include "tjpgd.h"
#include <stdint.h>
struct Input { const uint8_t *bytes; size_t length, position; uint16_t *pixels; };
static size_t input(JDEC *decoder, uint8_t *dest, size_t n) {
  struct Input *in = decoder->device;
  if (n > in->length - in->position) n = in->length - in->position;
  if (dest) memcpy(dest, in->bytes + in->position, n);
  in->position += n;
  return n;
}
static int output(JDEC *decoder, void *data, JRECT *r) {
  struct Input *in = decoder->device;
  const uint8_t *rgb = data;
  for (unsigned y = r->top; y <= r->bottom; y++)
    for (unsigned x = r->left; x <= r->right; x++, rgb += 3)
      in->pixels[y * decoder->width + x] =
          (uint16_t)(((rgb[0] / 8) << 11) | ((rgb[1] / 4) << 5) | (rgb[2] / 8));
  return 1;
}
int reference_decode(const uint8_t *bytes, size_t n, uint16_t *pixels,
                      unsigned *width, unsigned *height) {
  _Alignas(4) uint8_t pool[10240]; JDEC decoder;
  struct Input in = {bytes, n, 0, pixels};
  int result = jd_prepare(&decoder, input, pool, sizeof(pool), &in);
  if (result) return result;
  *width = decoder.width; *height = decoder.height;
  if (!pixels) return 0;
  return jd_decomp(&decoder, output, 0);
}
'''

SOURCE = r'''
#include "tjpgd_fast.hpp"
#include <cassert>
#include <fstream>
#include <string>
#include <vector>
extern "C" int reference_decode(const uint8_t*, size_t, uint16_t*, unsigned*, unsigned*);
class Clock : public mvb::ProfileClock {
public:
  uint64_t ticks = 0;
  uint64_t now() override { return ticks++; }
};
class Canvas : public mvb::Painter {
public:
  std::vector<uint16_t> pixels = std::vector<uint16_t>(320 * 240);
  std::vector<unsigned> writes = std::vector<unsigned>(320 * 240);
  bool reject = false;
  unsigned blocks = 0, rows = 0;
  bool row(int x, int y, const uint16_t *p, int n) override {
    ++rows;
    if (reject) return false;
    assert(p && x >= 0 && y >= 0 && n > 0 && y < 240 && x + n <= 320);
    for (int i = 0; i < n; i++) {
      pixels[y * 320 + x + i] = p[i]; ++writes[y * 320 + x + i];
    }
    return true;
  }
  bool block(int x, int y, const uint16_t *p, int w, int h, int stride) override {
    ++blocks;
    if (reject) return false;
    for (int line = 0; line < h; line++)
      if (!row(x, y + line, p + line * stride, w)) return false;
    return true;
  }
  void clear(mvb::Rect) override {}
  void waiting(bool, bool, const char*) override {}
};
std::vector<uint8_t> read(const std::string &path) {
  std::ifstream file(path, std::ios::binary); assert(file);
  return std::vector<uint8_t>((std::istreambuf_iterator<char>(file)), {});
}
std::vector<uint16_t> reference(const std::vector<uint8_t> &bytes,
                                unsigned &w, unsigned &h) {
  assert(reference_decode(bytes.data(), bytes.size(), nullptr, &w, &h) == 0);
  std::vector<uint16_t> pixels(w * h);
  assert(reference_decode(bytes.data(), bytes.size(), pixels.data(), &w, &h) == 0);
  return pixels;
}
void native(const std::string &folder) {
  for (const char *name : {"edge-1x1.jpg", "edge-2x1.jpg", "edge-2x2.jpg", "gray-17x13.jpg"}) {
    auto bytes = read(folder + "/" + name);
    unsigned w, h; auto expected = reference(bytes, w, h);
    for (bool big : {false, true}) {
      mvb::TjpgdFast decoder; Clock clock; Canvas canvas;
      mvb::TileRenderer renderer(canvas);
      renderer.source_width = int(w); renderer.source_height = int(h);
      renderer.transform = {1, 0, int(w), int(h), 0, false};
      assert(!decoder.decode());
      for (int pass = 0; pass < 4; pass++) {
        std::fill(canvas.pixels.begin(), canvas.pixels.end(), 0);
        std::fill(canvas.writes.begin(), canvas.writes.end(), 0);
        assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, big));
        const auto used = decoder.workspace_used(); assert(used > 0 && used <= 10240);
        assert(decoder.decode() && !decoder.decode());
        assert(decoder.workspace_used() == used && decoder.copy_us() > 0);
        for (unsigned y = 0; y < 240; y++)
          for (unsigned x = 0; x < 320; x++) {
            uint16_t color = 0; bool inside = x >= 1 && x <= w && y < h;
            if (inside) {
              color = expected[y * w + x - 1];
              if (big) color = uint16_t((color << 8) | (color >> 8));
            }
            assert(canvas.pixels[y * 320 + x] == color);
            assert(canvas.writes[y * 320 + x] == (inside ? 1u : 0u));
          }
      }
    }
  }
}
void transforms(const std::string &folder) {
  auto bytes = read(folder + "/gray-17x13.jpg");
  unsigned w, h; auto expected = reference(bytes, w, h);
  unsigned cases = 0;
  mvb::TjpgdFast decoder; Clock clock;
  for (int rotation : {0, 90, 180, 270})
    for (int dx : {-640, -5, 11, 319, 640})
      for (int dw : {1, 7, 17, 34, 640})
        for (int dh : {1, 5, 13, 26, 480}) {
          Canvas canvas; mvb::TileRenderer renderer(canvas);
          renderer.source_width = int(w); renderer.source_height = int(h);
          renderer.transform = {dx, -3, dw, dh, rotation, false};
          assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, false));
          assert(decoder.decode());
          int rw = rotation % 180 ? int(h) : int(w);
          int rh = rotation % 180 ? int(w) : int(h);
          for (int y = 0; y < 240; y++)
            for (int x = 0; x < 320; x++) {
              const int u = x - dx, v = y + 3;
              const bool inside = u >= 0 && u < dw && v >= 0 && v < dh;
              uint16_t color = 0;
              if (inside) {
                int sx, sy;
                int a = std::min(rw - 1, (2 * u + 1) * rw / (2 * dw));
                int b = std::min(rh - 1, (2 * v + 1) * rh / (2 * dh));
                if (rotation == 0) { sx = a; sy = b; }
                else if (rotation == 90) { sx = b; sy = int(h) - 1 - a; }
                else if (rotation == 180) { sx = int(w) - 1 - a; sy = int(h) - 1 - b; }
                else { sx = int(w) - 1 - b; sy = a; }
                color = expected[sy * w + sx];
              }
              assert(canvas.pixels[y * 320 + x] == color);
              assert(canvas.writes[y * 320 + x] == (inside ? 1u : 0u));
            }
          ++cases;
        }
  assert(cases == 500);
}
void failures(const std::string &folder) {
  auto bytes = read(folder + "/edge-2x2.jpg");
  auto bad = read(folder + "/unsupported-ffmpeg-444.jpg");
  Clock clock; Canvas canvas; mvb::TileRenderer renderer(canvas);
  renderer.source_width = 317; renderer.source_height = 239;
  renderer.transform = {0, 0, 317, 239, 0, false};
  mvb::TjpgdFast decoder;
  assert(!decoder.decode());
  assert(!decoder.prepare(nullptr, 0, renderer, clock, true, false) && !decoder.decode());
  assert(!decoder.prepare(bytes.data(), 19, renderer, clock, true, false) && !decoder.decode());
  assert(!decoder.prepare(bad.data(), bad.size(), renderer, clock, true, false) && !decoder.decode());
  canvas.reject = true;
  assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, false));
  assert(!decoder.decode() && decoder.draw_failed());
  canvas.reject = false;
  assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, false));
  assert(decoder.decode() && !decoder.draw_failed());
  assert(!decoder.prepare(bytes.data(), 1, renderer, clock, true, false) && !decoder.decode());
  const auto rows = canvas.rows;
  renderer.source_width = 1; renderer.source_height = 1;
  assert(!decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, false));
  assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, true));
  assert(decoder.decode() && canvas.rows == rows && decoder.draw_us() == 0);
  assert(decoder.copy_us() > 0);
  assert(decoder.prepare(bytes.data(), bytes.size() - 500, renderer, clock, false, false));
  assert(!decoder.decode());
}
void strips() {
  Clock clock; Canvas canvas; mvb::TileRenderer renderer(canvas);
  renderer.source_width = renderer.source_height = 1;
  renderer.transform = {0, 0, 1, 1, 0, false};
  mvb::Rgb565StripCollector collector; alignas(4) uint16_t pixels[256]{};
  assert(!collector.complete());
  assert(!collector.begin(renderer, clock, 321, 1, true, false));
  assert(collector.begin(renderer, clock, 1, 1, true, false));
  assert(!collector.accept(0, 0, 0, 0, nullptr));
  assert(!collector.accept(0, 0, 0, 0, pixels));
  assert(collector.begin(renderer, clock, 1, 1, true, false));
  assert(!collector.accept(1, 1, 0, 0, pixels));
  assert(collector.begin(renderer, clock, 1, 17, true, false));
  assert(!collector.accept(0, 0, 0, 16, pixels));
  assert(collector.begin(renderer, clock, 1, 1, true, false));
  auto *unaligned = reinterpret_cast<const uint16_t *>(reinterpret_cast<const uint8_t *>(pixels) + 1);
  assert(!collector.accept(0, 0, 0, 0, unaligned));
  assert(collector.begin(renderer, clock, 2, 2, false, false));
  assert(collector.accept(0, 0, 0, 1, pixels) && !collector.complete());
  assert(!collector.accept(1, 1, 0, 0, pixels));
  assert(collector.begin(renderer, clock, 1, 1, true, false));
  pixels[0] = 0x19e3;
  assert(collector.accept(0, 0, 0, 0, pixels) && collector.complete());
  pixels[0] = 0;
  assert(canvas.pixels[0] == 0x19e3);
}
int main(int argc, char **argv) {
  assert(argc == 3);
  const std::string scenario = argv[1], folder = argv[2];
  static_assert(mvb::TjpgdFast::available(), "Test must exercise actual software codec");
  if (scenario == "native") native(folder);
  else if (scenario == "transforms") transforms(folder);
  else if (scenario == "failures") failures(folder);
  else if (scenario == "strips") strips();
  else return 2;
}
'''


class SourceIntegrityTests(unittest.TestCase):
    def test_upstream_and_adapted_source_hashes(self):
        provenance = json.loads((VENDOR / "provenance.json").read_text())
        for entry in provenance["upstream"]:
            name = entry["file"].removeprefix("Espressif_tjpgd_")
            self.assertEqual(hashlib.sha256((VENDOR / "upstream" / name).read_bytes()).hexdigest(),
                             entry["sha256"])
        for name, expected in provenance["local_sha256"].items():
            self.assertEqual(hashlib.sha256((VENDOR / name).read_bytes()).hexdigest(), expected)


@unittest.skipUnless(CXX and CC, "C and C++ compilers are required")
class FastDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="muse-fast-jpeg-tests-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        (directory / "sdkconfig.h").write_text(
            "#define CONFIG_JD_SZBUF 512\n#define CONFIG_JD_FORMAT 0\n"
            "#define CONFIG_JD_USE_SCALE 0\n#define CONFIG_JD_TBLCLIP 1\n"
            "#define CONFIG_JD_FASTDECODE 2\n")
        (directory / "reference.c").write_text(REFERENCE)
        (directory / "test.cpp").write_text(SOURCE)
        sanitizer = ["-O2", "-g", "-Wall", "-Wextra", "-Werror",
                     "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        commands = [
            [CC, "-std=c11", *sanitizer, "-I", str(directory), "-I", str(VENDOR / "upstream"),
             "-c", str(VENDOR / "upstream/tjpgd.c"), "-o", str(directory / "reference-codec.o")],
            [CC, "-std=c11", *sanitizer, "-I", str(directory), "-I", str(VENDOR / "upstream"),
             "-c", str(directory / "reference.c"), "-o", str(directory / "reference.o")],
            [CXX, "-std=c++17", *sanitizer, "-DMVB_ENABLE_TJPGD_FAST",
             "-I", str(ROOT / "firmware/include"), str(directory / "test.cpp"),
             str(ROOT / "firmware/src/tjpgd_fast.cpp"), str(directory / "reference-codec.o"),
             str(directory / "reference.o"), "-o", str(directory / "test")],
        ]
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
        cls.executable = directory / "test"

    def scenario(self, name):
        result = subprocess.run([str(self.executable), name, str(FIXTURES)], capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_rgb565_matches_unmodified_rgb888_codec_edges_endian_and_reuse(self):
        self.scenario("native")

    def test_500_transforms_and_clipping_against_independent_pixel_oracle(self):
        self.scenario("transforms")

    def test_unsupported_truncated_stale_prepare_draw_failure_and_decode_only(self):
        self.scenario("failures")

    def test_collector_bounds_order_alignment_partial_rows_and_buffer_reuse(self):
        self.scenario("strips")


if __name__ == "__main__":
    unittest.main()
