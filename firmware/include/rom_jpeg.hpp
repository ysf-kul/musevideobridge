#pragma once
#include "render.hpp"
#include <cstddef>
#include <cstring>

#ifdef ARDUINO_ARCH_ESP32
#include <sdkconfig.h>
#endif
#if defined(ARDUINO_ARCH_ESP32) && defined(CONFIG_IDF_TARGET_ESP32)
#include <esp32/rom/tjpgd.h>
#define MVB_HAS_ROM_JPEG 1
#else
#define MVB_HAS_ROM_JPEG 0
#endif

namespace mvb {
// ROM TJpgDec produces temporary, tightly packed RGB888 MCU rectangles. Gather
// one complete MCU row into reusable RGB565 storage before presenting it. This
// avoids a full framebuffer and preserves continuous native-width TFT writes.
class Rgb888StripCollector {
  alignas(4) uint16_t pixels_[320 * 16];
  TileRenderer *renderer_ = nullptr;
  ProfileClock *clock_ = nullptr;
  unsigned width_ = 0, height_ = 0, completed_y_ = 0, next_x_ = 0,
           strip_height_ = 0;
  bool draw_ = false, big_endian_ = false, failed_ = false;
  uint64_t draw_us_ = 0, color_us_ = 0;

public:
  bool begin(TileRenderer &renderer, ProfileClock &clock, unsigned width,
             unsigned height, bool draw_image, bool big_endian) {
    renderer_ = &renderer;
    clock_ = &clock;
    width_ = width;
    height_ = height;
    completed_y_ = next_x_ = strip_height_ = 0;
    draw_ = draw_image;
    big_endian_ = big_endian;
    draw_us_ = color_us_ = 0;
    failed_ = width == 0 || width > 320 || height == 0 || height > 240;
    return !failed_;
  }
  bool accept(unsigned left, unsigned right, unsigned top, unsigned bottom,
              const uint8_t *rgb) {
    if (failed_ || !rgb || !clock_ || left > right || top > bottom ||
        right >= width_ || bottom >= height_ || top != completed_y_ ||
        left != next_x_ || bottom - top + 1 > 16 ||
        (next_x_ && bottom - top + 1 != strip_height_)) {
      failed_ = true;
      return false;
    }
    if (!next_x_)
      strip_height_ = bottom - top + 1;
    const unsigned used = right - left + 1;
    uint64_t at = clock_->now();
    for (unsigned row = 0; row < strip_height_; row++) {
      uint16_t *dest = pixels_ + row * width_ + left;
      for (unsigned column = 0; column < used; column++, rgb += 3) {
        uint16_t color = uint16_t((uint16_t(rgb[0] & 0xf8) << 8) |
                                  (uint16_t(rgb[1] & 0xfc) << 3) |
                                  (rgb[2] >> 3));
        dest[column] = big_endian_ ? uint16_t((color >> 8) | (color << 8))
                                   : color;
      }
    }
    color_us_ += clock_->now() - at;
    next_x_ = right + 1;
    if (next_x_ == width_) {
      bool ok = true;
      if (draw_) {
        at = clock_->now();
        ok = renderer_->tile(0, int(top), int(width_), int(width_),
                             int(strip_height_), pixels_);
        draw_us_ += clock_->now() - at;
      }
      if (!ok) {
        failed_ = true;
        return false;
      }
      completed_y_ += strip_height_;
      next_x_ = strip_height_ = 0;
    }
    return true;
  }
  bool complete() const {
    return width_ != 0 && height_ != 0 && !failed_ &&
           completed_y_ == height_ && next_x_ == 0;
  }
  bool failed() const { return failed_; }
  uint64_t draw_us() const { return draw_us_; }
  uint64_t color_us() const { return color_us_; }
};

class RomJpeg {
  Rgb888StripCollector strips_;
#if MVB_HAS_ROM_JPEG
  JDEC decoder_{};
  alignas(4) uint8_t workspace_[6144];
  const uint8_t *input_ = nullptr;
  size_t length_ = 0, position_ = 0;
  static UINT input(JDEC *decoder, BYTE *destination, UINT requested) {
    auto *self = static_cast<RomJpeg *>(decoder->device);
    if (!self || self->position_ > self->length_)
      return 0;
    size_t available = self->length_ - self->position_;
    UINT count = requested < available ? requested : UINT(available);
    if (destination && count)
      std::memcpy(destination, self->input_ + self->position_, count);
    self->position_ += count;
    return count;
  }
  static UINT output(JDEC *decoder, void *bitmap, JRECT *rectangle) {
    auto *self = static_cast<RomJpeg *>(decoder->device);
    if (!self || !rectangle)
      return 0;
    return self->strips_.accept(rectangle->left, rectangle->right,
                                rectangle->top, rectangle->bottom,
                                static_cast<const uint8_t *>(bitmap)) ? 1 : 0;
  }
#endif

public:
  static constexpr bool available() { return MVB_HAS_ROM_JPEG != 0; }
  bool prepare(const uint8_t *bytes, size_t length, TileRenderer &renderer,
               ProfileClock &clock, bool draw_image, bool big_endian) {
#if MVB_HAS_ROM_JPEG
    if (!bytes || !length)
      return false;
    input_ = bytes;
    length_ = length;
    position_ = 0;
    decoder_ = {};
    if (jd_prepare(&decoder_, input, workspace_, sizeof(workspace_), this) !=
            JDR_OK ||
        (draw_image &&
         (decoder_.width != unsigned(renderer.source_width) ||
          decoder_.height != unsigned(renderer.source_height))))
      return false;
    return strips_.begin(renderer, clock, decoder_.width, decoder_.height,
                          draw_image, big_endian);
#else
    (void)bytes; (void)length; (void)renderer; (void)clock;
    (void)draw_image; (void)big_endian;
    return false;
#endif
  }
  bool decode() {
#if MVB_HAS_ROM_JPEG
    return jd_decomp(&decoder_, output, 0) == JDR_OK && strips_.complete();
#else
    return false;
#endif
  }
  bool draw_failed() const { return strips_.failed(); }
  uint64_t draw_us() const { return strips_.draw_us(); }
  uint64_t color_us() const { return strips_.color_us(); }
};
} // namespace mvb
