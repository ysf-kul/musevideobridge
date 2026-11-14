#include "checked_stdio.hpp"
#include "render.hpp"
#include "timing.hpp"
#include "wire.hpp"
#include "playlist.hpp"
#include <cassert>
#include <cstdio>
#include <limits>
#include <vector>

namespace {
// Real FFmpeg-generated, independently decodable 16x16 black baseline JPEG.
// COM segments below vary record length without changing the image or tables.
const uint8_t black_jpeg[] = {
    0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 0x01,
    0x02, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xff, 0xfe, 0x00, 0x0f,
    0x4c, 0x61, 0x76, 0x63, 0x36, 0x33, 0x2e, 0x31, 0x2e, 0x31, 0x30, 0x32,
    0x00, 0xff, 0xdb, 0x00, 0x43, 0x00, 0x08, 0x10, 0x10, 0x13, 0x10, 0x13,
    0x16, 0x16, 0x16, 0x16, 0x16, 0x16, 0x1a, 0x18, 0x1a, 0x1b, 0x1b, 0x1b,
    0x1a, 0x1a, 0x1a, 0x1a, 0x1b, 0x1b, 0x1b, 0x1d, 0x1d, 0x1d, 0x22, 0x22,
    0x22, 0x1d, 0x1d, 0x1d, 0x1b, 0x1b, 0x1d, 0x1d, 0x20, 0x20, 0x22, 0x22,
    0x25, 0x26, 0x25, 0x23, 0x23, 0x22, 0x23, 0x26, 0x26, 0x28, 0x28, 0x28,
    0x30, 0x30, 0x2e, 0x2e, 0x38, 0x38, 0x3a, 0x45, 0x45, 0x53, 0xff, 0xc4,
    0x00, 0x4b, 0x00, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x08, 0x01, 0x01, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x10, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x11, 0x01, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0xff, 0xc0, 0x00, 0x11, 0x08, 0x00, 0x10, 0x00, 0x10,
    0x03, 0x01, 0x22, 0x00, 0x02, 0x11, 0x00, 0x03, 0x11, 0x00, 0xff, 0xda,
    0x00, 0x0c, 0x03, 0x01, 0x00, 0x02, 0x11, 0x03, 0x11, 0x00, 0x3f, 0x00,
    0x9f, 0xc0, 0x07, 0xff, 0xd9,
};

std::vector<uint8_t> jpeg_with_comment(size_t extra) {
  assert(extra >= 4 && extra <= 65537);
  std::vector<uint8_t> bytes{
      0xff, 0xd8, 0xff, 0xfe, uint8_t((extra - 2) >> 8), uint8_t(extra - 2)};
  bytes.resize(2 + extra, 0);
  bytes.insert(bytes.end(), black_jpeg + 2, black_jpeg + sizeof(black_jpeg));
  return bytes;
}

std::vector<uint8_t>
container_bytes(const std::vector<std::vector<uint8_t>> &payloads) {
  std::vector<uint8_t> bytes(96, 0), index;
  uint32_t max_jpeg = 0;
  for (size_t i = 0; i < payloads.size(); i++) {
    const auto &payload = payloads[i];
    assert(!payload.empty() && payload.size() <= mvb::MaxJpeg);
    size_t offset = bytes.size();
    uint32_t checksum = mvb::crc32(payload.data(), payload.size());
    bytes.resize(offset + 16, 0);
    mvb::put32(bytes.data() + offset, uint32_t(payload.size()));
    mvb::put32(bytes.data() + offset + 4, uint32_t(i));
    mvb::put32(bytes.data() + offset + 8, checksum);
    bytes.insert(bytes.end(), payload.begin(), payload.end());
    bytes.resize((bytes.size() + 3) & ~size_t(3), 0);
    size_t at = index.size();
    index.resize(at + 24);
    mvb::put64(index.data() + at, offset);
    mvb::put64(index.data() + at + 8, mvb::Rate{}.pts(uint32_t(i)));
    mvb::put32(index.data() + at + 16, uint32_t(payload.size()));
    mvb::put32(index.data() + at + 20, checksum);
    max_jpeg = std::max(max_jpeg, uint32_t(payload.size()));
  }
  uint64_t index_offset = bytes.size();
  bytes.insert(bytes.end(), index.begin(), index.end());
  std::memcpy(bytes.data(), "MVBJPG1\0", 8);
  mvb::put16(bytes.data() + 8, 1);
  mvb::put16(bytes.data() + 10, 96);
  mvb::put16(bytes.data() + 16, 16);
  mvb::put16(bytes.data() + 18, 16);
  mvb::put32(bytes.data() + 20, 10);
  mvb::put32(bytes.data() + 24, 1);
  mvb::put32(bytes.data() + 28, uint32_t(payloads.size()));
  mvb::put64(bytes.data() + 32, mvb::Rate{}.pts(uint32_t(payloads.size())));
  mvb::put64(bytes.data() + 40, 96);
  mvb::put64(bytes.data() + 48, index_offset);
  mvb::put64(bytes.data() + 56, bytes.size());
  mvb::put32(bytes.data() + 64, max_jpeg);
  mvb::put16(bytes.data() + 68, 24);
  mvb::put16(bytes.data() + 70, 16);
  mvb::put32(bytes.data() + 72, mvb::crc32(index.data(), index.size()));
  mvb::put32(bytes.data() + 76, mvb::crc32(bytes.data(), 96));
  return bytes;
}

class RecordClock : public mvb::ProfileClock {
public:
  uint64_t time = 100;
  uint64_t now() override { return time++; }
};

class RecordFile : public mvb::File {
  RecordClock &clock_;
  size_t offset_ = 0;
  mvb::IoProfile profile_;

public:
  std::vector<uint8_t> bytes;
  std::vector<size_t> requests;
  size_t readable_end = std::numeric_limits<size_t>::max();
  size_t short_limit = std::numeric_limits<size_t>::max();
  bool fail_seek = false;
  RecordFile(RecordClock &clock, std::vector<uint8_t> data)
      : clock_(clock), bytes(std::move(data)) {
    profile_.available = true;
  }
  uint64_t size() override { return bytes.size(); }
  bool seek(uint32_t offset) override {
    profile_.seek_us += 11;
    profile_.seek_calls++;
    clock_.time += 11;
    if (fail_seek || offset > bytes.size())
      return false;
    offset_ = offset;
    return true;
  }
  size_t read(uint8_t *out, size_t count) override {
    requests.push_back(count);
    size_t end = std::min(bytes.size(), readable_end);
    size_t got = offset_ < end ? std::min(count, end - offset_) : 0;
    got = std::min(got, short_limit);
    if (got)
      std::copy_n(bytes.data() + offset_, got, out);
    offset_ += got;
    profile_.read_us += 17;
    profile_.read_calls++;
    profile_.read_bytes += got;
    clock_.time += 17;
    return got;
  }
  size_t write(const uint8_t *, size_t) override { return 0; }
  bool flush() override { return false; }
  void close() override {}
  mvb::IoProfile io_profile() const override { return profile_; }
  void reset_profile() {
    requests.clear();
    profile_ = {};
    profile_.available = true;
  }
};

mvb::Entry prepare_record(mvb::Video &video, RecordFile &file, uint32_t i) {
  assert(video.begin(&file));
  while (!video.ready)
    assert(video.scan_step());
  mvb::Entry e;
  assert(video.get_entry(i, e));
  file.reset_profile();
  return e;
}

void record_failure(const std::vector<uint8_t> &bytes, const char *error,
                    size_t readable_end = std::numeric_limits<size_t>::max(),
                    size_t short_limit = std::numeric_limits<size_t>::max(),
                    bool fail_seek = false) {
  for (bool coalesced : {false, true}) {
    RecordClock clock;
    RecordFile file(clock, bytes);
    mvb::Video video;
    prepare_record(video, file, 0);
    file.readable_end = readable_end;
    file.short_limit = short_limit;
    file.fail_seek = fail_seek;
    std::vector<uint8_t> output(mvb::MaxJpeg + 20, 0xa5);
    size_t length = 0;
    bool ok = coalesced
                  ? video.frame_record(0, output.data(), output.size(), length)
                  : video.frame(0, output.data(), length);
    assert(!ok && video.error && !std::strcmp(video.error, error));
  }
}

void frame_record_tests() {
  std::vector<std::vector<uint8_t>> payloads;
  for (size_t extra : {size_t(4), size_t(5), size_t(6), size_t(7),
                       mvb::MaxJpeg - sizeof(black_jpeg)})
    payloads.push_back(jpeg_with_comment(extra));
  auto bytes = container_bytes(payloads);
  for (uint32_t i = 0; i < payloads.size(); i++) {
    RecordClock clock;
    RecordFile legacy_file(clock, bytes), record_file(clock, bytes);
    mvb::Video legacy, coalesced;
    auto e = prepare_record(legacy, legacy_file, i);
    prepare_record(coalesced, record_file, i);
    size_t record_bytes = size_t(e.next() - e.offset);
    std::vector<uint8_t> payload(mvb::MaxJpeg), record(mvb::MaxJpeg + 22, 0xa5);
    size_t payload_length = 0, record_length = 0;
    mvb::FrameProfile legacy_profile, record_profile;
    assert(legacy.frame(i, payload.data(), payload_length, &clock,
                        &legacy_profile));
    assert(coalesced.frame_record(i, record.data() + 1, record_bytes,
                                  record_length, &clock, &record_profile));
    assert(payload_length == payloads[i].size() &&
           record_length == payload_length);
    assert(!std::memcmp(payload.data(), payloads[i].data(), payload_length));
    assert(!std::memcmp(record.data() + 1 + mvb::Video::RecordHeaderBytes,
                        payload.data(), payload_length));
    assert(
        !std::memcmp(record.data() + 1, bytes.data() + e.offset, record_bytes));
    assert(record[0] == 0xa5);
    for (size_t k = record_bytes + 1; k < record.size(); k++)
      assert(record[k] == 0xa5);
    assert(legacy_file.requests.size() == 3);
    assert(record_file.requests == std::vector<size_t>{record_bytes});
    assert(legacy_profile.io_available && record_profile.io_available);
    assert(legacy_profile.sd_read_us == 51 && record_profile.sd_read_us == 17);
    assert(record_profile.sd_seek_us == 11 && record_profile.crc_us > 0 &&
           record_profile.validate_us > 0);
  }

  auto single = container_bytes({payloads.front()});
  for (size_t header_at : {0, 4, 8, 12}) {
    auto corrupt = single;
    corrupt[96 + header_at] ^= 1;
    record_failure(corrupt, "invalid_container");
  }
  auto corrupt = single;
  corrupt[96 + 16 + payloads.front().size() - 3] ^= 1;
  record_failure(corrupt, "crc_mismatch");
  auto unsupported = payloads.front();
  unsupported[0] = 0;
  record_failure(container_bytes({unsupported}), "unsupported_jpeg");
  auto wrong_dimensions = payloads.front();
  size_t sof = 0;
  for (size_t k = 0; k + 8 < wrong_dimensions.size(); k++)
    if (wrong_dimensions[k] == 0xff && wrong_dimensions[k + 1] == 0xc0) {
      sof = k;
      break;
    }
  assert(sof);
  wrong_dimensions[sof + 8] = 17;
  record_failure(container_bytes({wrong_dimensions}), "unsupported_jpeg");
  for (size_t k = 0; k < 3; k++) {
    corrupt = single;
    corrupt[96 + 16 + payloads.front().size() + k] = 1;
    record_failure(corrupt, "invalid_container");
  }
  for (size_t end :
       {size_t(96 + 15), size_t(96 + 16 + payloads.front().size() - 1),
        size_t(mvb::get64(single.data() + 48) - 1)})
    record_failure(single, "io_error", end);
  record_failure(single, "io_error", std::numeric_limits<size_t>::max(), 8);
  record_failure(single, "seek_failed", std::numeric_limits<size_t>::max(),
                 std::numeric_limits<size_t>::max(), true);

  for (uint32_t index : {uint32_t(payloads.size()), UINT32_MAX})
    for (bool coalesced : {false, true}) {
      RecordClock clock;
      RecordFile file(clock, bytes);
      mvb::Video video;
      prepare_record(video, file, 0);
      std::vector<uint8_t> output(mvb::MaxJpeg + 20);
      size_t length = 0;
      assert(!(coalesced ? video.frame_record(index, output.data(),
                                              output.size(), length)
                         : video.frame(index, output.data(), length)));
      assert(video.ready && !video.error && file.requests.empty());
    }
  for (bool null_buffer : {false, true}) {
    RecordClock clock;
    RecordFile file(clock, single);
    mvb::Video video;
    auto e = prepare_record(video, file, 0);
    std::vector<uint8_t> output(mvb::MaxJpeg + 20, 0xa5);
    size_t length = 0;
    assert(!video.frame_record(0, null_buffer ? nullptr : output.data(),
                               size_t(e.next() - e.offset) - 1, length));
    assert(!std::strcmp(video.error, "invalid_parameter") &&
           file.requests.empty());
    assert(std::all_of(output.begin(), output.end(),
                       [](uint8_t byte) { return byte == 0xa5; }));
  }
  std::puts(
      "PASS: coalesced frame records match payloads/errors, cover all padding "
      "lengths and 48 KiB boundary, reject short reads/capacity/index faults; "
      "one record read after index cache warm-up. Host assertions only.");
}
} // namespace

