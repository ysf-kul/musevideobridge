"""Safe JPEG word reads and pinned patch integrity; no serial port is opened."""
import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("jpeg_patch", ROOT / "firmware/patch_jpegdec.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")


class PortableWordTests(unittest.TestCase):
    @unittest.skipUnless(CXX, "A C++ compiler is required")
    def test_host_and_esp32_reads_at_unaligned_allocation_boundaries(self):
        source = r"""
#include "jpeg_portable.hpp"
#include <cassert>
#include <initializer_list>
#include <memory>
uint64_t le(const uint8_t *p, unsigned width) {
  uint64_t n = 0;
  for (unsigned i = 0; i < width; ++i) n |= uint64_t(p[i]) << (i * 8);
  return n;
}
uint64_t be(const uint8_t *p, unsigned width) {
  uint64_t n = 0;
  for (unsigned i = 0; i < width; ++i) n = (n << 8) | p[i];
  return n;
}
int main() {
  for (unsigned width : {2u, 4u, 8u}) {
    for (unsigned offset = 0; offset < 16; ++offset) {
      std::unique_ptr<uint8_t[]> bytes(new uint8_t[offset + width]);
      for (unsigned i = 0; i < offset + width; ++i) bytes[i] = uint8_t(113 + i * 37);
      const uint8_t *p = bytes.get() + offset;
      assert(INTELSHORT(p) == le(p, 2));
      assert(MOTOSHORT(p) == be(p, 2));
      if (width >= 4) {
        assert(INTELLONG(p) == le(p, 4));
#ifdef ARDUINO_ARCH_ESP32
        assert(MOTOLONG(p) == be(p, 4));
#endif
      }
      if (width >= 8) {
        assert(mvb_word<uint64_t>(p) == le(p, 8));
#ifndef ARDUINO_ARCH_ESP32
        assert(MOTOLONG(p) == be(p, sizeof(MOTOLONG(p))));
#endif
      }
    }
  }
}
"""
        with tempfile.TemporaryDirectory(prefix="muse-word-tests-") as directory:
            directory = Path(directory)
            cpp = directory / "reads.cpp"
            cpp.write_text(source)
            for variant in ["host", "esp32"]:
                with self.subTest(variant=variant):
                    executable = directory / variant
                    command = [CXX, "-std=c++11", "-O2", "-fsanitize=address,undefined",
                               "-fno-omit-frame-pointer", "-I", str(ROOT / "firmware/include"),
                               str(cpp), "-o", str(executable)]
                    if variant == "esp32":
                        command.append("-DARDUINO_ARCH_ESP32")
                    result = subprocess.run(command, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(executable)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PinnedPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates = sorted((ROOT / "firmware/.pio/libdeps").glob("*/JPEGDEC/src/jpeg.inl"))
        if not candidates:
            raise unittest.SkipTest("Install the pinned PlatformIO JPEGDEC dependency")
        inl = candidates[0]
        source = inl.read_text()
        if source.startswith(patcher.MARKER):
            source = source.removeprefix(patcher.MARKER)
            for old, (new, _) in patcher.INL_EDITS.items():
                source = source.replace(new, old)
        header = (inl.parent / "JPEGDEC.h").read_text()
        if header.startswith(patcher.HEADER_MARKER):
            header = header.removeprefix(patcher.HEADER_MARKER)
            header = header.replace(patcher.HEADER_NEW, patcher.HEADER_OLD)
        if (hashlib.sha256(source.encode()).hexdigest() != patcher.INL_ORIGINAL_SHA256 or
                hashlib.sha256(header.encode()).hexdigest() != patcher.HEADER_ORIGINAL_SHA256):
            raise RuntimeError("Installed JPEGDEC fixture does not match the pinned upstream hashes")
        cls.original = {"jpeg.inl": source.encode(), "JPEGDEC.h": header.encode()}

    def fixture(self, directory):
        root = Path(directory)
        (root / "src").mkdir()
        for name, data in self.original.items():
            (root / "src" / name).write_bytes(data)
        return root

    def test_exact_original_patch_hashes_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            patcher.patch(root)
            expected = {"jpeg.inl": patcher.INL_PATCHED_SHA256,
                        "JPEGDEC.h": patcher.HEADER_PATCHED_SHA256}
            first = {}
            for name, digest in expected.items():
                first[name] = (root / "src" / name).read_bytes()
                self.assertEqual(hashlib.sha256(first[name]).hexdigest(), digest)
            patcher.patch(root)
            self.assertEqual(first, {name: (root / "src" / name).read_bytes() for name in first})

    def test_original_and_patched_corruption_fail_before_either_file_changes(self):
        for already_patched in [False, True]:
            for name in self.original:
                with self.subTest(already_patched=already_patched, name=name):
                    with tempfile.TemporaryDirectory() as directory:
                        root = self.fixture(directory)
                        if already_patched:
                            patcher.patch(root)
                        path = root / "src" / name
                        path.write_bytes(path.read_bytes() + b"\n// unexpected mutation\n")
                        before = {name: (root / "src" / name).read_bytes() for name in self.original}
                        with self.assertRaisesRegex(RuntimeError, "source changed"):
                            patcher.patch(root)
                        self.assertEqual(before, {name: (root / "src" / name).read_bytes()
                                                  for name in self.original})

    @unittest.skipUnless(CXX, "A C++ compiler is required")
    def test_default_and_optional_buffer_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            patcher.patch(root)
            cpp = root / "buffer.cpp"
            cpp.write_text('#include "JPEGDEC.h"\n'
                           'static_assert(MAX_BUFFERED_PIXELS == EXPECTED_PIXELS, "buffer mismatch");\n')
            for value, allowed in [(None, True), (2048, True), (5120, True),
                                   (0, False), (4096, False), (16384, False)]:
                with self.subTest(value=value):
                    command = [CXX, "-std=c++11", "-D__LINUX__", "-I", str(root / "src"),
                               f"-DEXPECTED_PIXELS={value or 2048}", "-c", str(cpp),
                               "-o", str(root / "buffer.o")]
                    if value is not None:
                        command.append(f"-DMVB_JPEG_MAX_BUFFERED_PIXELS={value}")
                    result = subprocess.run(command, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)
                    if not allowed:
                        self.assertIn("Muse JPEG decoder buffer must be 2048 or 5120 pixels",
                                      result.stderr)


if __name__ == "__main__":
    unittest.main()
