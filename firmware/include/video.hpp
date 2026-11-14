#pragma once
#include "jpeg_validation.hpp"
#include "profiling.hpp"
#include "wire.hpp"
#include <algorithm>

namespace mvb {
class File {
public:
  virtual ~File() = default;
  virtual uint64_t size() = 0;
  virtual bool seek(uint32_t) = 0;
  virtual size_t read(uint8_t *, size_t) = 0;
  virtual size_t write(const uint8_t *, size_t) = 0;
  virtual bool flush() = 0;
  virtual void close() = 0;
  virtual IoProfile io_profile() const { return {}; }
};
inline bool exact(File &f, uint8_t *b, size_t n) { return f.read(b, n) == n; }
struct Rate {
  uint32_t numerator = 10, denominator = 1;
  bool valid() const {
    return numerator && numerator <= 30000 && denominator &&
           denominator <= 1001 && numerator >= denominator &&
           uint64_t(numerator) <= uint64_t(denominator) * 30;
  }
  uint64_t pts(uint32_t frame) const {
    return uint64_t(frame) * 1000000 * denominator / numerator;
  }
  uint32_t at_or_after(uint64_t us, uint32_t count) const {
    // Clamp before multiplication, including arbitrary large host seek values.
    if (!count || us >= pts(count - 1))
      return count ? count - 1 : 0;
    uint64_t divisor = uint64_t(1000000) * denominator;
    return uint32_t((us * numerator + divisor - 1) / divisor);
  }
};
struct Header {
  uint16_t width = 0, height = 0;
  Rate fps;
  uint32_t frames = 0, max_jpeg = 0, index_crc = 0;
  uint64_t duration = 0, index = 0, bytes = 0;
};
struct Entry {
  uint64_t offset = 0, pts = 0;
  uint32_t length = 0, crc = 0;
  uint64_t next() const { return (offset + 16 + length + 3) & ~uint64_t(3); }
};
inline Entry entry(const uint8_t *p) {
  return {get64(p), get64(p + 8), get32(p + 16), get32(p + 20)};
}
inline bool jpeg_dimensions(const uint8_t *b, size_t length, uint16_t &w,
                            uint16_t &h) {
  return baseline_jpeg(b, length, w, h);
}
class Video {
  File *file_ = nullptr;
  uint32_t scan_ = 0, scan_crc_ = 0xffffffffu;
  uint64_t expected_ = 96;
  uint8_t cache_[64 * 24]{};
  uint32_t cache_start_ = 0, cache_count_ = 0;

public:
  static constexpr size_t RecordHeaderBytes = 16;
  Header header;
  const char *error = nullptr;
  bool ready = false;
  bool begin(File *file) {
    file_ = file;
    ready = false;
    error = nullptr;
    scan_ = 0;
    scan_crc_ = 0xffffffffu;
    expected_ = 96;
    cache_count_ = 0;
    uint8_t b[96];
    if (!file || !file->seek(0) || !exact(*file, b, 96))
      return fail("invalid_container");
    uint32_t crc = get32(b + 76);
    put32(b + 76, 0);
    if (std::memcmp(b, "MVBJPG1\0", 8) || get16(b + 8) != 1 ||
        get16(b + 10) != 96 || get32(b + 12) || get64(b + 40) != 96 ||
        get16(b + 68) != 24 || get16(b + 70) != 16 || crc32(b, 96) != crc)
      return fail("invalid_container");
    for (size_t i = 80; i < 96; i++)
      if (b[i])
        return fail("invalid_container");
    header = {get16(b + 16), get16(b + 18), {get32(b + 20), get32(b + 24)},
              get32(b + 28), get32(b + 64), get32(b + 72),
              get64(b + 32), get64(b + 48), get64(b + 56)};
    if (!header.fps.valid() || !header.width || header.width > 320 ||
        !header.height || header.height > 240 || !header.frames ||
        header.frames > 1000000 || !header.max_jpeg ||
        header.max_jpeg > MaxJpeg || header.bytes > 2147483647 ||
        header.bytes != file->size() ||
        header.duration != header.fps.pts(header.frames) || header.index < 96 ||
        header.index > header.bytes ||
        uint64_t(header.frames) * 24 != header.bytes - header.index)
      return fail("invalid_container");
    return true;
  }
  bool fail(const char *why) {
    error = why;
    ready = false;
    return false;
  }
  bool scan_step() {
    if (error || !file_)
      return false;
    if (ready)
      return true;
    uint32_t n = std::min(uint32_t(64), header.frames - scan_);
    if (!file_->seek(uint32_t(header.index + uint64_t(scan_) * 24)) ||
        !exact(*file_, cache_, n * 24))
      return fail("io_error");
    scan_crc_ = crc_update(scan_crc_, cache_, n * 24);
    for (uint32_t k = 0; k < n; k++) {
      Entry e = entry(cache_ + k * 24);
      if (e.offset != expected_ || !e.length || e.length > header.max_jpeg ||
          e.pts != header.fps.pts(scan_ + k) || e.next() > header.index)
        return fail("invalid_container");
      expected_ = e.next();
    }
    scan_ += n;
    cache_count_ = 0;
    if (scan_ == header.frames) {
      if (expected_ != header.index ||
          (scan_crc_ ^ 0xffffffffu) != header.index_crc)
        return fail("crc_mismatch");
      ready = true;
    }
    return true;
  }
  bool get_entry(uint32_t i, Entry &e) {
    if (!ready || i >= header.frames)
      return false;
    if (!cache_count_ || i < cache_start_ || i >= cache_start_ + cache_count_) {
      cache_start_ = i / 64 * 64;
      cache_count_ = std::min(uint32_t(64), header.frames - cache_start_);
      if (!file_->seek(uint32_t(header.index + uint64_t(cache_start_) * 24)) ||
          !exact(*file_, cache_, cache_count_ * 24))
        return fail("io_error");
    }
    e = entry(cache_ + (i - cache_start_) * 24);
    if (e.offset < 96 || e.offset >= header.index || (e.offset & 3) ||
        !e.length || e.length > header.max_jpeg || e.pts != header.fps.pts(i) ||
        e.next() > header.index)
      return fail("invalid_container");
    return true;
  }
  bool frame(uint32_t i, uint8_t *output, size_t &length,
             ProfileClock *clock = nullptr, FrameProfile *profile = nullptr) {
    auto before = file_ ? file_->io_profile() : IoProfile{};
    uint64_t started = clock ? clock->now() : 0;
    Entry e;
    if (!get_entry(i, e))
      return false;
    if (!file_->seek(uint32_t(e.offset)))
      return fail("seek_failed");
    uint8_t b[16];
    if (!exact(*file_, b, 16))
      return fail("io_error");
    if (get32(b) != e.length || get32(b + 4) != i || get32(b + 8) != e.crc ||
        get32(b + 12))
      return fail("invalid_container");
    if (!exact(*file_, output, e.length))
      return fail("io_error");
    length = e.length;
    uint64_t at = clock ? clock->now() : 0;
    auto checksum = crc32(output, length);
    if (profile && clock)
      profile->crc_us = clock->now() - at;
    if (checksum != e.crc)
      return fail("crc_mismatch");
    uint16_t w = 0, h = 0;
    at = clock ? clock->now() : 0;
    bool valid = jpeg_dimensions(output, length, w, h);
    if (profile && clock)
      profile->validate_us = clock->now() - at;
    if (!valid || w != header.width || h != header.height)
      return fail("unsupported_jpeg");
    size_t padding = (4 - length % 4) % 4;
    uint8_t p[3]{};
    if (!exact(*file_, p, padding))
      return fail("io_error");
    for (size_t k = 0; k < padding; k++)
      if (p[k])
        return fail("invalid_container");
    if (profile && clock) {
      auto after = file_->io_profile();
      profile->io_available = before.available && after.available;
      profile->sd_read_us = after.read_us - before.read_us;
      profile->sd_seek_us = after.seek_us - before.seek_us;
      uint64_t measured = profile->sd_read_us + profile->sd_seek_us +
                          profile->crc_us + profile->validate_us;
      uint64_t elapsed = clock->now() - started;
      profile->prepare_us = elapsed >= measured ? elapsed - measured : 0;
    }
    return true;
  }
  // Read the checked header, JPEG and padding together. The JPEG remains at
  // record + RecordHeaderBytes; callers can decode it there without a copy.
  // Capacity must cover this record, at most MaxJpeg + RecordHeaderBytes + 3.
  bool frame_record(uint32_t i, uint8_t *record, size_t capacity,
                    size_t &length, ProfileClock *clock = nullptr,
                    FrameProfile *profile = nullptr) {
    auto before = file_ ? file_->io_profile() : IoProfile{};
    uint64_t started = clock ? clock->now() : 0;
    Entry e;
    if (!get_entry(i, e))
      return false;
    size_t padding = (4 - e.length % 4) % 4;
    size_t record_bytes = RecordHeaderBytes + size_t(e.length) + padding;
    if (!record || capacity < record_bytes)
      return fail("invalid_parameter");
    if (!file_->seek(uint32_t(e.offset)))
      return fail("seek_failed");
    if (!exact(*file_, record, record_bytes))
      return fail("io_error");
    if (get32(record) != e.length || get32(record + 4) != i ||
        get32(record + 8) != e.crc || get32(record + 12))
      return fail("invalid_container");
    uint8_t *payload = record + RecordHeaderBytes;
    length = e.length;
    uint64_t at = clock ? clock->now() : 0;
    auto checksum = crc32(payload, length);
    if (profile && clock)
      profile->crc_us = clock->now() - at;
    if (checksum != e.crc)
      return fail("crc_mismatch");
    uint16_t w = 0, h = 0;
    at = clock ? clock->now() : 0;
    bool valid = jpeg_dimensions(payload, length, w, h);
    if (profile && clock)
      profile->validate_us = clock->now() - at;
    if (!valid || w != header.width || h != header.height)
      return fail("unsupported_jpeg");
    for (size_t k = 0; k < padding; k++)
      if (payload[length + k])
        return fail("invalid_container");
    if (profile && clock) {
      auto after = file_->io_profile();
      profile->io_available = before.available && after.available;
      profile->sd_read_us = after.read_us - before.read_us;
      profile->sd_seek_us = after.seek_us - before.seek_us;
      uint64_t measured = profile->sd_read_us + profile->sd_seek_us +
                          profile->crc_us + profile->validate_us;
      uint64_t elapsed = clock->now() - started;
      profile->prepare_us = elapsed >= measured ? elapsed - measured : 0;
    }
    return true;
  }
};
} // namespace mvb
