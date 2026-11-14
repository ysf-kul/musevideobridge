"""Optional decoder adapter tests use a host codec substitute, not real JPEG decoding."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")
SOURCE = r"""
#include "new_jpeg.hpp"
#include <cassert>
#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

class Clock : public mvb::ProfileClock {
public:
  uint64_t ticks = 0;
  uint64_t now() override { return ticks++; }
};
class Canvas : public mvb::Painter {
public:
  Clock &clock;
  std::vector<uint16_t> pixels = std::vector<uint16_t>(320 * 240);
  std::vector<unsigned> writes = std::vector<unsigned>(320 * 240);
  unsigned blocks = 0, rows = 0;
  bool reject = false;
  explicit Canvas(Clock &c) : clock(c) {}
  bool row(int x, int y, const uint16_t *p, int n) override {
    ++rows; clock.ticks += 5;
    if (reject) return false;
    assert(x >= 0 && y >= 0 && n > 0 && x + n <= 320 && y < 240);
    for (int i = 0; i < n; ++i) {
      pixels[y * 320 + x + i] = p[i];
      ++writes[y * 320 + x + i];
    }
    return true;
  }
  bool block(int x, int y, const uint16_t *p, int w, int h, int stride) override {
    ++blocks; clock.ticks += 7;
    if (reject) return false;
    assert(x >= 0 && y >= 0 && w > 0 && h > 0 && stride >= w);
    assert(x + w <= 320 && y + h <= 240);
    for (int row = 0; row < h; ++row)
      for (int column = 0; column < w; ++column) {
        size_t index = (y + row) * 320 + x + column;
        pixels[index] = p[row * stride + column];
        ++writes[index];
      }
    return true;
  }
  void clear(mvb::Rect) override { assert(false); }
  void waiting(bool, bool, const char *) override { assert(false); }
};
uint16_t color(int x, int y) { return uint16_t(1 + (x + 1) * 31 + (y + 1) * 521); }
std::vector<uint8_t> fixture(unsigned width, unsigned height, uint8_t seed = 0) {
  return {uint8_t(width), uint8_t(width >> 8), uint8_t(height), uint8_t(height >> 8), seed};
}

