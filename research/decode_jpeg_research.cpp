// Host actual-JPEGDEC verification of bounded trusted encoder streams.
// Never opens UART or claims hardware FPS; keeps one compressed/decoded frame.
#include "JPEGDEC.h"
#include "wire.hpp"
#ifdef MVB_COMPARE_TJPGD_FAST
#include "jpeg_validation.hpp"
#include "tjpgd_fast.hpp"
#endif
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <vector>

namespace {
constexpr size_t Pixels = 320 * 240;
int image_width = 0, image_height = 0;
size_t image_pixels = 0;
std::array<uint16_t, Pixels> image;
std::array<uint8_t, Pixels> coverage;
bool invalid = false;
uint64_t count = 0;
int draw(JPEGDRAW *tile) {
  if (tile->x < 0 || tile->y < 0 || tile->iWidthUsed < 1 || tile->iHeight < 1 ||
      tile->x + tile->iWidthUsed > image_width ||
      tile->y + tile->iHeight > image_height ||
      tile->iWidth < tile->iWidthUsed) {
    invalid = true;
    return 0;
  }
  for (int y = 0; y < tile->iHeight; y++)
    for (int x = 0; x < tile->iWidthUsed; x++) {
      size_t offset = size_t(tile->y + y) * size_t(image_width) + tile->x + x;
      if (coverage[offset]++)
        invalid = true;
      image[offset] = tile->pPixels[y * tile->iWidth + x];
      count++;
    }
  return invalid ? 0 : 1;
}
#ifdef MVB_COMPARE_TJPGD_FAST
class ResearchClock : public mvb::ProfileClock {
public:
  uint64_t now() override {
    return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::steady_clock::now().time_since_epoch())
                        .count());
  }
};
class ResearchPainter : public mvb::Painter {
public:
  bool row(int x, int y, const uint16_t *pixels, int n) override {
    return block(x, y, pixels, n, 1, n);
  }
  bool block(int x, int y, const uint16_t *pixels, int w, int h,
             int stride) override {
    JPEGDRAW tile{};
    tile.x = x;
    tile.y = y;
    tile.iWidthUsed = w;
    tile.iWidth = stride;
    tile.iHeight = h;
    tile.pPixels = const_cast<uint16_t *>(pixels);
    return draw(&tile) != 0;
  }
  void clear(mvb::Rect) override {}
  void waiting(bool, bool, const char *) override {}
};
// Same output oracle and stream framing, exercising the real software helper.
// This does not substitute for the precompiled Espressif codec on the ESP32.
class ResearchDecoder {
  mvb::TjpgdFast decoder_;
  ResearchClock clock_;
  ResearchPainter painter_;
  mvb::TileRenderer renderer_{painter_};
  const uint8_t *bytes_ = nullptr;
  size_t length_ = 0;
  uint16_t width_ = 0, height_ = 0;
  bool big_ = false;

public:
  bool openRAM(uint8_t *bytes, int length, int (*)(JPEGDRAW *)) {
    if (length < 0 ||
        !mvb::baseline_jpeg(bytes, size_t(length), width_, height_))
      return false;
    bytes_ = bytes;
    length_ = size_t(length);
    renderer_.source_width = width_;
    renderer_.source_height = height_;
    renderer_.transform = {0, 0, width_, height_, 0, false};
    return true;
  }
  void setPixelType(int type) { big_ = type == RGB565_BIG_ENDIAN; }
  void setMaxOutputSize(int) {}
  int getWidth() const { return width_; }
  int getHeight() const { return height_; }
  bool decode(int x, int y, int flags) {
    return x == 0 && y == 0 && flags == 0 &&
           decoder_.prepare(bytes_, length_, renderer_, clock_, true, big_) &&
           decoder_.decode();
  }
  void close() {}
};
constexpr const char *Codec = "host_actual_software_TJpgDec_not_device_FPS";
#else
using ResearchDecoder = JPEGDEC;
constexpr const char *Codec = "host_actual_JPEGDEC_not_device_FPS";
#endif
bool frame(std::ifstream &stream, std::vector<uint8_t> &out) {
  out.clear();
  int first = stream.get();
  if (first == EOF)
    return false;
  out.push_back(uint8_t(first));
  auto byte = [&]() {
    int value = stream.get();
    if (value == EOF || out.size() >= mvb::MaxJpeg)
      throw std::runtime_error("truncated/oversized JPEG");
    out.push_back(uint8_t(value));
    return uint8_t(value);
  };
  if (first != 255 || byte() != 0xd8)
    throw std::runtime_error("missing SOI");
  for (;;) {
    if (byte() != 255)
      throw std::runtime_error("bad marker");
    uint8_t marker;
    do {
      marker = byte();
    } while (marker == 255);
    uint16_t length = uint16_t(byte()) << 8;
    length |= byte();
    if (length < 2)
      throw std::runtime_error("bad segment length");
    for (uint16_t n = 2; n < length; n++)
      byte();
    if (marker == 0xda)
      break;
  }
  for (;;) {
    if (byte() != 255)
      continue;
    uint8_t marker;
    do {
      marker = byte();
    } while (marker == 255);
    if (marker == 0 || (marker >= 0xd0 && marker <= 0xd7))
      continue;
    if (marker == 0xd9)
      return true;
    throw std::runtime_error("unexpected entropy marker");
  }
}
} // namespace

