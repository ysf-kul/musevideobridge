#pragma once
#include "video.hpp"
#include <algorithm>

namespace mvb {
class Timeline {
  uint64_t anchor_ = 0, position_ = 0;
  bool running_ = false;
  uint32_t next_ = 0;

public:
  Rate encoded, target;
  uint32_t count = 0, dropped = 0, rate_skipped = 0, drawn = 0, late = 0;
  uint64_t work_estimate = 0;
  void start(uint64_t now) {
    anchor_ = now - position_;
    running_ = true;
  }
  void pause(uint64_t now) {
    if (running_)
      position_ = position(now);
    running_ = false;
  }
  void stop() {
    running_ = false;
    position_ = 0;
    next_ = 0;
    drawn = dropped = rate_skipped = late = 0;
  }
  uint64_t position(uint64_t now) const {
    return running_ ? now - anchor_ : position_;
  }
  void seek(uint32_t i, uint64_t now) {
    next_ = i;
    position_ = encoded.pts(i);
    anchor_ = now - position_;
  }
  bool running() const { return running_; }
  bool eligible(uint32_t i) const {
    if (uint64_t(target.numerator) * encoded.denominator >=
            uint64_t(encoded.numerator) * target.denominator ||
        i == 0)
      return true;
    uint64_t n = uint64_t(target.numerator) * encoded.denominator,
             d = uint64_t(encoded.numerator) * target.denominator;
    return uint64_t(i) * n / d != uint64_t(i - 1) * n / d;
  }
  bool due(uint64_t now, uint32_t &index) {
    if (!running_ || !count || next_ >= count)
      return false;
    uint64_t pos = position(now);
    // Only a later eligible frame can supersede an eligible presentation.
    // Ineligible source frames must not erase the most recent useful frame.
    uint32_t candidate = next_;
    while (candidate < count && !eligible(candidate)) {
      rate_skipped++;
      candidate++;
    }
    if (candidate >= count) {
      next_ = candidate;
      return false;
    }
    uint32_t scan = candidate + 1;
    while (scan < count && encoded.pts(scan) <= pos) {
      if (eligible(scan)) {
        dropped++;
        for (uint32_t k = candidate + 1; k < scan; k++)
          if (!eligible(k))
            rate_skipped++;
        candidate = scan;
      }
      scan++;
    }
    next_ = candidate;
    if (encoded.pts(next_) > pos + work_estimate)
      return false;
    index = next_++;
    return true;
  }
  // Predict on a copy, preserving presentation/drop counters. When playback
  // is early, prepare the next eligible frame even though it is not due yet.
  bool next_for_prefetch(uint64_t now, uint32_t &index) const {
    Timeline prediction = *this;
    if (prediction.due(now, index))
      return true;
    if (!prediction.running_ || prediction.next_ >= prediction.count)
      return false;
    index = prediction.next_;
    return true;
  }
  void complete(uint32_t i, uint64_t started, uint64_t finished) {
    drawn++;
    work_estimate = (work_estimate * 3 + (finished - started)) / 4;
    if (position(finished) > encoded.pts(i) + 1000)
      late++;
  }
  bool ended(uint64_t now) const {
    return next_ >= count && position(now) >= encoded.pts(count);
  }
};
} // namespace mvb
