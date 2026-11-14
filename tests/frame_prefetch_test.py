"""Actual container/codec tests for joined compressed-frame prefetch ownership."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")

WORKER_SOURCE = r'''
#define main existing_core_main
#include "core-adapters.hpp"
#undef main
#include "frame_prefetch.hpp"
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>

class BlockedFile : public mvb::File {
  size_t position_ = 0;
  mvb::IoProfile profile_;
  std::mutex mutex_;
  std::condition_variable condition_;
  bool entered_ = false, released_ = false;
public:
  std::vector<uint8_t> bytes;
  bool block = false, short_read = false;
  std::atomic<bool> reading{false};
  explicit BlockedFile(std::vector<uint8_t> data) : bytes(std::move(data)) {
    profile_.available = true;
  }
  uint64_t size() override { assert(!reading); return bytes.size(); }
  bool seek(uint32_t offset) override {
    assert(!reading); profile_.seek_us += 11; profile_.seek_calls++;
    position_ = offset; return offset <= bytes.size();
  }
  size_t read(uint8_t *out, size_t count) override {
    assert(!reading.exchange(true));
    if (block) {
      std::unique_lock<std::mutex> lock(mutex_); entered_ = true;
      condition_.notify_all(); condition_.wait(lock, [&] { return released_; });
    }
    size_t got = std::min(count, bytes.size() - position_);
    if (short_read && got) got--;
    std::copy_n(bytes.data() + position_, got, out); position_ += got;
    profile_.read_us += 17; profile_.read_calls++; profile_.read_bytes += got;
    reading = false; return got;
  }
  size_t write(const uint8_t *, size_t) override { assert(!reading); return 0; }
  bool flush() override { assert(!reading); return false; }
  void close() override { assert(!reading); }
  mvb::IoProfile io_profile() const override { assert(!reading); return profile_; }
  void await_read() {
    std::unique_lock<std::mutex> lock(mutex_);
    assert(condition_.wait_for(lock, std::chrono::seconds(3), [&] { return entered_; }));
  }
  void unblock() {
    std::lock_guard<std::mutex> lock(mutex_); released_ = true; condition_.notify_all();
  }
};

void prepare(mvb::Video &video, BlockedFile &file) {
  assert(video.begin(&file));
  while (!video.ready) assert(video.scan_step());
}
void blocked_lifetime(bool coalesced, bool release) {
  auto payload = jpeg_with_comment(4);
  BlockedFile file(container_bytes({payload, jpeg_with_comment(7)}));
  mvb::Video video; prepare(video, file);
  mvb::FramePrefetch worker;
  assert(!worker.configure(0));
  assert(!worker.configure(mvb::FramePrefetch::MaximumSlotBytes + 1));
  const size_t capacity = video.header.max_jpeg + 19;
  assert(worker.configure(capacity));
  auto *allocation = worker.buffer();
  assert(worker.configure(capacity) && worker.buffer() == allocation);
  std::vector<uint8_t> current(capacity, 0xa5);
  file.block = true;
  assert(worker.start(video, 1, worker.buffer(), worker.capacity(), coalesced));
  file.await_read();
  assert(!worker.start(video, 0, current.data(), current.size(), coalesced));
  assert(std::all_of(current.begin(), current.end(), [](uint8_t b) { return b == 0xa5; }));
  std::atomic<bool> joined{false}; mvb::FramePrefetch::Result result;
  std::thread joining([&] {
    if (release) worker.release(); else assert(worker.join(result));
    joined = true;
  });
  std::this_thread::sleep_for(std::chrono::milliseconds(10));
  assert(!joined && file.reading); // No task deletion or buffer release during read.
  file.unblock(); joining.join(); assert(joined && !file.reading);
  file.close();
  if (release) {
    assert(!worker.enabled() && worker.buffer() == nullptr);
  } else {
    assert(result.ok && result.index == 1 && result.length == payload.size() + 3);
    assert(result.profile.io_available && result.profile.sd_read_us > 0);
    assert(result.wall_us >= 10000);
    const uint8_t *jpeg = result.record + (coalesced ? 16 : 0);
    auto expected = jpeg_with_comment(7);
    assert(!std::memcmp(jpeg, expected.data(), expected.size()));
    assert(!worker.join(result));
    assert(!worker.start(video, 100, current.data(), current.size(), coalesced));
    assert(!worker.start(video, 0, nullptr, current.size(), coalesced));
    assert(!worker.start(video, 0, current.data(), 1, coalesced));
    worker.release(); assert(!worker.enabled());
  }
}
void errors_and_reuse() {
  auto bytes = container_bytes({jpeg_with_comment(4), jpeg_with_comment(7)});
  for (bool short_read : {false, true}) {
    BlockedFile file(bytes); mvb::Video video; prepare(video, file);
    file.short_read = short_read;
    if (!short_read) file.bytes[96 + 16 + 3] ^= 1;
    mvb::FramePrefetch worker;
    assert(worker.configure(video.header.max_jpeg + 19));
    assert(worker.start(video, 0, worker.buffer(), worker.capacity(), true));
    mvb::FramePrefetch::Result result; assert(worker.join(result));
    assert(!result.ok && result.error);
    assert(!std::strcmp(result.error, short_read ? "io_error" : "crc_mismatch"));
    worker.release(); assert(!worker.enabled());
  }
  BlockedFile file(bytes); mvb::Video video; prepare(video, file);
  mvb::FramePrefetch worker; assert(worker.configure(video.header.max_jpeg + 19));
  std::vector<uint8_t> other(video.header.max_jpeg + 19, 0xa5);
  for (unsigned i = 0; i < 200; i++) {
    auto *out = i % 2 ? other.data() : worker.buffer();
    assert(worker.start(video, i % 2, out, other.size(), true));
    mvb::FramePrefetch::Result result; assert(worker.join(result));
    assert(result.ok && result.record == out && result.index == i % 2);
  }
  worker.release();
}
void prediction() {
  mvb::Timeline timeline; timeline.encoded = {30, 1}; timeline.target = {10, 1};
  timeline.count = 60; timeline.start(1000000);
  uint32_t index; assert(timeline.due(1000000, index) && index == 0);
  assert(timeline.next_for_prefetch(1000001, index) && index == 3);
  assert(timeline.dropped == 0 && timeline.rate_skipped == 0 && timeline.drawn == 0);
  assert(!timeline.due(1000001, index));
  assert(timeline.next_for_prefetch(1250000, index) && index == 6);
  timeline.seek(59, 1000000); assert(!timeline.next_for_prefetch(1000000, index));
  timeline.stop(); assert(!timeline.next_for_prefetch(1000000, index));
}
int main() {
  for (bool record : {false, true}) for (bool release : {false, true})
    blocked_lifetime(record, release);
  errors_and_reuse(); prediction();
}
'''

ENGINE_SOURCE = r'''
#define main existing_profile_main
#include "profile-adapters.hpp"
#undef main
#define main existing_core_main
#include "core-adapters.hpp"
#undef main

void ready(mvb::Engine &engine, Requests &requests) {
  for (int i = 0; i < 1000; i++) {
    engine.tick(); auto status = requests.command("{\"method\":\"get_status\"}");
    if (status["result"]["state"] == "error") { char json[2048]; serializeJson(status, json, sizeof(json)); std::fprintf(stderr, "%s\n", json); }
    assert(status["result"]["state"] != "error");
    if (!status["result"]["frame_index"].isNull()) return;
  }
  assert(false);
}
void engine_controls() {
  std::vector<std::vector<uint8_t>> payloads;
  for (size_t i = 0; i < 80; i++) payloads.push_back(jpeg_with_comment(40 + i % 4));
  auto bytes = container_bytes(payloads);
  Clock clock; Storage storage(clock, bytes, true); Runtime runtime(clock);
  Painter painter(clock); Output output; mvb::Engine engine(storage, painter, runtime, output);
  engine.begin(); Requests requests(engine, output); ready(engine, requests);
  assert(requests.command("{\"method\":\"pause\"}")["ok"] == true);
  auto options = requests.command("{\"method\":\"set_prefetch_options\",\"enabled\":\"yes\"}");
  assert(options["error"]["code"] == "invalid_parameter");
  options = requests.command("{\"method\":\"set_prefetch_options\",\"enabled\":true}");
#ifndef MVB_FRAME_PREFETCH
  assert(options["error"]["code"] == "unsupported_configuration"); return;
#else
  assert(options["ok"] == true && options["result"]["enabled"] == true);
  assert(options["result"]["slot_bytes"].as<size_t>() == sizeof(black_jpeg) + 43 + 19);
  assert(requests.command("{\"method\":\"restart\"}")["ok"] == true);
  for (int i = 0; i < 12; i++) { engine.tick(); clock.advance(100000); }
  assert(requests.command("{\"method\":\"pause\"}")["ok"] == true);
  auto benchmark = requests.command("{\"method\":\"get_benchmark\"}");
  auto before = benchmark["result"]["frames"].as<unsigned>();
  assert(before >= 10 && benchmark["result"]["prefetch"]["hits"].as<unsigned>() >= 8);
  assert(overlapping_rows > 0 && storage_reads == 0);
  auto pipeline = requests.command("{\"method\":\"get_pipeline_profile\"}");
  assert(pipeline["result"]["sd_fetch_overlapped"] == true);
  assert(pipeline["result"]["average_us"]["sd_read"].as<double>() > 0);
  assert(pipeline["result"]["average_us"]["frame_fetch_wait"].as<double>() <
         benchmark["result"]["average_read_us"].as<double>());
  auto reads = total_storage_reads.load();
  for (const char *command : {
      "{\"method\":\"set_position\",\"x\":100,\"y\":50}",
      "{\"method\":\"set_rotation\",\"rotation_deg\":90}",
      "{\"method\":\"set_scale\",\"scale\":2}"}) {
    assert(requests.command(command)["ok"] == true); engine.tick();
  }
  assert(total_storage_reads == reads); // Paused live redraw uses retained compressed frame.
  assert(requests.command("{\"method\":\"get_benchmark\"}")["result"]["frames"] == before);
  assert(requests.command("{\"method\":\"probe_pipeline\",\"stage\":\"sd\",\"repetitions\":1}")["ok"] == true);
  assert(requests.command("{\"method\":\"set_position\",\"x\":90,\"y\":30}")["ok"] == true);
  engine.tick();
  assert(requests.command("{\"method\":\"get_status\"}")["result"]["state"] == "paused");
  for (unsigned i = 0; i < 30; i++) {
    std::string seek = "{\"method\":\"seek\",\"position_us\":" + std::to_string(i * 137111) + "}";
    assert(requests.command(seek.c_str())["ok"] == true);
    assert(requests.command("{\"method\":\"set_fps\",\"numerator\":7,\"denominator\":1}")["ok"] == true);
    assert(requests.command("{\"method\":\"resume\"}")["ok"] == true);
    clock.advance(150000); engine.tick();
    assert(requests.command("{\"method\":\"pause\"}")["ok"] == true);
    assert(storage_reads == 0);
  }
  assert(requests.command("{\"method\":\"stop\"}")["ok"] == true);
  assert(requests.command("{\"method\":\"restart\"}")["ok"] == true); engine.tick();
  options = requests.command("{\"method\":\"set_prefetch_options\",\"enabled\":false}");
  assert(options["ok"] == true && options["result"]["enabled"] == false);
  engine.tick(); assert(storage_reads == 0);
  assert(requests.command("{\"method\":\"set_prefetch_options\",\"enabled\":true}")["ok"] == true);
  auto upload = requests.command("{\"method\":\"begin_upload\",\"sha256\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"size\":136,\"container_version\":1}");
  assert(upload["ok"] == true && upload["result"]["state"] == "receiving");
  assert(requests.command("{\"method\":\"get_benchmark\"}")["result"]["prefetch"]["enabled"] == false);
  assert(storage.original_bytes() == bytes && storage_reads == 0);
#endif
}
void failures(bool corrupt_future, bool failed_completion) {
#ifdef MVB_FRAME_PREFETCH
  auto bytes = container_bytes({jpeg_with_comment(40), jpeg_with_comment(41), jpeg_with_comment(42)});
  if (corrupt_future) {
    const size_t next = (96 + 16 + sizeof(black_jpeg) + 40 + 3) & ~size_t(3);
    bytes[next + 16 + 3] ^= 1;
  }
  Clock clock; Storage storage(clock, bytes); Runtime runtime(clock);
  Painter painter(clock); Output output; mvb::Engine engine(storage, painter, runtime, output);
  engine.begin(); Requests requests(engine, output); ready(engine, requests);
  assert(requests.command("{\"method\":\"pause\"}")["ok"] == true);
  assert(requests.command("{\"method\":\"set_prefetch_options\",\"enabled\":true}")["ok"] == true);
  painter.fail_completion = failed_completion;
  painter.fail_row = !failed_completion && !corrupt_future;
  assert(requests.command("{\"method\":\"restart\"}")["ok"] == true); engine.tick();
  auto status = requests.command("{\"method\":\"get_status\"}");
  auto benchmark = requests.command("{\"method\":\"get_benchmark\"}");
  assert(status["result"]["state"] == "error" && storage_reads == 0);
  assert(status["result"]["error"] == (corrupt_future ? "crc_mismatch" : "display_failed"));
  assert(benchmark["result"]["frames"] == (corrupt_future ? 1 : 0));
  assert(benchmark["result"]["prefetch"]["enabled"] == false);
#else
  (void)corrupt_future; (void)failed_completion;
#endif
}
int main() { engine_controls(); failures(true, false); failures(false, false); failures(false, true); }
'''


def renamed_adapter(path):
    source = path.read_text()
    before, after = source.rsplit("}", 1)
    return before + "return 0;\n}" + after


@unittest.skipUnless(CXX, "A C++ compiler is required")
class FramePrefetchTests(unittest.TestCase):
    def compile_run(self, source, *, engine=False, enabled=True, sanitizer="address,undefined"):
        dependency = ROOT / "firmware/.pio/libdeps/cyd_ili9341"
        with tempfile.TemporaryDirectory(prefix="muse-prefetch-") as directory:
            directory = Path(directory)
            (directory / "core-adapters.hpp").write_text(renamed_adapter(ROOT / "firmware/native/core_tests.cpp"))
            if engine:
                adapter = renamed_adapter(ROOT / "firmware/native/profile_tests.cpp")
                adapter = adapter.replace("namespace {", "namespace {\nstd::atomic<int> storage_reads{0}, overlapping_rows{0};\nstd::atomic<unsigned> total_storage_reads{0};", 1)
                adapter = adapter.replace("uint64_t time = 10000000;", "std::atomic<uint64_t> time{10000000};")
                adapter = adapter.replace("size_t read(uint8_t *data, size_t count) override {", "size_t read(uint8_t *data, size_t count) override {\n    assert(storage_reads.fetch_add(1) == 0); ++total_storage_reads;\n    std::this_thread::sleep_for(std::chrono::milliseconds(2));")
                adapter = adapter.replace("return n;\n  }", "--storage_reads; return n;\n  }", 1)
                adapter = adapter.replace("void close() override {}", "void close() override { assert(storage_reads == 0); }")
                adapter = adapter.replace("bool row(int x, int y, const uint16_t *, int n) override {", "bool row(int x, int y, const uint16_t *, int n) override {\n    if (storage_reads) ++overlapping_rows;\n    std::this_thread::sleep_for(std::chrono::milliseconds(1));")
                # Every main storage operation is forbidden while a read owns SD.
                for operation in ["mount", "mounted", "capacity", "free_bytes", "read_file", "write_file", "exists", "mkdir_library", "remove_owned"]:
                    lines = adapter.splitlines()
                    for i, line in enumerate(lines):
                        if f" {operation}(" in line and "override {" in line:
                            lines[i] = line.replace("override {", "override { assert(storage_reads == 0);")
                    adapter = "\n".join(lines) + "\n"
                (directory / "profile-adapters.hpp").write_text("#include <thread>\n#include <chrono>\n" + adapter)
            (directory / "driver.cpp").write_text(source)
            executable = directory / "test"
            command = [CXX, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", "-g", "-pthread",
                       f"-fsanitize={sanitizer}", "-fno-omit-frame-pointer", "-I", str(ROOT / "firmware/include"),
                       str(directory / "driver.cpp"), "-o", str(executable)]
            if enabled:
                command.append("-DMVB_FRAME_PREFETCH")
            if engine:
                if not (dependency / "JPEGDEC/src/JPEGDEC.cpp").is_file():
                    self.skipTest("Install the pinned PlatformIO dependencies")
                command += ["-DMVB_FRAME_RECORD_READ", "-D__LINUX__", "-DNO_SIMD", "-include", str(ROOT / "firmware/include/jpeg_portable.hpp"),
                            "-I", str(dependency / "ArduinoJson/src"), "-I", str(dependency / "JPEGDEC/src"),
                            str(dependency / "JPEGDEC/src/JPEGDEC.cpp"),
                            "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-sign-compare", "-Wno-type-limits"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_container_blocked_read_join_release_and_buffer_ownership(self):
        self.compile_run(WORKER_SOURCE)

    def test_actual_codec_engine_controls_retained_redraw_upload_and_failure_cleanup(self):
        self.compile_run(ENGINE_SOURCE, engine=True)

    def test_disabled_build_preserves_synchronous_fallback(self):
        self.compile_run(ENGINE_SOURCE, engine=True, enabled=False)

    @unittest.skipUnless(os.environ.get("MVB_RUN_TSAN"), "Set MVB_RUN_TSAN=1 on a host supporting ThreadSanitizer")
    def test_worker_thread_sanitizer(self):
        self.compile_run(WORKER_SOURCE, sanitizer="thread")

    @unittest.skipUnless(os.environ.get("MVB_RUN_TSAN"), "Set MVB_RUN_TSAN=1 on a host supporting ThreadSanitizer")
    def test_engine_thread_sanitizer(self):
        self.compile_run(ENGINE_SOURCE, engine=True, sanitizer="thread")


if __name__ == "__main__":
    unittest.main()