int main(int argc, char **argv) {
  if (argc != 2 && argc != 3)
    return 2;
  try {
    std::ifstream stream(argv[1], std::ios::binary);
    if (!stream)
      return 3;
    std::vector<uint8_t> bytes;
    bytes.reserve(mvb::MaxJpeg);
    ResearchDecoder decoder;
    ResearchDecoder other_decoder;
    std::ifstream other;
    if (argc == 3)
      other.open(argv[2], std::ios::binary);
    std::vector<uint8_t> other_bytes;
    other_bytes.reserve(mvb::MaxJpeg);
    std::array<uint16_t, Pixels> first_image{}, reused_image{};
    uint64_t differing_pixels = 0, differing_frames = 0,
             fresh_reuse_differences = 0;
    unsigned maximum_channel_step = 0;
    uint32_t frames = 0, first_crc = 0, crc = 0xffffffffu;
    auto started = std::chrono::steady_clock::now();
    while (frame(stream, bytes)) {
      image.fill(0);
      coverage.fill(0);
      count = 0;
      invalid = false;
      if (!decoder.openRAM(bytes.data(), int(bytes.size()), draw)) {
        std::printf("{\"stage\":\"JPEGDEC_open\",\"rejected_frame\":%u,"
                    "\"hardware_tested\":false}\n",
                    frames);
        return 4;
      }
      decoder.setPixelType(RGB565_BIG_ENDIAN);
      decoder.setMaxOutputSize(20);
      const int w = decoder.getWidth(), h = decoder.getHeight();
      if (w < 1 || w > 320 || h < 1 || h > 240 ||
          (frames && (w != image_width || h != image_height)))
        return 5;
      image_width = w;
      image_height = h;
      image_pixels = size_t(w) * size_t(h);
      bool ok = decoder.decode(0, 0, 0);
      decoder.close();
      if (!ok || invalid || count != image_pixels ||
          std::any_of(coverage.begin(), coverage.begin() + image_pixels,
                      [](uint8_t value) { return value != 1; })) {
        std::printf("{\"stage\":\"JPEGDEC_decode_or_coverage\",\"rejected_"
                    "frame\":%u,\"hardware_tested\":false}\n",
                    frames);
        return 5;
      }
      auto pixels = reinterpret_cast<const uint8_t *>(image.data());
      if (!frames)
        first_crc = mvb::crc32(pixels, image_pixels * 2);
      crc = mvb::crc_update(crc, pixels, image_pixels * 2);
      if (argc == 3) {
        first_image = image;
        if (!frame(other, other_bytes))
          return 8;
        auto decode_other = [&](ResearchDecoder &candidate,
                                std::vector<uint8_t> &input) {
          image.fill(0);
          coverage.fill(0);
          count = 0;
          invalid = false;
          if (!candidate.openRAM(input.data(), int(input.size()), draw))
            return false;
          candidate.setPixelType(RGB565_BIG_ENDIAN);
          candidate.setMaxOutputSize(20);
          bool success = candidate.getWidth() == image_width &&
                         candidate.getHeight() == image_height &&
                         candidate.decode(0, 0, 0);
          candidate.close();
          return success && !invalid && count == image_pixels;
        };
        ResearchDecoder fresh_baseline;
        if (!decode_other(fresh_baseline, bytes))
          return 10;
        for (size_t p = 0; p < image_pixels; p++)
          if (image[p] != first_image[p])
            fresh_reuse_differences++;
        if (!decode_other(other_decoder, other_bytes))
          return 9;
        uint64_t differences = 0;
        for (size_t p = 0; p < image_pixels; p++) {
          if (image[p] == first_image[p])
            continue;
          differences++;
          uint16_t a = uint16_t(image[p] << 8 | image[p] >> 8);
          uint16_t b = uint16_t(first_image[p] << 8 | first_image[p] >> 8);
          for (unsigned shift : {0u, 5u, 11u}) {
            unsigned mask = shift == 5 ? 63 : 31;
            int difference =
                int((a >> shift) & mask) - int((b >> shift) & mask);
            maximum_channel_step =
                std::max(maximum_channel_step, unsigned(std::abs(difference)));
          }
        }
        differing_pixels += differences;
        if (differences)
          differing_frames++;
        reused_image = image;
        ResearchDecoder fresh;
        if (!decode_other(fresh, other_bytes))
          return 10;
        for (size_t p = 0; p < image_pixels; p++)
          if (image[p] != reused_image[p])
            fresh_reuse_differences++;
      }
      frames++;
    }
    if (argc == 3 && frame(other, other_bytes))
      return 8;
    double seconds = std::chrono::duration<double>(
                         std::chrono::steady_clock::now() - started)
                         .count();
    std::printf("{\"scope\":\"%s\",\"frames\":%u,\"width\":%d,\"height\":%d,"
                "\"pixels_per_frame\":%zu,"
                "\"all_pixels_exactly_once\":true,\"first_rgb565_crc32\":%u,"
                "\"all_frames_rgb565_crc32\":%u,\"paired_different_pixels\":%"
                "llu,\"paired_different_frames\":%llu,\"maximum_RGB565_channel_"
                "step\":%u,\"fresh_vs_reused_different_pixels\":%llu,\"host_"
                "seconds\":%.9f,\"hardware_tested\":false}\n",
                Codec, frames, image_width, image_height, image_pixels,
                first_crc, crc ^ 0xffffffffu,
                (unsigned long long)differing_pixels,
                (unsigned long long)differing_frames, maximum_channel_step,
                (unsigned long long)fresh_reuse_differences, seconds);
    return frames == 360 ? 0 : 6;
  } catch (const std::exception &error) {
    std::fprintf(stderr, "%s\n", error.what());
    return 7;
  }
}