#if MVB_HAS_NEW_JPEG
struct Faults {
  bool open = false, parse = false, allocate = false, output_query = false,
       count_query = false;
  int queried_size = -999, count = -999, reported_size = -999, process_error = -1;
} faults;
unsigned opens = 0, closes = 0, allocations = 0, frees = 0, processes = 0;
struct Codec {
  jpeg_dec_config_t config;
  unsigned width = 0, height = 0, block = 0;
  uint8_t seed = 0;
  bool partial = false;
};
extern "C" jpeg_error_t jpeg_dec_open(jpeg_dec_config_t *config,
                                     jpeg_dec_handle_t *handle) {
  ++opens;
  assert(config && config->block_enable && config->rotate == JPEG_ROTATE_0D);
  assert(!config->scale.width && !config->scale.height);
  assert(!config->clipper.width && !config->clipper.height);
  if (faults.open) { *handle = nullptr; return JPEG_ERR_NO_MEM; }
  *handle = new Codec{*config};
  return JPEG_ERR_OK;
}
extern "C" jpeg_error_t jpeg_dec_close(jpeg_dec_handle_t handle) {
  assert(handle); ++closes; delete static_cast<Codec *>(handle); return JPEG_ERR_OK;
}
extern "C" void *jpeg_calloc_align(size_t size, int alignment) {
  ++allocations;
  assert(alignment == 16 && size && size <= 320 * 16 * 2);
  if (faults.allocate) return nullptr;
  void *p = std::aligned_alloc(alignment, (size + alignment - 1) / alignment * alignment);
  assert(p); return p;
}
extern "C" void jpeg_free_align(void *data) {
  if (data) { ++frees; std::free(data); }
}
extern "C" jpeg_error_t jpeg_dec_parse_header(jpeg_dec_handle_t handle,
                                             jpeg_dec_io_t *io,
                                             jpeg_dec_header_info_t *info) {
  assert(handle && io && info && io->inbuf && io->inbuf_len >= 4);
  if (faults.parse) return JPEG_ERR_BAD_DATA;
  auto *codec = static_cast<Codec *>(handle);
  // Deliberately reject undocumented partial-header reuse. Abort paths must
  // close/reopen even if parsing completed but processing never started.
  assert(!codec->partial);
  codec->width = unsigned(io->inbuf[0]) + unsigned(io->inbuf[1]) * 256;
  codec->height = unsigned(io->inbuf[2]) + unsigned(io->inbuf[3]) * 256;
  codec->block = 0;
  codec->seed = io->inbuf_len > 4 ? io->inbuf[4] : 0;
  codec->partial = true;
  info->width = codec->width; info->height = codec->height;
  return JPEG_ERR_OK;
}
extern "C" jpeg_error_t jpeg_dec_get_outbuf_len(jpeg_dec_handle_t handle, int *size) {
  auto *codec = static_cast<Codec *>(handle);
  *size = faults.queried_size == -999 ? int(codec->width * 16 * 2) : faults.queried_size;
  return faults.output_query ? JPEG_ERR_FAIL : JPEG_ERR_OK;
}
extern "C" jpeg_error_t jpeg_dec_get_process_count(jpeg_dec_handle_t handle, int *count) {
  auto *codec = static_cast<Codec *>(handle);
  *count = faults.count == -999 ? int((codec->height + 15) / 16) : faults.count;
  return faults.count_query ? JPEG_ERR_FAIL : JPEG_ERR_OK;
}
extern "C" jpeg_error_t jpeg_dec_process(jpeg_dec_handle_t handle, jpeg_dec_io_t *io) {
  ++processes;
  auto *codec = static_cast<Codec *>(handle);
  assert(io->outbuf && reinterpret_cast<uintptr_t>(io->outbuf) % 16 == 0);
  if (int(codec->block) == faults.process_error) return JPEG_ERR_BAD_DATA;
  if (faults.reported_size != -999) { io->out_size = faults.reported_size; return JPEG_ERR_OK; }
  for (unsigned y = 0; y < 16; ++y)
    for (unsigned x = 0; x < codec->width; ++x) {
      const uint16_t pixel = color(x, codec->block * 16 + y) + codec->seed;
      const size_t index = (y * codec->width + x) * 2;
      if (codec->config.output_type == JPEG_PIXEL_FORMAT_RGB565_BE) {
        io->outbuf[index] = uint8_t(pixel >> 8); io->outbuf[index + 1] = uint8_t(pixel);
      } else {
        assert(codec->config.output_type == JPEG_PIXEL_FORMAT_RGB565_LE);
        io->outbuf[index] = uint8_t(pixel); io->outbuf[index + 1] = uint8_t(pixel >> 8);
      }
    }
  io->out_size = codec->width * 16 * 2;
  ++codec->block;
  codec->partial = codec->block < (codec->height + 15) / 16;
  return JPEG_ERR_OK;
}

