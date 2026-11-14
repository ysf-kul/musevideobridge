#pragma once
#include "profiling.hpp"
#include <algorithm>
#include <cstddef>
#include <cstdint>

namespace mvb {
struct Transform {
  int x = 0, y = 0, width = 320, height = 240, rotation = 0;
  bool lock_aspect = true;
  bool valid() const {
    return x >= -640 && x <= 640 && y >= -480 && y <= 480 && width >= 1 &&
           width <= 640 && height >= 1 && height <= 480 &&
           (rotation == 0 || rotation == 90 || rotation == 180 ||
            rotation == 270);
  }
};
struct Rect {
  int x = 0, y = 0, w = 0, h = 0;
};
// Exclusive codec-write lease. Submission transfers ownership to the display;
// cancellation returns an unsubmitted strip. Neither operation frees storage.
struct PixelLease {
  uint16_t *pixels = nullptr;
  size_t capacity_pixels = 0;
  unsigned slot = unsigned(-1);
  uint32_t token = 0;
  explicit operator bool() const { return pixels && token; }
};
class Painter {
public:
  virtual ~Painter() = default;
  virtual void begin_frame() {}
  virtual void begin_image(Rect) { begin_frame(); }
  virtual bool end_frame() { return true; }
  virtual bool big_endian() const { return false; }
  virtual DisplayProfile display_profile() const { return {}; }
  // When armed, begin/end frame and row/block must compare existing display
  // memory without writing pixels. Call end after every successful begin,
  // including when decoding or a comparison callback fails.
  virtual bool begin_integrity_check() { return false; }
  virtual IntegrityProfile end_integrity_check() { return {}; }
  virtual bool configure(const RenderOptions &) { return false; }
  virtual bool configured_render_options(RenderOptions &) const { return false; }
  virtual bool output_leases_available() const { return false; }
  virtual bool acquire_output_lease(size_t, PixelLease &) { return false; }
  virtual bool release_output_lease(PixelLease &) { return false; }
  virtual bool leased_block(int, int, int, int, PixelLease &) { return false; }
  virtual bool row(int x, int y, const uint16_t *pixels, int count) = 0;
  virtual bool block(int x, int y, const uint16_t *pixels, int width,
                     int height, int stride) {
    for (int line = 0; line < height; line++)
      if (!row(x, y + line, pixels + line * stride, width))
        return false;
    return true;
  }
  virtual void clear(Rect) = 0;
  virtual void waiting(bool card, bool connected, const char *error) = 0;
  virtual void preparation(const char *, int) {}
  virtual void controls(bool, bool, bool) {}
};
inline Rect clipped(const Transform &t) {
  int l = std::max(0, t.x), r = std::min(320, t.x + t.width),
      top = std::max(0, t.y), bottom = std::min(240, t.y + t.height);
  return {l, top, std::max(0, r - l), std::max(0, bottom - top)};
}
inline void clear_difference(Painter &p, Rect old, Rect next) {
  int l = std::max(old.x, next.x), r = std::min(old.x + old.w, next.x + next.w),
      top = std::max(old.y, next.y),
      bottom = std::min(old.y + old.h, next.y + next.h);
  if (l >= r || top >= bottom) {
    p.clear(old);
    return;
  }
  p.clear({old.x, old.y, old.w, top - old.y});
  p.clear({old.x, bottom, old.w, old.y + old.h - bottom});
  p.clear({old.x, top, l - old.x, bottom - top});
  p.clear({r, top, old.x + old.w - r, bottom - top});
}
inline int sample(int pixel, int source, int dest) {
  return std::min(source - 1, ((2 * pixel + 1) * source) / (2 * dest));
}
inline int lower_sample(int source_coord, int source_size, int dest_size) {
  int l = 0, r = dest_size;
  while (l < r) {
    int m = (l + r) / 2;
    if (sample(m, source_size, dest_size) < source_coord)
      l = m + 1;
    else
      r = m;
  }
  return l;
}
inline void inverse(int u, int v, int w, int h, int rotation, int &x, int &y) {
  switch (rotation) {
  case 90:
    x = v;
    y = h - 1 - u;
    break;
  case 180:
    x = w - 1 - u;
    y = h - 1 - v;
    break;
  case 270:
    x = w - 1 - v;
    y = u;
    break;
  default:
    x = u;
    y = v;
  }
}
class TileRenderer {
  Painter &painter_;
  uint16_t row_[320]{};

public:
  Transform transform;
  bool native_blocks = true;
  int source_width = 0, source_height = 0;
  int viewport_height = 240;
  explicit TileRenderer(Painter &p) : painter_(p) {}
  bool direct_output_eligible() const {
    return native_blocks && transform.valid() && source_width > 0 &&
           source_height > 0 && transform.rotation == 0 &&
           transform.width == source_width &&
           transform.height == source_height && transform.x >= 0 &&
           transform.y >= 0 && transform.x + source_width <= 320 &&
           transform.y + source_height <= 240 &&
           painter_.output_leases_available();
  }
  bool acquire_output_lease(size_t pixels, PixelLease &lease) {
    return direct_output_eligible() &&
           painter_.acquire_output_lease(pixels, lease);
  }
  bool release_output_lease(PixelLease &lease) {
    return painter_.release_output_lease(lease);
  }
  bool submit_output_lease(int y, int height, PixelLease &lease) {
    if (transform.y + y >= viewport_height)
      return painter_.release_output_lease(lease);
    height = std::min(height, viewport_height - transform.y - y);
    return direct_output_eligible() && y >= 0 && height > 0 &&
           y + height <= source_height &&
           painter_.leased_block(transform.x, transform.y + y, source_width,
                                 height, lease);
  }
  bool tile(int x, int y, int stride, int used, int height,
            const uint16_t *pixels) {
    if (!transform.valid() || !source_width || !source_height || x < 0 ||
        y < 0 || used < 0 || used > stride || height < 0 || !pixels)
      return false;
    int sw = source_width, sh = source_height;
    used = std::min(used, sw - x);
    height = std::min(height, sh - y);
    if (used <= 0 || height <= 0)
      return true;
    // Native-size playback can pass decoder tiles directly to the display.
    // This preserves every RGB565 pixel and avoids per-pixel resampling and
    // a separate SPI transaction for each row.
    if (native_blocks && transform.rotation == 0 && transform.width == sw &&
        transform.height == sh) {
      int left = std::max(0, transform.x + x);
      int top = std::max(0, transform.y + y);
      int right = std::min(320, transform.x + x + used);
      int bottom = std::min(viewport_height, transform.y + y + height);
      if (left >= right || top >= bottom)
        return true;
      const uint16_t *start =
          pixels + (top - transform.y - y) * stride + left - transform.x - x;
      return painter_.block(left, top, start, right - left, bottom - top,
                            stride);
    }
    int rw = sw, rh = sh, u0 = x, u1 = x + used, v0 = y, v1 = y + height;
    switch (transform.rotation) {
    case 90:
      rw = sh;
      rh = sw;
      u0 = sh - y - height;
      u1 = sh - y;
      v0 = x;
      v1 = x + used;
      break;
    case 180:
      u0 = sw - x - used;
      u1 = sw - x;
      v0 = sh - y - height;
      v1 = sh - y;
      break;
    case 270:
      rw = sh;
      rh = sw;
      u0 = y;
      u1 = y + height;
      v0 = sw - x - used;
      v1 = sw - x;
      break;
    }
    int l = std::max(lower_sample(u0, rw, transform.width), -transform.x);
    int r = std::min(lower_sample(u1, rw, transform.width), 320 - transform.x);
    int top = std::max(lower_sample(v0, rh, transform.height), -transform.y);
    int bottom =
        std::min(lower_sample(v1, rh, transform.height), viewport_height - transform.y);
    if (l >= r || top >= bottom)
      return true;
    for (int dy = top; dy < bottom; dy++) {
      for (int dx = l; dx < r; dx++) {
        int sx, sy;
        inverse(sample(dx, rw, transform.width),
                sample(dy, rh, transform.height), sw, sh, transform.rotation,
                sx, sy);
        if (sx < x || sx >= x + used || sy < y || sy >= y + height)
          return false;
        row_[dx - l] = pixels[(sy - y) * stride + sx - x];
      }
      if (!painter_.row(transform.x + l, transform.y + dy, row_, r - l))
        return false;
    }
    return true;
  }
};
} // namespace mvb
