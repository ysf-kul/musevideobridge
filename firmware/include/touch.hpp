#pragma once
#include <Arduino.h>
#include <algorithm>

// XPT2046 conversion sequence adapted from XPT2046_Touchscreen:
// https://github.com/PaulStoffregen/XPT2046_Touchscreen
// Copyright (c) 2015, Paul Stoffregen, paul@pjrc.com
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
// The above copyright notice, development funding notice, and this permission
// notice shall be included in all copies or substantial portions of the Software.
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
// THE SOFTWARE.

namespace mvb {
// The reference CYD's XPT2046 has dedicated wires. Software SPI keeps both
// hardware SPI hosts (TFT/SD) and their DMA ownership completely untouched.
class CydTouch {
  static constexpr int Clock = 25, Mosi = 32, Miso = 39, Cs = 33, Irq = 36;
  uint32_t last_poll_ = 0;
  uint32_t contacts_ = 0, accepted_ = 0;
  bool pressed_ = false, pending_ = false;
  int pending_x_ = 0, pending_y_ = 0;
  uint16_t accepted_z1_ = 0, accepted_z2_ = 0, accepted_x_ = 0, accepted_y_ = 0;
  uint16_t exchange(unsigned bits, uint16_t value) {
    uint16_t result = 0;
    for (int bit = int(bits) - 1; bit >= 0; --bit) {
      digitalWrite(Mosi, (value >> bit) & 1);
      delayMicroseconds(1);
      digitalWrite(Clock, HIGH); delayMicroseconds(1);
      result = uint16_t((result << 1) | digitalRead(Miso));
      digitalWrite(Clock, LOW); delayMicroseconds(1);
    }
    return result;
  }
public:
  uint32_t contacts_sampled() const { return contacts_; }
  uint32_t contacts_accepted() const { return accepted_; }
  uint16_t accepted_z1() const { return accepted_z1_; }
  uint16_t accepted_z2() const { return accepted_z2_; }
  uint16_t accepted_x() const { return accepted_x_; }
  uint16_t accepted_y() const { return accepted_y_; }
  void begin() {
    pinMode(Cs, OUTPUT); digitalWrite(Cs, HIGH);
    pinMode(Clock, OUTPUT); digitalWrite(Clock, LOW);
    pinMode(Mosi, OUTPUT); pinMode(Miso, INPUT); pinMode(Irq, INPUT);
    // The ESP32 may reset while the separately powered controller is converting.
    // Complete a known power-down command so PENIRQ starts in its idle mode.
    digitalWrite(Cs, LOW); delayMicroseconds(1);
    exchange(8, 0xd0); exchange(16, 0);
    digitalWrite(Cs, HIGH);
  }
  // Keep ADC sampling/sorting out of the renderer's caller instruction path.
  [[gnu::noinline]] bool poll(int &x, int &y) {
    uint32_t now = millis();
    if (uint32_t(now - last_poll_) < 30) return false;
    last_poll_ = now;
    if (digitalRead(Irq)) { pressed_ = pending_ = false; return false; }
    if (pressed_) return false; // One action per contact, release to repeat.
    if (!pending_) ++contacts_;
    // PENIRQ alone can glitch. Confirm resistive pressure before accepting XY
    // and never repeatedly sample an invalid held contact in the frame loop.
    // Hold CS and the ADC reference through the pipelined measurements. The
    // outgoing low byte starts the next conversion while the previous is read.
    digitalWrite(Cs, LOW); delayMicroseconds(1);
    exchange(8, 0xb1);
    const uint16_t z1 = (exchange(16, 0xc1) >> 3) & 4095;
    const uint16_t z2 = (exchange(16, 0x91) >> 3) & 4095;
    const bool pressure = z1 && z2 >= z1 && 4095 + int(z1) - int(z2) >= 400;
    uint16_t rx[3]{}, ry[3]{};
    if (pressure) {
      exchange(16, 0x91); // Discard the first noisy X conversion.
      for (int i = 0; i < 2; ++i) {
        rx[i] = (exchange(16, 0xd1) >> 3) & 4095;
        ry[i] = (exchange(16, 0x91) >> 3) & 4095;
      }
    }
    rx[2] = (exchange(16, 0xd0) >> 3) & 4095;
    ry[2] = (exchange(16, 0) >> 3) & 4095; // Drain and power down on every path.
    digitalWrite(Cs, HIGH);
    if (!pressure) { pressed_ = true; pending_ = false; return false; }
    std::sort(rx, rx + 3); std::sort(ry, ry + 3);
    // Reference landscape calibration; actual panel calibration is qualified
    // separately from the protocol and simulator tests.
    if (rx[1] < 150 || rx[1] > 3950 || ry[1] < 150 || ry[1] > 3950) {
      pressed_ = true; pending_ = false; return false;
    }
    if (std::min(rx[1] - rx[0], rx[2] - rx[1]) > 240 ||
        std::min(ry[1] - ry[0], ry[2] - ry[1]) > 240 || digitalRead(Irq)) {
      pressed_ = true; pending_ = false; return false;
    }
    x = std::clamp((int(rx[1]) - 200) * 320 / 3700, 0, 319);
    y = std::clamp((int(ry[1]) - 200) * 240 / 3700, 0, 239);
    // Require matching pressure/coordinates on two polls 30 ms apart. A
    // transient PENIRQ/ADC result must never become a playback action.
    if (!pending_) {
      pending_ = true; pending_x_ = x; pending_y_ = y; return false;
    }
    pressed_ = true; pending_ = false;
    if (std::abs(x - pending_x_) > 32 || std::abs(y - pending_y_) > 32) return false;
    accepted_z1_ = z1; accepted_z2_ = z2;
    accepted_x_ = rx[1]; accepted_y_ = ry[1];
    ++accepted_;
    return true;
  }
};
} // namespace mvb
