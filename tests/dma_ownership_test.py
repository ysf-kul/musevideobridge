"""DMA slot ownership and delayed display pixel integrity; no hardware claims."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")

SOURCE = r'''
#include "dma_ownership.hpp"
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <deque>
#include <random>
#include <vector>

// The fake peripheral keeps the submitted buffer pointer and reads its pixels
// later. Decoder input is immediately overwritten after copying. A buffer reuse
// bug therefore damages the pixel oracle rather than merely changing a counter.
class Display {
public:
  static constexpr size_t Pixels = 64;
  struct Transfer {
    unsigned slot;
    const uint16_t *borrowed;
    std::array<uint16_t, Pixels> expected;
  };
  mvb::DmaOwnership ownership;
  alignas(16) uint16_t buffers[2][Pixels]{};
  std::deque<Transfer> transfers;
  std::vector<uint16_t> actual, expected;
  explicit Display(unsigned depth) : ownership(depth) {}
  unsigned acquire() {
    // A queued buffer and its persistent descriptor are both still borrowed.
    if (ownership.pending() == ownership.depth()) complete_oldest();
    for (unsigned slot = 0; slot < 2; slot++) if (!ownership.owns(slot)) return slot;
    assert(false); return mvb::DmaOwnership::InvalidSlot;
  }
  void copy_and_submit(std::array<uint16_t, Pixels> &decoder) {
    const unsigned slot = acquire(); assert(!ownership.owns(slot));
    std::copy(decoder.begin(), decoder.end(), buffers[slot]);
    assert(ownership.submit(slot));
    Transfer transfer{slot, buffers[slot], decoder};
    transfers.push_back(transfer);
    expected.insert(expected.end(), decoder.begin(), decoder.end());
    decoder.fill(0xdead); // The decoder reuses its callback strip immediately.
  }
  void complete_oldest() {
    assert(!transfers.empty()); Transfer transfer = transfers.front();
    assert(ownership.oldest() == transfer.slot && ownership.owns(transfer.slot));
    assert(std::equal(transfer.expected.begin(), transfer.expected.end(), transfer.borrowed));
    actual.insert(actual.end(), transfer.borrowed, transfer.borrowed + Pixels);
    assert(ownership.complete(transfer.slot)); transfers.pop_front();
  }
  void drain() { while (!transfers.empty()) complete_oldest(); }
};

void delayed_pixels(unsigned depth) {
  Display display(depth); std::mt19937 random(0x9341 + depth);
  for (unsigned frame = 0; frame < 10000; frame++) {
    std::array<uint16_t, Display::Pixels> decoder;
    for (size_t x = 0; x < decoder.size(); x++) decoder[x] = uint16_t(random());
    display.copy_and_submit(decoder);
    if (random() % 3 == 0) display.complete_oldest();
    assert(display.ownership.pending() == display.transfers.size());
    assert(display.ownership.pending() <= depth);
  }
  display.drain();
  assert(display.actual == display.expected);
  assert(display.ownership.submissions() == 10000 && display.ownership.completions() == 10000);
  assert(display.ownership.errors() == 0 && !display.ownership.faulted());
}

void rejected_completions() {
  Display display(2);
  std::array<uint16_t, Display::Pixels> decoder; decoder.fill(0xf800);
  display.copy_and_submit(decoder); decoder.fill(0x07e0); display.copy_and_submit(decoder);
  const unsigned oldest = display.ownership.oldest();
  assert(!display.ownership.complete(1 - oldest)); // Valid slot, wrong FIFO descriptor.
  assert(display.ownership.pending() == 2 && display.ownership.owns(0) && display.ownership.owns(1));
  assert(display.ownership.faulted());
  assert(!display.ownership.complete(9)); // Unknown descriptor cannot release anything.
  assert(!display.ownership.reset(false, 2)); // Reset cannot cancel borrowed hardware memory.
  assert(display.ownership.pending() == 2 && display.ownership.owns(0) && display.ownership.owns(1));
  display.complete_oldest();
  assert(!display.ownership.submit(oldest)); // A fault prohibits recycling even a completed slot.
  display.drain(); assert(display.actual == display.expected);
  assert(display.ownership.reset(true, 2) && !display.ownership.faulted());
  decoder.fill(0x001f); display.copy_and_submit(decoder); display.drain();
  assert(display.actual == display.expected);
  assert(!display.ownership.complete(0)); // Duplicate/stale completion.
  assert(display.ownership.pending() == 0 && !display.ownership.owns(0) && !display.ownership.owns(1));
}

void randomized_state_machine(unsigned depth) {
  mvb::DmaOwnership ownership(depth); std::deque<unsigned> model;
  std::mt19937 random(0x2432 + depth);
  for (unsigned step = 0; step < 50000; step++) {
    if (ownership.faulted()) {
      while (!model.empty()) {
        assert(ownership.complete(model.front())); model.pop_front();
      }
      assert(ownership.reset(true, depth));
    }
    const unsigned slot = random() % 4;
    if (random() % 2) {
      bool valid = slot < 2 && model.size() < depth &&
                   std::find(model.begin(), model.end(), slot) == model.end();
      const auto before = model;
      assert(ownership.submit(slot) == valid);
      if (valid) model.push_back(slot);
      else assert(ownership.pending() == before.size());
    } else {
      bool valid = !model.empty() && model.front() == slot;
      const auto before = model;
      assert(ownership.complete(slot) == valid);
      if (valid) model.pop_front();
      else assert(ownership.pending() == before.size());
    }
    assert(ownership.pending() == model.size());
    assert(ownership.oldest() == (model.empty() ? mvb::DmaOwnership::InvalidSlot : model.front()));
    for (unsigned owned = 0; owned < 2; owned++)
      assert(ownership.owns(owned) == (std::find(model.begin(), model.end(), owned) != model.end()));
  }
}
int main() {
  delayed_pixels(1); delayed_pixels(2); rejected_completions();
  randomized_state_machine(1); randomized_state_machine(2);
  mvb::DmaOwnership invalid(3); assert(invalid.faulted());
  assert(!invalid.reset(true, 3) && invalid.faulted());
  assert(invalid.reset(true, 1) && !invalid.faulted());
}
'''


@unittest.skipUnless(CXX, "A C++ compiler is required")
class DmaOwnershipTests(unittest.TestCase):
    def test_fifo_ownership_delayed_pixel_reads_and_invalid_completion(self):
        with tempfile.TemporaryDirectory(prefix="muse-dma-ownership-") as directory:
            directory = Path(directory)
            source = directory / "test.cpp"
            source.write_text(SOURCE)
            executable = directory / "test"
            command = [CXX, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                       "-I", str(ROOT / "firmware/include"), str(source), "-o", str(executable)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
