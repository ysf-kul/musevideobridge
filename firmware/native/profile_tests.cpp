// Deterministic adapter faults and timings, never ESP32 performance measurements.
#include "engine.hpp"
#include <cassert>
#include <fstream>
#include <iterator>
#include <vector>

namespace {
struct Clock {
  uint64_t time = 10000000;
  uint64_t now() { return time++; }
  void advance(uint64_t microseconds) { time += microseconds; }
};
class MemoryFile : public mvb::File {
  Clock &clock_;
  std::vector<uint8_t> bytes_;
  size_t offset_ = 0;
  mvb::IoProfile profile_;
public:
  MemoryFile(Clock &clock, std::vector<uint8_t> bytes)
      : clock_(clock), bytes_(std::move(bytes)) { profile_.available = true; }
  uint64_t size() override { return bytes_.size(); }
  bool seek(uint32_t offset) override {
    clock_.advance(11); profile_.seek_us += 11; profile_.seek_calls++;
    offset_ = offset; return offset <= bytes_.size();
  }
  size_t read(uint8_t *data, size_t count) override {
    size_t n = std::min(count, bytes_.size() - offset_);
    std::copy_n(bytes_.data() + offset_, n, data); offset_ += n;
    clock_.advance(17); profile_.read_us += 17;
    profile_.read_bytes += n; profile_.read_calls++;
    return n;
  }
  size_t write(const uint8_t *data, size_t count) override {
    if (offset_ + count > bytes_.size()) bytes_.resize(offset_ + count);
    std::copy_n(data, count, bytes_.data() + offset_); offset_ += count;
    return count;
  }
  bool flush() override { return true; }
  void close() override {}
  mvb::IoProfile io_profile() const override { return profile_; }
  const std::vector<uint8_t> &bytes() const { return bytes_; }
};
class Storage : public mvb::Storage {
  MemoryFile file_, writer_;
  bool upload_enabled_;
public:
  Storage(Clock &clock, const std::vector<uint8_t> &bytes, bool upload = false)
      : file_(clock, bytes), writer_(clock, {}), upload_enabled_(upload) {}
  bool mount() override { return true; }
  bool mounted() override { return true; }
  uint64_t capacity() override { return 0; }
  uint64_t free_bytes() override { return upload_enabled_ ? 1048576 : 0; }
  mvb::File *read_file(const char *) override { file_.seek(0); return &file_; }
  mvb::File *write_file(const char *, bool) override { return upload_enabled_ ? &writer_ : nullptr; }
  bool exists(const char *name) override { return std::strncmp(name, "/muse-video/.upload", 19) != 0; }
  bool rename_new(const char *, const char *) override { return false; }
  bool remove_owned(const char *) override { return false; }
  bool mkdir_library() override { return upload_enabled_; }
  bool first_video(char *out, size_t size, const char *) override {
    std::snprintf(out, size, "/muse-video/%s.mvb", std::string(64, 'a').c_str());
    return true;
  }
  bool checkpoint_file(const char *, const uint8_t *, size_t, bool) override {
    return upload_enabled_;
  }
  bool select(const char *) override { return false; }
  const std::vector<uint8_t> &original_bytes() const { return file_.bytes(); }
};
class Runtime : public mvb::Runtime {
  Clock &clock_;
  uint32_t session_ = 42;
public:
  explicit Runtime(Clock &clock) : clock_(clock) {}
  uint64_t now() override { return clock_.now(); }
  void telemetry(JsonObject r) override { r["simulator"] = true; }
  bool hash_begin() override { return true; }
  bool hash_update(const uint8_t *, size_t) override { return true; }
  bool hash_finish(char out[65]) override { std::memset(out, 'a', 64); out[64] = 0; return true; }
  uint32_t session_nonce() override { return session_++; }
};
class Painter : public mvb::Painter {
  Clock &clock_;
  mvb::DisplayProfile profile_;
public:
  int begin_calls = 0, end_calls = 0, rows = 0;
  bool fail_row = false, fail_completion = false;
  explicit Painter(Clock &clock) : clock_(clock) {}
  void begin_frame() override {
    begin_calls++; profile_ = {}; profile_.available = true;
    clock_.advance(5); profile_.submit_us += 5;
  }
  bool end_frame() override {
    end_calls++; clock_.advance(2000);
    profile_.submit_us += 2000; profile_.wait_us += 2000;
    return !fail_completion;
  }
  bool row(int x, int y, const uint16_t *, int n) override {
    assert(x >= 0 && y >= 0 && y < 240 && n > 0 && x + n <= 320);
    rows++; clock_.advance(20); profile_.submit_us += 20;
    profile_.pixels += n; profile_.windows++; profile_.pushes++;
    return !fail_row;
  }
  mvb::DisplayProfile display_profile() const override { return profile_; }
  void clear(mvb::Rect) override {}
  void waiting(bool, bool, const char *) override {}
};
class Output : public mvb::Output {
public:
  mvb::Frame last;
  bool send(const mvb::Frame &frame) override { if (frame.kind == 2) last = frame; return true; }
};
class Requests {
  mvb::Engine &engine_;
  Output &output_;
  uint32_t session_ = 0, sequence_ = 0;
public:
  Requests(mvb::Engine &engine, Output &output) : engine_(engine), output_(output) {
    command("{\"method\":\"hello\",\"protocol_min\":1,\"protocol_max\":1,\"client_nonce\":\"deterministic-profile-test\"}");
    session_ = output_.last.session;
  }
  JsonDocument command(const char *json) {
    mvb::Frame frame;
    frame.kind = 1; frame.session = session_; frame.sequence = ++sequence_;
    frame.length = uint16_t(std::strlen(json));
    std::memcpy(frame.payload, json, frame.length); engine_.command(frame);
    assert(output_.last.sequence == sequence_);
    JsonDocument result;
    assert(!deserializeJson(result, output_.last.payload, output_.last.length));
    return result;
  }
};
void run(const std::vector<uint8_t> &bytes, bool fail_row, bool fail_completion) {
  Clock clock;
  Storage storage(clock, bytes); Runtime runtime(clock); Painter painter(clock); Output output;
  painter.fail_row = fail_row; painter.fail_completion = fail_completion;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin();
  Requests requests(engine, output);
  JsonDocument status;
  for (int tries = 0; tries < 10000; tries++) {
    engine.tick(); status = requests.command("{\"method\":\"get_status\"}");
    if (status["result"]["state"] == "error" || !status["result"]["frame_index"].isNull()) break;
  }
  assert(painter.begin_calls == 1 && painter.end_calls == 1);
  auto benchmark = requests.command("{\"method\":\"get_benchmark\"}");
  auto pipeline = requests.command("{\"method\":\"get_pipeline_profile\"}");
  if (fail_row || fail_completion) {
    assert(status["result"]["state"] == "error");
    assert(status["result"]["error"] == "display_failed");
    assert(status["result"]["frame_index"].isNull());
    assert(benchmark["result"]["frames"] == 0);
    assert(pipeline["result"]["average_us"]["frame"].isNull());
    return;
  }
  requests.command("{\"method\":\"pause\"}");
  assert(benchmark["result"]["frames"] == 1);
  assert(pipeline["result"]["io_available"] == true);
  assert(pipeline["result"]["display_available"] == true);
  auto stages = pipeline["result"]["average_us"];
  assert(stages["sd_read"].as<double>() > 0 && stages["sd_seek"].as<double>() > 0);
  assert(stages["dma_wait_nested"].as<double>() == 2000);
  double sum = 0;
  for (const char *key : {"sd_read", "sd_seek", "crc", "jpeg_validation", "container_prepare",
                          "jpeg_open", "jpeg_decode", "display", "transform"})
    sum += stages[key].as<double>();
  double frame = stages["frame"].as<double>();
  assert(sum <= frame && frame - sum < 50);
  assert(stages["dma_wait_nested"].as<double>() <= stages["display"].as<double>());
  int rows = painter.rows;
  requests.command("{\"method\":\"set_position\",\"x\":640,\"y\":0}");
  engine.tick();
  assert(painter.rows == rows); // Offscreen redraw never submits a pixel row.
  assert(requests.command("{\"method\":\"get_benchmark\"}")["result"]["frames"] == 1);
}
void startup_preemption(const std::vector<uint8_t> &bytes, bool hash_phase) {
  Clock clock;
  Storage storage(clock, bytes, true); Runtime runtime(clock); Painter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin();
  Requests requests(engine, output);
  bool reached = false;
  for (int tries = 0; tries < 10000; tries++) {
    engine.tick();
    auto status = requests.command("{\"method\":\"get_status\"}");
    if (status["result"]["state"] == "loading" &&
        status["result"]["validation_total_bytes"].isNull() != hash_phase) {
      reached = true; break;
    }
  }
  assert(reached);
  auto invalid = requests.command("{\"method\":\"begin_upload\",\"sha256\":\"bad\",\"size\":136,\"container_version\":1}");
  assert(invalid["error"]["code"] == "invalid_parameter");
  assert(requests.command("{\"method\":\"get_status\"}")["result"]["state"] == "loading");
  assert(requests.command("{\"method\":\"upload_status\"}")["result"]["state"] == "none");
  auto valid = requests.command("{\"method\":\"begin_upload\",\"sha256\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"size\":136,\"container_version\":1}");
  assert(valid["ok"] == true && valid["result"]["state"] == "receiving");
  assert(storage.original_bytes() == bytes);
}
} // namespace
int main(int argc, char **argv) {
  assert(argc == 2);
  std::ifstream file(argv[1], std::ios::binary);
  assert(file);
  std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(file)), {});
  run(bytes, false, false); run(bytes, true, false); run(bytes, false, true);
  startup_preemption(bytes, true); startup_preemption(bytes, false);
  std::puts("PASS: injected callback/completion failures never count; nested timing attribution and offscreen clipping. No hardware performance measurements.");
}