class Canvas : public mvb::Painter {
public:
  std::vector<uint16_t> pixels = std::vector<uint16_t>(320 * 240);
  bool row(int x, int y, const uint16_t *p, int n) override {
    assert(x >= 0 && y >= 0 && y < 240 && x + n <= 320);
    std::copy(p, p + n, pixels.begin() + y * 320 + x);
    return true;
  }
  void clear(mvb::Rect r) override {
    for (int y = r.y; y < r.y + r.h; y++)
      for (int x = r.x; x < r.x + r.w; x++) {
        assert(x >= 0 && x < 320 && y >= 0 && y < 240);
        pixels[y * 320 + x] = 0;
      }
  }
  void waiting(bool, bool, const char *) override {}
};
int main() {
  mvb::Playlist queue;
  char a[65], b[65], c[65];
  std::memset(a, 'a', 64); a[64] = 0;
  std::memset(b, 'b', 64); b[64] = 0;
  std::memset(c, 'c', 64); c[64] = 0;
  assert(queue.adjacent(1) == -1 && queue.after_end() == -1);
  assert(queue.append(a) && queue.append(b) && queue.append(c));
  assert(queue.append(b) && queue.count == 3 && queue.revision == 3);
  queue.position = 0;
  assert(queue.adjacent(-1) == -1 && queue.after_end() == 1);
  queue.position = 2;
  assert(queue.adjacent(1) == -1 && queue.adjacent(-1) == 1);
  queue.mode = mvb::LoopMode::Current;
  assert(queue.after_end() == 2 && queue.adjacent(1) == -1);
  queue.mode = mvb::LoopMode::Queue;
  assert(queue.after_end() == 0);
  queue.position = 0;
  assert(queue.adjacent(-1) == 2);
  queue.count = 1;
  assert(queue.adjacent(1) == 0 && queue.adjacent(-1) == 0);
  FILE *temporary = std::tmpfile();
  assert(temporary && std::fwrite("verified", 1, 8, temporary) == 8);
  assert(mvb::sync_file(temporary));
  assert(std::fclose(temporary) == 0);
#if defined(__linux__)
  FILE *full = std::fopen("/dev/full", "wb");
  assert(full);
  std::fwrite("failed", 1, 6, full);
  assert(!mvb::sync_file(full));
  std::fclose(full);
  int pipes[2];
  assert(::pipe(pipes) == 0);
  FILE *pipe = ::fdopen(pipes[1], "wb");
  assert(pipe && std::fwrite("sync", 1, 4, pipe) == 4);
  // fflush succeeds on a pipe; filesystem fsync fails. Detect both stages.
  assert(!mvb::sync_file(pipe));
  std::fclose(pipe);
  ::close(pipes[0]);
#endif
  frame_record_tests();
  assert(mvb::crc_contract_self_test());
  assert(mvb::crc32(reinterpret_cast<const uint8_t *>("123456789"), 9) ==
         0xcbf43926);
  for (size_t n : {0, 1, 254, 1024, 2048})
    for (uint8_t value : {uint8_t(0), uint8_t(1), uint8_t(255)}) {
      mvb::Frame f;
      f.kind = 16;
      f.sequence = 123;
      f.session = 456;
      f.length = uint16_t(n);
      std::memset(f.payload, value, n);
      uint8_t encoded[mvb::MaxWire];
      auto size = mvb::encode(f, encoded);
      assert(size <= mvb::MaxWire);
      mvb::Frame g;
      assert(mvb::decode(encoded, size - 1, g));
      assert(g.length == f.length && !std::memcmp(g.payload, f.payload, n));
      encoded[1] ^= 1;
      assert(!mvb::decode(encoded, size - 1, g));
    }
  int cases = 0;
  const int sw = 18, sh = 10;
  std::vector<uint16_t> source(sw * sh);
  for (int i = 0; i < sw * sh; i++)
    source[i] = uint16_t(i + 1);
  for (int rotation : {0, 90, 180, 270})
    for (int w : {1, 7, 18, 36, 640})
      for (int h : {1, 5, 10, 20, 480})
        for (int x : {-640, -5, 11, 319, 640}) {
          Canvas canvas;
          mvb::TileRenderer renderer(canvas);
          renderer.source_width = sw;
          renderer.source_height = sh;
          renderer.transform = {x, -3, w, h, rotation, false};
          for (int ty = 0; ty < sh; ty += 4)
            for (int tx = 0; tx < sw; tx += 6) {
              int used = std::min(6, sw - tx), height = std::min(4, sh - ty);
              uint16_t tile[6 * 4]{};
              for (int dy = 0; dy < height; dy++)
                for (int dx = 0; dx < used; dx++)
                  tile[dy * 6 + dx] = source[(ty + dy) * sw + tx + dx];
              assert(renderer.tile(tx, ty, 6, used, height, tile));
            }
          int rw = rotation % 180 ? sh : sw, rh = rotation % 180 ? sw : sh;
          for (int py = 0; py < 240; py++)
            for (int px = 0; px < 320; px++) {
              uint16_t expected = 0;
              int dx = px - x, dy = py + 3;
              if (dx >= 0 && dx < w && dy >= 0 && dy < h) {
                int sx, sy;
                const int u = std::min(rw - 1, (2 * dx + 1) * rw / (2 * w));
                const int v = std::min(rh - 1, (2 * dy + 1) * rh / (2 * h));
                if (rotation == 0) {
                  sx = u;
                  sy = v;
                } else if (rotation == 90) {
                  sx = v;
                  sy = sh - 1 - u;
                } else if (rotation == 180) {
                  sx = sw - 1 - u;
                  sy = sh - 1 - v;
                } else {
                  sx = sw - 1 - v;
                  sy = u;
                }
                expected = source[sy * sw + sx];
              }
              assert(canvas.pixels[py * 320 + px] == expected);
            }
          cases++;
        }
  for (auto next : {mvb::Rect{50, 50, 80, 80}, mvb::Rect{0, 0, 320, 240},
                    mvb::Rect{200, 200, 20, 20}, mvb::Rect{0, 0, 0, 0}}) {
    Canvas canvas;
    std::fill(canvas.pixels.begin(), canvas.pixels.end(), 42);
    mvb::Rect old{20, 20, 100, 100};
    mvb::clear_difference(canvas, old, next);
    for (int y = 0; y < 240; y++)
      for (int x = 0; x < 320; x++) {
        bool inside_old =
            x >= old.x && x < old.x + old.w && y >= old.y && y < old.y + old.h;
        bool inside_next = x >= next.x && x < next.x + next.w && y >= next.y &&
                           y < next.y + next.h;
        assert(canvas.pixels[y * 320 + x] ==
               (inside_old && !inside_next ? 0 : 42));
      }
  }
  mvb::Timeline t;
  t.encoded = {15, 1};
  t.target = {15, 1};
  t.count = 100;
  t.start(1000000);
  uint32_t index = 0;
  assert(t.due(1000000, index) && index == 0);
  t.complete(index, 1000000, 1001000);
  assert(!t.due(1010000, index));
  t.pause(1020000);
  assert(t.position(9000000) == 20000);
  t.start(9000000);
  assert(t.position(9010000) == 30000);
  t.seek(30, 9010000);
  assert(t.position(9010000) == 2000000);
  t.stop();
  assert(t.position(9010000) == 0);
  t.encoded = {30, 1};
  t.target = {10, 1};
  t.count = 30;
  assert(t.eligible(0) && t.eligible(3) && !t.eligible(1));
  t.start(1000000);
  assert(t.due(1050000, index) &&
         index == 0); // Source frame1 is late but ineligible.
  assert(!t.due(1050000, index));
  assert(t.due(1140000, index) && index == 3);
  assert(t.due(1310000, index) &&
         index == 9); // Eligible frame6 was superseded.
  assert(t.dropped == 1);
  assert((mvb::Rate{15, 1}).pts(54000) == 3600000000ULL);
  std::printf("PASS: protocol bounds/CRC, %d transform golden cases, rational "
              "timing/pause/seek\n",
              cases);
}
