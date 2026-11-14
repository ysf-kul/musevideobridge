#pragma once
#include "render.hpp"
#include <climits>
#include <cstddef>
#include <cstring>

#ifdef ARDUINO_ARCH_ESP32
#include <sdkconfig.h>
#endif
#if defined(MVB_ENABLE_NEW_JPEG) && defined(ARDUINO_ARCH_ESP32) &&             \
    defined(CONFIG_IDF_TARGET_ESP32)
#include "../vendor/esp_new_jpeg/include/esp_jpeg_dec.h"
#define MVB_HAS_NEW_JPEG 1
#else
#define MVB_HAS_NEW_JPEG 0
#endif

namespace mvb {
// Optional classic-ESP32 esp_new_jpeg adapter. The codec produces complete
// RGB565 scanline blocks; no full RGB frame or callback color copy is needed.
// Explicit two-block lookahead retains one owned first strip; ordinary
// decoding and one-block lookahead allocate no additional RGB strip.
// Source dimensions must be multiples of eight in the codec's block mode.
class NewJpeg {
  TileRenderer *renderer_ = nullptr;
  ProfileClock *clock_ = nullptr;
  bool prepared_ = false, draw_ = false, draw_failed_ = false;
  uint64_t draw_us_ = 0;
  uint64_t handle_open_calls_ = 0, first_open_us_ = 0, first_decode_us_ = 0;
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
  uint64_t first_copy_us_ = 0;
  unsigned warmup_blocks_ = 1;
#endif
#if MVB_HAS_NEW_JPEG
  static constexpr size_t MaxStripBytes = 320 * 16 * sizeof(uint16_t);
  jpeg_dec_handle_t handle_ = nullptr;
  jpeg_dec_io_t io_{};
  jpeg_dec_header_info_t info_{};
  uint8_t *pixels_ = nullptr;
  size_t capacity_ = 0;
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
  PixelLease output_lease_{};
  bool direct_output_ = false;
  void release_output() {
    if (output_lease_ && renderer_)
      renderer_->release_output_lease(output_lease_);
  }
#endif
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
  uint8_t *first_pixels_ = nullptr;
  size_t first_capacity_ = 0;
#endif
  int output_bytes_ = 0, process_count_ = 0;
  bool configured_big_endian_ = false;
  bool partial_ = false;
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
  unsigned cached_blocks_ = 0, first_visible_ = 0, last_visible_ = 0;
#else
  bool first_block_ready_ = false;
#endif
  const uint8_t *prepared_input_ = nullptr;
  size_t prepared_length_ = 0;

  bool process_block(unsigned y, int block, unsigned &visible) {
    const unsigned row_bytes = unsigned(info_.width) * sizeof(uint16_t);
    if (y >= info_.height)
      return codec_failure();
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
    if (direct_output_) {
      // Each process call may overwrite its whole output, including padded
      // final rows. Acquire an exclusive, completed strip before that call.
      if (output_lease_ ||
          !renderer_->acquire_output_lease(size_t(output_bytes_) / 2,
                                           output_lease_) ||
          !output_lease_ ||
          output_lease_.capacity_pixels < size_t(output_bytes_) / 2 ||
          (reinterpret_cast<uintptr_t>(output_lease_.pixels) & 15))
        return codec_failure();
      io_.outbuf = reinterpret_cast<uint8_t *>(output_lease_.pixels);
    }
#endif
    io_.out_size = 0;
    if (jpeg_dec_process(handle_, &io_) != JPEG_ERR_OK || io_.out_size <= 0 ||
        io_.out_size > output_bytes_ || unsigned(io_.out_size) % row_bytes)
      return codec_failure();
    const unsigned lines = unsigned(io_.out_size) / row_bytes;
    if (!lines || lines > 16 ||
        (lines > unsigned(info_.height) - y && block + 1 != process_count_))
      return codec_failure();
    visible = std::min(lines, unsigned(info_.height) - y);
    return true;
  }

