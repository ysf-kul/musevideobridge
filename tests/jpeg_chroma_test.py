"""Default and explicit-research JPEG validation parity; no hardware access."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")
SOURCE = r'''
#include "jpeg_validation.hpp"
#include <cassert>
#include <fstream>
#include <string>
#include <vector>
std::vector<uint8_t> read(const std::string &path) {
  std::ifstream file(path, std::ios::binary); assert(file);
  return std::vector<uint8_t>((std::istreambuf_iterator<char>(file)), {});
}
size_t marker(const std::vector<uint8_t> &data, uint8_t expected) {
  size_t at = 2;
  while (data.at(at + 1) != expected)
    at += 2 + (size_t(data.at(at + 2)) << 8) + data.at(at + 3);
  return at;
}
bool accepted(const std::vector<uint8_t> &data) {
  uint16_t w = 0, h = 0;
  bool valid = mvb::baseline_jpeg(data.data(), data.size(), w, h);
  if (valid) assert(w == 317 && h == 239);
  return valid;
}
int main(int argc, char **argv) {
  assert(argc == 2);
  const std::string folder = argv[1];
  for (const char *name : {"edge-2x2.jpg", "edge-2x1.jpg", "edge-1x1.jpg"}) {
    auto sample = read(folder + "/" + name);
#ifdef MVB_RESEARCH_JPEG_CHROMA
    assert(accepted(sample));
#else
    assert(accepted(sample) == (std::string(name) == "edge-2x2.jpg"));
#endif
    const auto sof = marker(sample, 0xc0), dqt = marker(sample, 0xdb);
    const auto dht = marker(sample, 0xc4), sos = marker(sample, 0xda);
    for (auto mutation : std::vector<std::pair<size_t, uint8_t>>{
           {sof + 10, 4}, {sof + 11, 0x12}, {sof + 12, 2},
           {sof + 14, 0x21}, {sof + 17, 0x12}, {dqt + 4, 0x10},
           {dqt + 5, 0}, {dht + 4, 2}, {dht + 5, 255},
           {sos + 6, 0x10}, {sos + 12, 62}}) {
      auto damaged = sample;
      damaged.at(mutation.first) = mutation.second;
      assert(!accepted(damaged));
    }
    auto damaged = sample;
    damaged.insert(damaged.end() - 2, {255, 0xc0});
    assert(!accepted(damaged));
    damaged = sample; damaged.pop_back(); assert(!accepted(damaged));
  }
  assert(!accepted(read(folder + "/unsupported-ffmpeg-444.jpg")));
}
'''


@unittest.skipUnless(CXX, "C++ compiler required")
class JpegChromaValidationTests(unittest.TestCase):
    def test_default_and_research_sampling_preserve_structure_guards(self):
        with tempfile.TemporaryDirectory(prefix="muse-jpeg-chroma-") as temporary:
            directory = Path(temporary)
            source = directory / "test.cpp"
            source.write_text(SOURCE)
            for research in (False, True):
                binary = directory / ("research" if research else "default")
                args = [CXX, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                        "-I", str(ROOT / "firmware/include")]
                if research:
                    args += ["-DMVB_RESEARCH_JPEG_CHROMA"]
                args += [str(source), "-o", str(binary)]
                compiled = subprocess.run(args, capture_output=True, text=True, timeout=60)
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                tested = subprocess.run([str(binary), str(ROOT / "tests/fixtures/tjpgd_fast")],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)


if __name__ == "__main__":
    unittest.main()