void reset() {
  faults = {};
  opens = closes = allocations = frees = processes = 0;
}
void oracle(const Canvas &canvas, int width, int height,
            const mvb::Transform &t, bool big_endian, uint8_t seed = 0) {
  int rw = t.rotation % 180 ? height : width, rh = t.rotation % 180 ? width : height;
  for (int y = 0; y < 240; ++y)
    for (int x = 0; x < 320; ++x) {
      int dx = x - t.x, dy = y - t.y;
      bool inside = dx >= 0 && dx < t.width && dy >= 0 && dy < t.height;
      uint16_t expected = 0;
      if (inside) {
        int u = (2 * dx + 1) * rw / (2 * t.width);
        int v = (2 * dy + 1) * rh / (2 * t.height);
        int sx = u, sy = v;
        if (t.rotation == 90) { sx = v; sy = height - 1 - u; }
        if (t.rotation == 180) { sx = width - 1 - u; sy = height - 1 - v; }
        if (t.rotation == 270) { sx = width - 1 - v; sy = u; }
        expected = color(sx, sy) + seed;
        if (big_endian) expected = uint16_t((expected & 255) * 256 + expected / 256);
      }
      size_t index = y * 320 + x;
      assert(canvas.pixels[index] == expected && canvas.writes[index] == unsigned(inside));
    }
}
void native() {
  static_assert(mvb::NewJpeg::available(), "Stub target must enable candidate");
  for (bool big_endian : {false, true})
    for (int width : {8, 24, 312, 320})
      for (int height : {8, 24, 232, 240}) {
        reset(); Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas);
        renderer.source_width = width; renderer.source_height = height;
        renderer.transform = {0, 0, width, height, 0, false};
        auto bytes = fixture(width, height);
        {
          mvb::NewJpeg decoder;
          assert(!decoder.decode());
          assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, big_endian));
          assert(decoder.decode() && !decoder.draw_failed());
          assert(!decoder.decode());
          assert(canvas.blocks == unsigned((height + 15) / 16) && canvas.rows == 0);
          assert(decoder.draw_us() == canvas.blocks * 8 && decoder.color_us() == 0);
          oracle(canvas, width, height, renderer.transform, big_endian);
        }
        assert(opens == 1 && closes == 1 && allocations == 1 && frees == 1);
      }
}
void reuse() {
  reset(); Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas);
  {
    mvb::NewJpeg decoder;
    for (unsigned frame = 0; frame < 10; ++frame) {
      auto bytes = fixture(320, 240);
      assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
      assert(decoder.decode() && !decoder.draw_failed());
      assert(decoder.draw_us() == 0 && decoder.color_us() == 0);
    }
    assert(opens == 1 && allocations == 1 && closes == 0 && frees == 0);
    auto bytes = fixture(8, 8);
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, true));
    assert(decoder.decode());
    assert(opens == 2 && closes == 1 && allocations == 1 && frees == 0);
    assert(canvas.blocks == 0 && canvas.rows == 0);
  }
  assert(opens == closes && allocations == frees);
  reset();
  {
    mvb::NewJpeg decoder;
    for (unsigned width : {8u, 24u, 8u}) {
      auto bytes = fixture(width, 8);
      assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
      assert(decoder.decode());
    }
    assert(opens == 1 && allocations == 2 && frees == 1);
  }
  assert(opens == closes && allocations == frees);
}
void transforms() {
  for (int rotation : {0, 90, 180, 270})
    for (int width : {1, 7, 24, 48, 640})
      for (int height : {1, 5, 24, 48, 480})
        for (int x : {-640, -3, 11, 319, 640}) {
          reset(); Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas);
          renderer.source_width = renderer.source_height = 24;
          renderer.transform = {x, -5, width, height, rotation, false};
          auto bytes = fixture(24, 24); const bool be = (width + height) % 2;
          mvb::NewJpeg decoder;
          assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, be));
          assert(decoder.decode());
          oracle(canvas, 24, 24, renderer.transform, be);
        }
}
void rejected() {
  Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas);
  for (unsigned width : {0u, 1u, 17u, 317u, 321u})
    for (unsigned height : {0u, 1u, 17u, 239u, 241u}) {
      reset(); auto bytes = fixture(width, height); mvb::NewJpeg decoder;
      assert(!decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
      assert(!decoder.decode() && !decoder.draw_failed());
      assert(allocations == 0 && processes == 0);
    }
  reset(); auto bytes = fixture(24, 24);
  mvb::NewJpeg decoder;
  assert(!decoder.prepare(nullptr, 4, renderer, clock, false, false));
  assert(!decoder.prepare(bytes.data(), 0, renderer, clock, false, false));
  assert(!decoder.prepare(bytes.data(), size_t(INT_MAX) + 1, renderer, clock, false, false));
  assert(opens == 0);
  assert(!decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, false));
  assert(processes == 0 && allocations == 0);
  assert(!decoder.decode());
}
void prepare_failures() {
  Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas); auto bytes = fixture(24, 24);
  for (int fault = 0; fault < 14; ++fault) {
    reset();
    if (fault == 0) faults.open = true;
    if (fault == 1) faults.parse = true;
    if (fault == 2) faults.allocate = true;
    if (fault == 3) faults.output_query = true;
    if (fault == 4) faults.count_query = true;
    if (fault == 5) faults.queried_size = 0;
    if (fault == 6) faults.queried_size = -1;
    if (fault == 7) faults.queried_size = 320 * 16 * 2 + 1;
    if (fault == 8) faults.queried_size = 24 * 2 + 1;
    if (fault == 9) faults.count = 0;
    if (fault == 10) faults.count = -1;
    if (fault == 11) faults.count = 4;
    if (fault == 12) faults.queried_size = 1;
    if (fault == 13) faults.queried_size = INT_MAX;
    mvb::NewJpeg decoder;
    assert(!decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
    assert(!decoder.decode() && !decoder.draw_failed());
    const unsigned failed_opens = opens;
    faults = {};
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
    assert(decoder.decode());
    assert(opens == failed_opens + 1);
  }
}
void decode_failures() {
  Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas); auto bytes = fixture(24, 32);
  for (int fault = 0; fault < 8; ++fault) {
    reset();
    if (fault == 0) faults.process_error = 0;
    if (fault == 1) faults.process_error = 1;
    if (fault == 2) faults.reported_size = 0;
    if (fault == 3) faults.reported_size = -1;
    if (fault == 4) faults.reported_size = 1;
    if (fault == 5) faults.reported_size = 24 * 16 * 2 + 2;
    if (fault == 6) faults.count = 1;
    if (fault == 7) faults.count = 3;
    mvb::NewJpeg decoder;
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
    assert(!decoder.decode() && !decoder.draw_failed());
    assert(!decoder.decode() && closes == 1);
    faults = {};
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
    assert(decoder.decode());
    assert(opens == 2);
  }
}
void draw_failure() {
  for (bool native : {false, true}) {
    reset(); Clock clock; Canvas canvas(clock); canvas.reject = true;
    mvb::TileRenderer renderer(canvas); renderer.native_blocks = native;
    renderer.source_width = renderer.source_height = 24;
    renderer.transform = {0, 0, 24, 24, 0, false}; auto bytes = fixture(24, 24);
    mvb::NewJpeg decoder;
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, false));
    assert(!decoder.decode() && decoder.draw_failed());
    assert(processes == 1 && canvas.blocks + canvas.rows == 1 && decoder.draw_us() > 0);
    assert(!decoder.decode() && processes == 1);
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
    assert(!decoder.draw_failed() && decoder.draw_us() == 0 && decoder.decode());
  }
}
void warmup(unsigned blocks = 1) {
  assert(mvb::NewJpeg::first_block_warmup_available());
  for (bool be : {false, true})
    for (int height : {8, 24, 240}) {
      reset(); Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas);
      renderer.source_width = 320; renderer.source_height = height;
      renderer.transform = {0, 0, 320, height, 0, false};
      auto current = fixture(320, height, 7), next = fixture(320, height, 93);
      mvb::NewJpeg decoder;
      assert(decoder.prepare(current.data(), current.size(), renderer, clock, true, be));
      assert(decoder.decode());
      assert(decoder.configure_warmup_blocks(blocks));
      assert(decoder.warmup_blocks() == blocks);
      assert(decoder.warmup_cache_capacity() == (blocks == 2 ? 320 * 16 * 2 : 0));
      // Canvas owns submitted pixels; its last strip remains intact when the
      // decoder reuses its output for another picture's first block.
      auto owned = canvas.pixels; auto writes = canvas.writes;
      unsigned before = processes, old_blocks = canvas.blocks;
      assert(decoder.warm_first_block(next.data(), next.size(), renderer, clock, be));
      unsigned cached = std::min(blocks, unsigned((height + 15) / 16));
      assert(processes == before + cached && canvas.blocks == old_blocks);
      assert(decoder.cached_blocks() == cached);
      assert(canvas.pixels == owned && canvas.writes == writes);
      assert(decoder.first_block_open_us() > 0 && decoder.first_block_decode_us() > 0);
      assert(opens == 1 && allocations == blocks && decoder.handle_open_calls() == 1);
      assert(decoder.copy_us() == (cached == 2 ? 1 : 0));
      canvas.pixels.assign(320 * 240, 0); canvas.writes.assign(320 * 240, 0);
      canvas.blocks = 0;
      assert(decoder.resume_first_block(next.data(), next.size(), renderer, clock, be));
      assert(decoder.decode());
      assert(processes == before + unsigned((height + 15) / 16));
      assert(canvas.blocks == unsigned((height + 15) / 16));
      oracle(canvas, 320, height, renderer.transform, be, 93);
      assert(!decoder.discard_warmup() && opens == 1 && allocations == blocks);
      assert(decoder.cached_blocks() == 0);
      // Repeated hits and explicit configuration reuse the one cache.
      assert(decoder.configure_warmup_blocks(blocks) && allocations == blocks);
      assert(decoder.warm_first_block(next.data(), next.size(), renderer, clock, be));
      assert(decoder.resume_first_block(next.data(), next.size(), renderer, clock, be));
      assert(decoder.decode() && allocations == blocks && opens == 1);
    }
}
void warmup_aborts(unsigned blocks = 1) {
  for (int mismatch = 0; mismatch < 4; ++mismatch) {
    reset(); Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas);
    renderer.source_width = renderer.source_height = 24;
    renderer.transform = {0, 0, 24, 24, 0, false};
    auto bytes = fixture(24, 24, 3), other = fixture(24, 24, 72);
    mvb::NewJpeg decoder;
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
    assert(decoder.decode() && decoder.configure_warmup_blocks(blocks));
    unsigned before = processes;
    assert(decoder.warm_first_block(bytes.data(), bytes.size(), renderer, clock, false));
    if (mismatch == 0)
      assert(!decoder.resume_first_block(other.data(), other.size(), renderer, clock, false));
    if (mismatch == 1)
      assert(!decoder.resume_first_block(bytes.data(), bytes.size() - 1, renderer, clock, false));
    if (mismatch == 2)
      assert(!decoder.resume_first_block(bytes.data(), bytes.size(), renderer, clock, true));
    if (mismatch == 3) {
      renderer.source_width = 16;
      assert(!decoder.resume_first_block(bytes.data(), bytes.size(), renderer, clock, false));
      renderer.source_width = 24;
    }
    assert(processes == before + blocks && canvas.blocks == 0);
    assert(decoder.discard_warmup() && closes == 1 && !decoder.discard_warmup());
    // Input recycling happens only after the partial handle was closed.
    bytes.assign(other.begin(), other.end());
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, false));
    assert(decoder.decode());
    assert(opens == 2 && allocations == blocks && decoder.handle_open_calls() == 2);
    oracle(canvas, 24, 24, renderer.transform, false, 72);
  }
  reset(); Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas);
  renderer.source_width = renderer.source_height = 24;
  auto bytes = fixture(24, 24); mvb::NewJpeg decoder;
  assert(decoder.warm_first_block(bytes.data(), bytes.size(), renderer, clock, false));
  // An ordinary decode/probe closes the partial picture before reparsing.
  assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
  assert(opens == 2 && closes == 1 && allocations == 1 && decoder.decode());
}
void two_block_configuration() {
  reset(); Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas);
  renderer.source_width = renderer.source_height = 24;
  auto bytes = fixture(24, 24); mvb::NewJpeg decoder;
  assert(!decoder.configure_warmup_blocks(0));
  assert(!decoder.configure_warmup_blocks(3));
  assert(!decoder.configure_warmup_blocks(2)); // No prepared output workspace.
  assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
  assert(decoder.decode());
  faults.allocate = true;
  assert(!decoder.configure_warmup_blocks(2));
  assert(decoder.warmup_blocks() == 1 && decoder.warmup_cache_capacity() == 0);
  assert(decoder.warm_first_block(bytes.data(), bytes.size(), renderer, clock, false));
  assert(decoder.cached_blocks() == 1 && processes == 3);
  assert(decoder.discard_warmup());
  faults = {};
  assert(decoder.configure_warmup_blocks(2));
  assert(decoder.warmup_cache_capacity() == 10240 && decoder.warmup_blocks() == 2);
  const unsigned allocated = allocations;
  assert(decoder.configure_warmup_blocks(2) && allocations == allocated);
  assert(decoder.warm_first_block(bytes.data(), bytes.size(), renderer, clock, false));
  unsigned old_closes = closes, old_frees = frees;
  // Changing back to one closes the abandoned codec before releasing cache.
  assert(decoder.configure_warmup_blocks(1));
  assert(closes == old_closes + 1 && frees == old_frees + 1);
  assert(decoder.warmup_cache_capacity() == 0 && decoder.cached_blocks() == 0);
  assert(decoder.warmup_blocks() == 1);
}
void two_block_process_failure() {
  reset(); Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas);
  renderer.source_width = renderer.source_height = 24;
  renderer.transform = {0, 0, 24, 24, 0, false};
  auto bytes = fixture(24, 24); mvb::NewJpeg decoder;
  assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
  assert(decoder.decode() && decoder.configure_warmup_blocks(2));
  faults.process_error = 1;
  assert(!decoder.warm_first_block(bytes.data(), bytes.size(), renderer, clock, false));
  assert(processes == 4 && canvas.blocks == 0 && closes == 1);
  assert(decoder.cached_blocks() == 0 && !decoder.decode());
  faults = {};
  assert(decoder.warm_first_block(bytes.data(), bytes.size(), renderer, clock, false));
  assert(decoder.resume_first_block(bytes.data(), bytes.size(), renderer, clock, false));
  assert(decoder.decode() && allocations == 2 && opens == 2);
  oracle(canvas, 24, 24, renderer.transform, false);
}
void warmup_errors() {
  for (int fault : {0, 1, 2, 3}) {
    reset(); Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas);
    renderer.source_width = renderer.source_height = 24;
    renderer.transform = {0, 0, 24, 24, 0, false};
    auto bytes = fixture(24, 24); mvb::NewJpeg decoder;
    if (fault == 0) faults.parse = true;
    if (fault == 1) faults.process_error = 0;
    if (fault == 2) faults.reported_size = 1;
    if (fault == 3) renderer.source_width = 16;
    assert(!decoder.warm_first_block(bytes.data(), bytes.size(), renderer, clock, false));
    assert(canvas.blocks == 0 && !decoder.decode() && !decoder.draw_failed());
    assert(closes == 1);
    faults = {}; renderer.source_width = 24;
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, false));
    assert(decoder.decode() && opens == 2);
  }
}
#endif

