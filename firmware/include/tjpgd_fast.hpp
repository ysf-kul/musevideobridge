#pragma once
#include "render.hpp"
#include <cstddef>
#include <cstring>

#ifdef MVB_ENABLE_TJPGD_FAST
#include "../vendor/tjpgd_fast/tjpgd.h"
#endif

namespace mvb {
class Rgb565StripCollector {
  alignas(4) uint16_t pixels_[320 * 16];
  TileRenderer *renderer_ = nullptr;
  ProfileClock *clock_ = nullptr;
  unsigned width_ = 0, height_ = 0, completed_y_ = 0, next_x_ = 0,
           strip_height_ = 0;
  bool draw_ = false, big_endian_ = false, failed_ = false,
       draw_failed_ = false;
  uint64_t draw_us_ = 0, copy_us_ = 0;

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
    draw_us_ = copy_us_ = 0;
    draw_failed_ = false;
    failed_ = width == 0 || width > 320 || height == 0 || height > 240;
    return !failed_;
  }
  bool accept(unsigned left, unsigned right, unsigned top, unsigned bottom,
              const uint16_t *rgb) {
    if (failed_ || !rgb || !clock_ || left > right || top > bottom ||
        right >= width_ || bottom >= height_ || top != completed_y_ ||
        left != next_x_ || bottom - top + 1 > 16 ||
        (reinterpret_cast<uintptr_t>(rgb) % alignof(uint16_t)) != 0 ||
        (next_x_ && bottom - top + 1 != strip_height_)) {
      failed_ = true;
      return false;
    }
    if (!next_x_)
      strip_height_ = bottom - top + 1;
    const unsigned used = right - left + 1;
    uint64_t at = clock_->now();
    for (unsigned row = 0; row < strip_height_; row++, rgb += used) {
      uint16_t *dest = pixels_ + row * width_ + left;
      if (!big_endian_)
        std::memcpy(dest, rgb, used * sizeof(uint16_t));
      else
        for (unsigned column = 0; column < used; column++) {
          uint16_t color = rgb[column];
          dest[column] = uint16_t((color >> 8) | (color << 8));
        }
    }
    copy_us_ += clock_->now() - at;
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
        failed_ = draw_failed_ = true;
        return false;
      }
      completed_y_ += strip_height_;
      next_x_ = strip_height_ = 0;
    }
    return true;
  }
  bool complete() const {
    return width_ != 0 && height_ != 0 && !failed_ && completed_y_ == height_ &&
           next_x_ == 0;
  }
  bool failed() const { return failed_; }
  bool draw_failed() const { return draw_failed_; }
  uint64_t draw_us() const { return draw_us_; }
  uint64_t copy_us() const { return copy_us_; }
};

class TjpgdFast {
  Rgb565StripCollector strips_;
  bool prepared_ = false;
#ifdef MVB_ENABLE_TJPGD_FAST
  MvbFastJdec decoder_{};
  alignas(4) uint8_t workspace_[10240];
  const uint8_t *input_ = nullptr;
  size_t length_ = 0, position_ = 0;
  static size_t input(MvbFastJdec *decoder, uint8_t *destination,
                      size_t requested) {
    auto *self = static_cast<TjpgdFast *>(decoder->device);
    if (!self || self->position_ > self->length_)
      return 0;
    size_t count = std::min(requested, self->length_ - self->position_);
    if (destination && count)
      std::memcpy(destination, self->input_ + self->position_, count);
    self->position_ += count;
    return count;
  }
  static int output(MvbFastJdec *decoder, void *bitmap,
                    MvbFastJrect *rectangle) {
    auto *self = static_cast<TjpgdFast *>(decoder->device);
    if (!self || !rectangle)
      return 0;
    return self->strips_.accept(rectangle->left, rectangle->right,
                                rectangle->top, rectangle->bottom,
                                static_cast<const uint16_t *>(bitmap))
               ? 1
               : 0;
  }
#endif

public:
  static constexpr bool available() {
#ifdef MVB_ENABLE_TJPGD_FAST
    return true;
#else
    return false;
#endif
  }
  bool prepare(const uint8_t *bytes, size_t length, TileRenderer &renderer,
               ProfileClock &clock, bool draw_image, bool big_endian) {
    prepared_ = false;
#ifdef MVB_ENABLE_TJPGD_FAST
    if (!bytes || !length)
      return false;
    input_ = bytes;
    length_ = length;
    position_ = 0;
    if (mvb_tjpgd_prepare(&decoder_, input, workspace_, sizeof(workspace_),
                          this) != MVB_FAST_JDR_OK ||
        (draw_image && (decoder_.width != unsigned(renderer.source_width) ||
                        decoder_.height != unsigned(renderer.source_height))))
      return false;
    prepared_ = strips_.begin(renderer, clock, decoder_.width, decoder_.height,
                              draw_image, big_endian);
    return prepared_;
#else
    (void)bytes;
    (void)length;
    (void)renderer;
    (void)clock;
    (void)draw_image;
    (void)big_endian;
    return false;
#endif
  }
  bool decode() {
#ifdef MVB_ENABLE_TJPGD_FAST
    if (!prepared_) return false;
    prepared_ = false;
    return mvb_tjpgd_decomp(&decoder_, output, 0) == MVB_FAST_JDR_OK &&
           strips_.complete();
#else
    return false;
#endif
  }
  bool draw_failed() const { return strips_.draw_failed(); }
  uint64_t draw_us() const { return strips_.draw_us(); }
  // YCbCr-to-RGB565 conversion is fused inside software decomposition. Strip
  // copies/optional byte swaps remain in decode time, separately observable.
  uint64_t color_us() const { return 0; }
  uint64_t copy_us() const { return strips_.copy_us(); }
  size_t workspace_used() const {
#ifdef MVB_ENABLE_TJPGD_FAST
    return sizeof(workspace_) - decoder_.sz_pool;
#else
    return 0;
#endif
  }
};
} // namespace mvb
