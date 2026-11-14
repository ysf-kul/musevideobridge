"""Exercise the production CYD adapter with GPIO and ADC responses, without a panel."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")

ARDUINO = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <deque>
#include <vector>
constexpr int INPUT = 0, OUTPUT = 1, LOW = 0, HIGH = 1;
inline uint32_t test_now = 100;
inline bool test_contact = false;
inline std::deque<int> test_adc;
inline unsigned test_bits_read = 0;
inline uint64_t test_micros = 0, test_last_clock = 0;
inline bool test_clock_seen = false;
inline bool test_cs = false;
inline unsigned test_command_bits = 0;
inline int test_mosi = 0;
inline uint8_t test_command = 0;
inline std::vector<uint8_t> test_commands;
inline uint32_t millis() { return test_now; }
inline void delayMicroseconds(unsigned us) { test_micros += us; }
inline void pinMode(int, int) {}
inline void digitalWrite(int pin, int value) {
  if (pin == 33) {
    test_cs = value == LOW; test_command_bits = 0; test_command = 0;
  }
  if (pin == 32) test_mosi = value;
  if (pin == 25 && value == HIGH) {
    if (test_clock_seen) assert(test_micros - test_last_clock >= 2);
    test_clock_seen = true; test_last_clock = test_micros;
    if (test_cs) {
      test_command = (test_command << 1) | test_mosi;
      if (++test_command_bits == 8) {
        test_commands.push_back(test_command); test_command_bits = 0; test_command = 0;
      }
    }
  }
}
inline int digitalRead(int pin) {
  if (pin == 36) return !test_contact;
  assert(pin == 39 && !test_adc.empty());
  ++test_bits_read;
  int bit = test_adc.front(); test_adc.pop_front(); return bit;
}
inline void adc(uint16_t value) {
  uint16_t word = value << 3;
  for (int i = 15; i >= 0; --i) test_adc.push_back((word >> i) & 1);
}
inline void ignored_byte() { for (int i = 0; i < 8; ++i) test_adc.push_back(0); }
inline void pressure(uint16_t z1, uint16_t z2) {
  ignored_byte(); adc(z1); adc(z2);
}
'''

SOURCE = r'''
#include "touch.hpp"
void release(mvb::CydTouch &touch) {
  int x = -1, y = -1;
  test_contact = false; test_now += 30;
  assert(!touch.poll(x, y));
}
int main() {
  ignored_byte(); adc(0);
  mvb::CydTouch touch; touch.begin();
  assert(test_adc.empty() && (test_commands == std::vector<uint8_t>{0xd0, 0, 0}));
  const unsigned initialization_reads = test_bits_read;
  int x = -1, y = -1;
  assert(!touch.poll(x, y) && test_bits_read == initialization_reads); // Idle only reads IRQ.
  test_contact = true; test_now += 30;
  pressure(500, 3500); adc(0); // Settling conversion is discarded.
  for (int i = 0; i < 3; ++i) { adc(2050); adc(2050); }
  assert(!touch.poll(x, y) && test_adc.empty());
  assert((test_commands == std::vector<uint8_t>{0xd0, 0, 0, 0xb1, 0, 0xc1, 0, 0x91, 0, 0x91, 0, 0xd1, 0, 0x91, 0, 0xd1, 0, 0x91, 0, 0xd0, 0, 0}));
  test_now += 30; pressure(500, 3500); adc(0);
  for (int i = 0; i < 3; ++i) { adc(2050); adc(2050); }
  assert(touch.poll(x, y) && x == 160 && y == 120 && test_adc.empty());
  assert(touch.contacts_sampled() == 1 && touch.contacts_accepted() == 1);
  assert(touch.accepted_z1() == 500 && touch.accepted_z2() == 3500);
  assert(touch.accepted_x() == 2050 && touch.accepted_y() == 2050);
  unsigned reads = test_bits_read;
  for (int i = 0; i < 10; ++i) { test_now += 30; assert(!touch.poll(x, y)); }
  assert(test_bits_read == reads); // A held contact never repeats ADC sampling.
  release(touch);
  test_contact = true; test_now += 10;
  assert(!touch.poll(x, y) && test_bits_read == reads); // Poll rate limit.
  test_now += 20;
  pressure(0, 4095); adc(0); adc(0); // No pressure; still drain and power down.
  assert(!touch.poll(x, y) && test_adc.empty());
  reads = test_bits_read;
  test_now += 30; assert(!touch.poll(x, y) && test_bits_read == reads);
  release(touch);
  test_contact = true; test_now += 30;
  pressure(500, 3500); adc(0);
  adc(2050); adc(2050); adc(1000); adc(2050); adc(2500); adc(2050);
  assert(!touch.poll(x, y) && test_adc.empty()); // Unstable coordinates.
  assert(touch.contacts_sampled() == 3 && touch.contacts_accepted() == 1);
  release(touch);
  test_contact = true; test_now += 30;
  pressure(500, 3500); adc(0);
  adc(3500); adc(2100); adc(2000); adc(2100); adc(2005); adc(2100);
  assert(!touch.poll(x, y) && test_adc.empty());
  test_now += 30; pressure(500, 3500); adc(0);
  adc(3500); adc(2100); adc(2000); adc(2100); adc(2005); adc(2100);
  assert(touch.poll(x, y) && x == 156 && y == 123 && test_adc.empty()); // One outlier and two stable samples.
  assert(touch.contacts_sampled() == 4 && touch.contacts_accepted() == 2);
  release(touch);
  test_contact = true; test_now += 30; pressure(500, 3500); adc(0);
  for (int i = 0; i < 3; ++i) { adc(2050); adc(2050); }
  assert(!touch.poll(x, y) && test_adc.empty()); // Transient valid ADC result.
  release(touch);
  assert(touch.contacts_sampled() == 5 && touch.contacts_accepted() == 2);
}
'''


@unittest.skipUnless(CXX, "A C++ compiler is required")
class TouchAdapterTests(unittest.TestCase):
    def test_pressure_coordinates_debounce_and_idle_sampling(self):
        with tempfile.TemporaryDirectory(prefix="muse-touch-") as directory:
            directory = Path(directory)
            (directory / "Arduino.h").write_text(ARDUINO)
            (directory / "touch.cpp").write_text(SOURCE)
            executable = directory / "touch"
            command = [CXX, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                       "-I", str(directory), "-I", str(ROOT / "firmware/include"),
                       str(directory / "touch.cpp"), "-o", str(executable)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