int main(int argc, char **argv) {
  assert(argc == 2); const std::string scenario = argv[1];
#if MVB_HAS_NEW_JPEG
  if (scenario == "native") native();
  else if (scenario == "reuse") reuse();
  else if (scenario == "transforms") transforms();
  else if (scenario == "rejected") rejected();
  else if (scenario == "prepare_failures") prepare_failures();
  else if (scenario == "decode_failures") decode_failures();
  else if (scenario == "draw_failure") draw_failure();
  else if (scenario == "warmup") warmup();
  else if (scenario == "warmup_aborts") warmup_aborts();
  else if (scenario == "warmup_errors") warmup_errors();
  else if (scenario == "warmup_two") warmup(2);
  else if (scenario == "warmup_two_aborts") warmup_aborts(2);
  else if (scenario == "warmup_two_configuration") two_block_configuration();
  else if (scenario == "warmup_two_process_failure") two_block_process_failure();
  else if (scenario == "two_guard_disabled") {
    assert(mvb::NewJpeg::first_block_warmup_available());
    assert(!mvb::NewJpeg::two_block_warmup_available());
    Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas); mvb::NewJpeg decoder;
    auto bytes = fixture(24, 24);
    assert(decoder.prepare(bytes.data(), bytes.size(), renderer, clock, false, false));
    assert(decoder.decode());
    unsigned allocated = allocations;
    assert(!decoder.configure_warmup_blocks(2) && allocations == allocated);
    assert(decoder.warmup_blocks() == 1 && decoder.warmup_cache_capacity() == 0);
    assert(decoder.copy_us() == 0);
  }
  else if (scenario == "guard_disabled") {
    assert(!mvb::NewJpeg::first_block_warmup_available());
    Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas); mvb::NewJpeg decoder;
    auto bytes = fixture(24, 24);
    assert(!decoder.warm_first_block(bytes.data(), bytes.size(), renderer, clock, false));
    assert(!decoder.resume_first_block(bytes.data(), bytes.size(), renderer, clock, false));
    assert(opens == 0 && allocations == 0 && processes == 0);
  }
  else return 2;