  void close_handle() {
    if (handle_)
      jpeg_dec_close(handle_);
    handle_ = nullptr;
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
    // Close private codec state before making an abandoned output writable.
    release_output();
    direct_output_ = false;
#endif
    prepared_ = false;
    partial_ = false;
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
    cached_blocks_ = 0;
#else
    first_block_ready_ = false;
#endif
  }
  bool codec_failure() {
    // A failed parse/process may leave private decoder state inconsistent.
    // Reopen after that failure; keep the independent output allocation.
    close_handle();
    return false;
  }
  bool open_handle(bool big_endian) {
    if (handle_ && configured_big_endian_ != big_endian)
      close_handle();
    if (handle_)
      return true;
    jpeg_dec_config_t config{};
    config.output_type =
        big_endian ? JPEG_PIXEL_FORMAT_RGB565_BE : JPEG_PIXEL_FORMAT_RGB565_LE;
    config.rotate = JPEG_ROTATE_0D;
    config.block_enable = true;
    ++handle_open_calls_;
    if (jpeg_dec_open(&config, &handle_) != JPEG_ERR_OK || !handle_)
      return codec_failure();
    configured_big_endian_ = big_endian;
    return true;
  }
#endif

public:
  NewJpeg() = default;
  NewJpeg(const NewJpeg &) = delete;
  NewJpeg &operator=(const NewJpeg &) = delete;
  ~NewJpeg() {
#if MVB_HAS_NEW_JPEG
    close_handle();
    jpeg_free_align(pixels_);
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
    jpeg_free_align(first_pixels_);
#endif
#endif
  }
  static constexpr bool two_block_warmup_available() {
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
    return first_block_warmup_available();
#else
    return false;
#endif
  }
  static constexpr bool available() { return MVB_HAS_NEW_JPEG != 0; }
  static constexpr bool direct_output_available() {
#if MVB_HAS_NEW_JPEG && defined(MVB_JPEG_DMA_DIRECT_OUTPUT)
    return true;
#else
    return false;
#endif
  }
  static constexpr bool first_block_warmup_available() {
#if MVB_HAS_NEW_JPEG && defined(MVB_JPEG_FIRST_BLOCK_WARMUP)
    return true;
#else
    return false;
#endif
  }
  // There is no SDK partial-picture reset API. Abandoning a prepared/partly
  // processed picture closes its private codec state before input recycling.
  bool discard_warmup() {
#if MVB_HAS_NEW_JPEG
    bool abandoned = partial_;
    if (abandoned)
      close_handle();
    return abandoned;
#else
    return false;
#endif
  }
  bool configure_warmup_blocks(unsigned blocks) {
    if (blocks != 1 && blocks != 2)
      return false;
    discard_warmup();
#if MVB_HAS_NEW_JPEG && defined(MVB_JPEG_TWO_BLOCK_WARMUP)
    if (blocks == 2) {
      if (!two_block_warmup_available() || !capacity_)
        return false;
      if (!first_pixels_) {
        // Configuration only: reserve the bounded largest supported strip so
        // no future picture needs a lookahead allocation or growth.
        auto *next =
            static_cast<uint8_t *>(jpeg_calloc_align(MaxStripBytes, 16));
        if (!next)
          return false;
        first_pixels_ = next;
        first_capacity_ = MaxStripBytes;
      }
    } else {
      jpeg_free_align(first_pixels_);
      first_pixels_ = nullptr;
      first_capacity_ = 0;
    }
#else
    if (blocks == 2)
      return false;
#endif
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
    warmup_blocks_ = blocks;
#endif
    return true;
  }
  bool prepare(const uint8_t *bytes, size_t length, TileRenderer &renderer,
               ProfileClock &clock, bool draw_image, bool big_endian,
               bool lease_output = false) {
    discard_warmup();
    prepared_ = false;
    draw_failed_ = false;
    draw_us_ = 0;
    first_open_us_ = first_decode_us_ = 0;
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
    first_copy_us_ = 0;
#endif
    renderer_ = &renderer;
    clock_ = &clock;
    draw_ = draw_image;
#if MVB_HAS_NEW_JPEG
    if (!bytes || !length || length > size_t(INT_MAX))
      return false;
    if (!open_handle(big_endian))
      return false;
    io_ = {};
    info_ = {};
    io_.inbuf = const_cast<uint8_t *>(bytes);
    io_.inbuf_len = int(length);
    if (jpeg_dec_parse_header(handle_, &io_, &info_) != JPEG_ERR_OK ||
        !info_.width || info_.width > 320 || !info_.height ||
        info_.height > 240 || info_.width % 8 || info_.height % 8 ||
        (draw_image && (info_.width != unsigned(renderer.source_width) ||
                        info_.height != unsigned(renderer.source_height))))
      return codec_failure();
    output_bytes_ = process_count_ = 0;
    if (jpeg_dec_get_outbuf_len(handle_, &output_bytes_) != JPEG_ERR_OK ||
        output_bytes_ <= 0 || size_t(output_bytes_) > MaxStripBytes ||
        output_bytes_ % (unsigned(info_.width) * sizeof(uint16_t)) ||
        jpeg_dec_get_process_count(handle_, &process_count_) != JPEG_ERR_OK ||
        process_count_ <= 0 || process_count_ > info_.height / 8)
      return codec_failure();
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
    direct_output_ = big_endian && (draw_image || lease_output) &&
                     renderer.direct_output_eligible()
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
                     && warmup_blocks_ == 1
#endif
        ;
    if (direct_output_) {
      // Switching modes happens outside jpeg_dec_process. The codec output
      // strip is redundant once the three bounded display strips are leased.
      jpeg_free_align(pixels_);
      pixels_ = nullptr;
      capacity_ = 0;
    } else {
#else
    (void)lease_output;
#endif
      if (capacity_ < size_t(output_bytes_)) {
        auto *next =
            static_cast<uint8_t *>(jpeg_calloc_align(output_bytes_, 16));
        if (!next)
          return codec_failure();
        jpeg_free_align(pixels_);
        pixels_ = next;
        capacity_ = size_t(output_bytes_);
      }
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
    }
#endif
    io_.outbuf = pixels_;
    prepared_input_ = bytes;
    prepared_length_ = length;
    prepared_ = true;
    partial_ = true;
    return true;
#else
    (void)bytes;
    (void)length;
    (void)big_endian;
    (void)lease_output;
    return false;
#endif
  }
  bool warm_first_block(const uint8_t *bytes, size_t length,
                        TileRenderer &renderer, ProfileClock &clock,
                        bool big_endian) {
#if MVB_HAS_NEW_JPEG && defined(MVB_JPEG_FIRST_BLOCK_WARMUP)
    const uint64_t start = clock.now();
    if (!prepare(bytes, length, renderer, clock, false, big_endian, true))
      return false;
    first_open_us_ = clock.now() - start;
    if (info_.width != unsigned(renderer.source_width) ||
        info_.height != unsigned(renderer.source_height))
      return codec_failure();
    const uint64_t decode_start = clock.now();
    unsigned visible = 0;
    if (!process_block(0, 0, visible))
      return false;
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
    first_visible_ = visible;
    const unsigned count = std::min(warmup_blocks_, unsigned(process_count_));
    if (count == 2) {
      if (!first_pixels_ || first_capacity_ < size_t(io_.out_size))
        return codec_failure();
      const uint64_t copy_start = clock.now();
      std::memcpy(first_pixels_, pixels_, size_t(io_.out_size));
      first_copy_us_ = clock.now() - copy_start;
      if (!process_block(visible, 1, visible))
        return false;
    }
    last_visible_ = visible;
    cached_blocks_ = count;
#else
    first_block_ready_ = true;
#endif
    first_decode_us_ = clock.now() - decode_start;
    return true;
#else
    (void)bytes;
    (void)length;
    (void)renderer;
    (void)clock;
    (void)big_endian;
    return false;
#endif
  }
  bool resume_first_block(const uint8_t *bytes, size_t length,
                          TileRenderer &renderer, ProfileClock &clock,
                          bool big_endian) {
#if MVB_HAS_NEW_JPEG && defined(MVB_JPEG_FIRST_BLOCK_WARMUP)
    if (!prepared_ ||
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
        !cached_blocks_ ||
#else
        !first_block_ready_ ||
#endif
        !handle_ || bytes != prepared_input_ || length != prepared_length_ ||
        configured_big_endian_ != big_endian ||
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
        (direct_output_ && !renderer.direct_output_eligible()) ||
#endif
        info_.width != unsigned(renderer.source_width) ||
        info_.height != unsigned(renderer.source_height))
      return false;
    renderer_ = &renderer;
    clock_ = &clock;
    draw_ = true;
    draw_us_ = 0;
    draw_failed_ = false;
    return true;
#else
    (void)bytes;
    (void)length;
    (void)renderer;
    (void)clock;
    (void)big_endian;
    return false;
#endif
  }
  bool decode() {
#if MVB_HAS_NEW_JPEG
    if (!prepared_ || !handle_ ||
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
        (!pixels_ && !direct_output_)
#else
        !pixels_
#endif
    )
      return false;
    prepared_ = false; // Every picture requires its own checked header.
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
    const unsigned cached = cached_blocks_;
    cached_blocks_ = 0;
#endif
    unsigned y = 0;
    for (int block = 0; block < process_count_; block++) {
      unsigned visible = 0;
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
      uint8_t *present = pixels_;
      if (unsigned(block) < cached) {
        // With two cached blocks, block zero has its independent owned copy
        // and block one remains in codec output. Submit both exactly once
        // before another process() can overwrite that output.
        const bool copied_first = cached == 2 && block == 0;
        visible = copied_first ? first_visible_ : last_visible_;
        present = copied_first ? first_pixels_ : pixels_;
      } else if (!process_block(y, block, visible))
        return false;
#else
      if (block == 0 && first_block_ready_) {
        first_block_ready_ = false;
        visible =
            std::min(unsigned(io_.out_size) /
                         (unsigned(info_.width) * unsigned(sizeof(uint16_t))),
                     unsigned(info_.height));
      } else if (!process_block(y, block, visible))
        return false;
#endif
      if (draw_) {
        const uint64_t at = clock_->now();
        const bool ok =
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
            direct_output_ ? renderer_->submit_output_lease(
                                 int(y), int(visible), output_lease_)
                           :
#endif
                           renderer_->tile(0, int(y), int(info_.width),
                                           int(info_.width), int(visible),
                                           reinterpret_cast<uint16_t *>(
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
                                               present
#else
                                pixels_
#endif
                                               ));
        draw_us_ += clock_->now() - at;
        if (!ok) {
          draw_failed_ = true;
          return codec_failure();
        }
      }
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
      else if (direct_output_)
        release_output();
#endif
      y += visible;
    }
    if (y != info_.height)
      return codec_failure();
    partial_ = false;
    return true;
#else
    return false;
#endif
  }
  bool draw_failed() const { return draw_failed_; }
  uint64_t draw_us() const { return draw_us_; }
  // RGB565 conversion is inside jpeg_dec_process and therefore included in
  // codec decode timing. There is no separate adapter color conversion stage.
  uint64_t color_us() const { return 0; }
  uint64_t handle_open_calls() const { return handle_open_calls_; }
  uint64_t first_block_open_us() const { return first_open_us_; }
  uint64_t first_block_decode_us() const { return first_decode_us_; }
  uint64_t copy_us() const {
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
    return first_copy_us_;
#else
    return 0;
#endif
  }
  unsigned warmup_blocks() const {
#ifdef MVB_JPEG_TWO_BLOCK_WARMUP
    return warmup_blocks_;
#else
    return 1;
#endif
  }
  unsigned cached_blocks() const {
#if MVB_HAS_NEW_JPEG && defined(MVB_JPEG_TWO_BLOCK_WARMUP)
    return cached_blocks_;
#elif MVB_HAS_NEW_JPEG
    return first_block_ready_ ? 1 : 0;
#else
    return 0;
#endif
  }
  size_t warmup_cache_capacity() const {
#if MVB_HAS_NEW_JPEG && defined(MVB_JPEG_TWO_BLOCK_WARMUP)
    return first_capacity_;
#else
    return 0;
#endif
  }
  size_t output_capacity() const {
#if MVB_HAS_NEW_JPEG
    return capacity_;
#else
    return 0;
#endif
  }
};
} // namespace mvb
