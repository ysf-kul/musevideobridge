#pragma once
#include <cstdint>

namespace mvb {
class ProfileClock {
public:
  virtual ~ProfileClock() = default;
  virtual uint64_t now() = 0;
};
struct IoProfile {
  bool available = false;
  uint64_t read_us = 0, seek_us = 0, write_us = 0, flush_us = 0;
  uint64_t read_bytes = 0, written_bytes = 0, read_calls = 0, seek_calls = 0;
};
struct FrameProfile {
  uint64_t sd_read_us = 0, sd_seek_us = 0, crc_us = 0, validate_us = 0;
  uint64_t prepare_us = 0, jpeg_open_us = 0, jpeg_decode_us = 0;
  uint64_t display_us = 0, transform_us = 0, frame_us = 0;
  uint64_t copy_us = 0, wait_us = 0, pixels = 0, windows = 0, pushes = 0;
  bool io_available = false, display_available = false;
  void add(const FrameProfile &p) {
    sd_read_us += p.sd_read_us;
    sd_seek_us += p.sd_seek_us;
    crc_us += p.crc_us;
    validate_us += p.validate_us;
    prepare_us += p.prepare_us;
    jpeg_open_us += p.jpeg_open_us;
    jpeg_decode_us += p.jpeg_decode_us;
    display_us += p.display_us;
    transform_us += p.transform_us;
    frame_us += p.frame_us;
    copy_us += p.copy_us;
    wait_us += p.wait_us;
    pixels += p.pixels;
    windows += p.windows;
    pushes += p.pushes;
    io_available = p.io_available;
    display_available = p.display_available;
  }
};
struct DisplayProfile {
  bool available = false;
  uint64_t submit_us = 0, copy_us = 0, wait_us = 0;
  uint64_t pixels = 0, windows = 0, pushes = 0;
};
// Comparison of decoded expected pixels with pixels already stored by the TFT.
// These probe reads are outside playback presentation and FPS accounting.
struct IntegrityProfile {
  bool available = false;
  uint64_t pixels_checked = 0, pixels_mismatched = 0, read_us = 0;
  uint32_t displayed_crc32 = 0;
};
enum class RenderMode { Legacy, Blocks, Dma };
struct RenderOptions {
  RenderMode mode = RenderMode::Blocks;
  bool big_endian = false;
  bool zero_copy = false; // Explicit experimental codec-output leases only.
  uint32_t spi_hz = 20000000;
  uint32_t command_spi_hz = 20000000;
  uint32_t verify_every_frames = 0;
};
} // namespace mvb