#else
  static_assert(!mvb::NewJpeg::available(), "Host must not claim real codec support");
  assert(scenario == "host");
  Clock clock; Canvas canvas(clock); mvb::TileRenderer renderer(canvas); mvb::NewJpeg decoder;
  auto bytes = fixture(24, 24);
  assert(!decoder.prepare(bytes.data(), bytes.size(), renderer, clock, true, false));
  assert(!decoder.decode() && !decoder.draw_failed());
  assert(decoder.draw_us() == 0 && decoder.color_us() == 0);
#endif
}
"""


class VendorIntegrityTests(unittest.TestCase):
    def test_pinned_upstream_files_match_provenance(self):
        root = ROOT / "firmware/vendor/esp_new_jpeg"
        provenance = json.loads((root / "PROVENANCE.json").read_text())
        self.assertEqual(provenance["version"], "1.0.2")
        self.assertEqual(provenance["target"], "esp32")
        self.assertEqual(provenance["upstream_commit"],
                         "7162d32381d6e1f8c8cba2ee5edcbfe82bc964b1")
        self.assertEqual(len(provenance["files"]), 7)
        for record in provenance["files"]:
            with self.subTest(file=record["file"]):
                data = (root / record["file"]).read_bytes()
                self.assertEqual(len(data), record["size"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
                self.assertIn(provenance["upstream_commit"], record["url"])


@unittest.skipUnless(CXX, "A C++ compiler is required")
class NewJpegAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="muse-new-jpeg-tests-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = directory / "adapter.cpp"
        source.write_text(SOURCE)
        (directory / "sdkconfig.h").write_text("#define CONFIG_IDF_TARGET_ESP32 1\n")
        cls.executables = {}
        for variant in ("host", "stub", "warm", "one"):
            executable = directory / variant
            command = [CXX, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                       "-I", str(ROOT / "firmware/include"), "-I", str(directory),
                       str(source), "-o", str(executable)]
            if variant in ("stub", "warm", "one"):
                command.extend(["-DARDUINO_ARCH_ESP32", "-DMVB_ENABLE_NEW_JPEG"])
            if variant in ("warm", "one"):
                command.append("-DMVB_JPEG_FIRST_BLOCK_WARMUP")
            if variant == "warm":
                command.append("-DMVB_JPEG_TWO_BLOCK_WARMUP")
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.executables[variant] = executable

    def scenario(self, name, variant="stub"):
        result = subprocess.run([str(self.executables[variant]), name],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_endian_and_final_padding_rows(self):
        self.scenario("native")

    def test_handles_and_aligned_output_are_reused_and_released(self):
        self.scenario("reuse")

    def test_transform_and_clipping_match_independent_pixel_oracle(self):
        self.scenario("transforms")

    def test_incompatible_dimensions_and_inputs_fail_before_output(self):
        self.scenario("rejected")

    def test_header_size_count_and_allocation_failures_recover(self):
        self.scenario("prepare_failures")

    def test_decode_failures_and_incomplete_blocks_recover(self):
        self.scenario("decode_failures")

    def test_draw_failure_is_reported_without_counting_completion(self):
        self.scenario("draw_failure")

    def test_disabled_host_has_no_codec_dependency(self):
        self.scenario("host", "host")

    def test_warm_first_block_owns_pixels_and_is_submitted_once(self):
        self.scenario("warmup", "warm")

    def test_warm_misses_close_partial_codec_before_input_recycling(self):
        self.scenario("warmup_aborts", "warm")

    def test_warm_header_process_and_geometry_errors_recover(self):
        self.scenario("warmup_errors", "warm")

    def test_warm_guard_disabled_performs_no_work(self):
        self.scenario("guard_disabled")

    def test_two_cached_strips_are_owned_and_submitted_once_in_both_orders(self):
        self.scenario("warmup_two", "warm")

    def test_two_cached_strips_abort_before_input_recycling(self):
        self.scenario("warmup_two_aborts", "warm")

    def test_two_strip_selection_allocation_failure_reuse_and_release(self):
        self.scenario("warmup_two_configuration", "warm")

    def test_second_warm_block_failure_closes_partial_picture_and_recovers(self):
        self.scenario("warmup_two_process_failure", "warm")

    def test_one_block_build_excludes_two_cache_and_preserves_original_rendering(self):
        self.scenario("two_guard_disabled", "one")
        self.scenario("warmup", "one")
        self.scenario("warmup_aborts", "one")


if __name__ == "__main__":
    unittest.main()
