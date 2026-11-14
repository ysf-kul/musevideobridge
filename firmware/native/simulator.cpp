#include "engine.hpp"
#include "messages.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <openssl/evp.h>
#include <poll.h>
#include <unistd.h>
#include <vector>

namespace fs = std::filesystem;
namespace {
class NativeFile : public mvb::File {
  std::fstream file_;
  fs::path path_;
  bool writable_ = false;

public:
  bool open(fs::path path, bool write, bool resume = false) {
    close();
    path_ = std::move(path);
    writable_ = write;
    file_.open(path_,
               std::ios::binary |
                   (write ? (std::ios::in | std::ios::out |
                             (resume ? std::ios::openmode(0) : std::ios::trunc))
                          : std::ios::in));
    return file_.is_open();
  }
  uint64_t size() override {
    std::error_code error;
    auto n = fs::file_size(path_, error);
    return error ? 0 : n;
  }
  bool seek(uint32_t offset) override {
    file_.clear();
    file_.seekg(offset);
    if (writable_)
      file_.seekp(offset);
    return bool(file_);
  }
  size_t read(uint8_t *b, size_t n) override {
    file_.read(reinterpret_cast<char *>(b), std::streamsize(n));
    return size_t(file_.gcount());
  }
  size_t write(const uint8_t *b, size_t n) override {
    file_.write(reinterpret_cast<const char *>(b), std::streamsize(n));
    return file_ ? n : 0;
  }
  bool flush() override {
    file_.flush();
    return bool(file_);
  }
  void close() override {
    if (file_.is_open())
      file_.close();
    file_.clear();
  }
};
class NativeStorage : public mvb::Storage {
  fs::path root_;
  NativeFile reader_, writer_;
  fs::path path(const char *name) {
    return root_ / std::string(name).substr(1);
  }

public:
  explicit NativeStorage(fs::path root) : root_(fs::canonical(root)) {}
  bool mount() override { return fs::is_directory(root_); }
  bool mounted() override { return fs::is_directory(root_); }
  uint64_t capacity() override { return fs::space(root_).capacity; }
  uint64_t free_bytes() override { return fs::space(root_).available; }
  mvb::File *read_file(const char *name) override {
    auto p = path(name);
    if (fs::is_symlink(p))
      return nullptr;
    return reader_.open(p, false) ? &reader_ : nullptr;
  }
  mvb::File *write_file(const char *name, bool resume) override {
    auto p = path(name);
    if (fs::is_symlink(p) || (!resume && fs::exists(p)))
      return nullptr;
    return writer_.open(p, true, resume) ? &writer_ : nullptr;
  }
  bool exists(const char *name) override { return fs::exists(path(name)); }
  bool rename_new(const char *from, const char *to) override {
    auto a = path(from), b = path(to);
    if (fs::exists(b))
      return false;
    std::error_code e;
    fs::rename(a, b, e);
    return !e;
  }
  bool remove_owned(const char *name) override {
    if (std::strncmp(name, "/muse-video/.upload", 19))
      return false;
    std::error_code e;
    fs::remove(path(name), e);
    return !e;
  }
  bool mkdir_library() override {
    std::error_code e;
    fs::create_directories(root_ / "muse-video", e);
    return !e;
  }
  bool queue_store_available() const override { return true; }
  bool read_queue(unsigned slot, uint8_t *bytes, size_t n) override {
    auto p = root_ / "muse-video" / ("queue." + std::to_string(slot) + ".bin");
    if (slot > 1 || fs::is_symlink(p) || !fs::exists(p) || fs::file_size(p) != n) return false;
    std::ifstream f(p, std::ios::binary); f.read(reinterpret_cast<char *>(bytes), n); return bool(f);
  }
  bool write_queue(unsigned slot, const uint8_t *bytes, size_t n) override {
    if (slot > 1 || n != mvb::QueueRecordBytes || !mkdir_library()) return false;
    auto p = root_ / "muse-video" / ("queue." + std::to_string(slot) + ".bin");
    if (fs::is_symlink(p)) return false;
    if (fs::exists(p)) {
      uint8_t old[mvb::QueueRecordBytes]; mvb::Playlist queue; uint32_t generation;
      if (!read_queue(slot, old, sizeof(old)) || !mvb::decode_queue(old, queue, generation)) {
        auto time = std::chrono::steady_clock::now().time_since_epoch().count();
        fs::rename(p, p.parent_path() / ("queue.unrecognized-" + std::to_string(time) + ".bin"));
      }
    }
    std::ofstream f(p, std::ios::binary | std::ios::trunc);
    f.write(reinterpret_cast<const char *>(bytes), n); f.flush(); return bool(f);
  }
  size_t video_ids(char (*ids)[65], size_t capacity) override {
    auto dir = root_ / "muse-video"; if (!fs::is_directory(dir)) return 0;
    std::vector<std::string> names;
    for (const auto &item : fs::directory_iterator(dir)) {
      auto name = item.path().filename().string();
      if (item.is_regular_file() && !item.is_symlink() && name.size() == 68 && name.substr(64) == ".mvb" && mvb::digest_valid(name.substr(0, 64).c_str())) names.push_back(name.substr(0, 64));
    }
    std::sort(names.begin(), names.end());
    size_t count = std::min(capacity, names.size());
    for (size_t i = 0; i < count; ++i) std::memcpy(ids[i], names[i].c_str(), 65);
    return count;
  }
  bool first_video(char *out, size_t n, const char *ignored) override {
    auto dir = root_ / "muse-video";
    if (!fs::is_directory(dir))
      return false;
    std::ifstream active(dir / "active.json");
    JsonDocument d;
    if (active && fs::file_size(dir / "active.json") <= 512 &&
        !deserializeJson(d, active)) {
      const char *hash = d["sha256"].as<const char *>();
      if (mvb::selection_valid(d)) {
        std::snprintf(out, n, "/muse-video/%s.mvb", hash);
        if (exists(out) && std::strcmp(out, ignored))
          return true;
      }
    }
    std::string best;
    for (const auto &item : fs::directory_iterator(dir)) {
      auto name = item.path().filename().string();
      if (item.is_regular_file() && !item.is_symlink() && name.size() == 68 &&
          name.substr(64) == ".mvb" &&
          mvb::digest_valid(name.substr(0, 64).c_str())) {
        auto candidate = "/muse-video/" + name;
        if (candidate != ignored && (best.empty() || candidate < best))
          best = candidate;
      }
    }
    if (best.empty())
      return false;
    std::snprintf(out, n, "%s", best.c_str());
    return true;
  }
  bool checkpoint_file(const char *name, const uint8_t *b, size_t n,
                       bool append) override {
    auto p = path(name);
    if (std::strncmp(name, "/muse-video/.upload", 19) || fs::is_symlink(p) ||
        (!append && fs::exists(p)))
      return false;
    std::ofstream f(p, std::ios::binary |
                           (append ? std::ios::app : std::ios::trunc));
    f.write(reinterpret_cast<const char *>(b), std::streamsize(n));
    f.flush();
    return bool(f);
  }
  bool select(const char *hash) override {
    auto dir = root_ / "muse-video";
    auto active = dir / "active.json";
    if (fs::exists(active)) {
      std::ifstream f(active);
      JsonDocument d;
      if (fs::file_size(active) > 512 || deserializeJson(d, f) ||
          !mvb::selection_valid(d))
        return false;
      f.close();
      auto time = std::chrono::steady_clock::now().time_since_epoch().count();
      fs::rename(active,
                 dir / ("active.previous-" + std::to_string(time) + ".json"));
    }
    std::ofstream f(active);
    JsonDocument d;
    d["schema_version"] = 1;
    d["sha256"] = hash;
    d["filename"] = std::string(hash) + ".mvb";
    serializeJson(d, f);
    f.flush();
    return bool(f);
  }
};
class NativePainter : public mvb::Painter {
  fs::path output_;
  std::vector<uint16_t> pixels_ = std::vector<uint16_t>(320 * 240);
  bool dirty_ = false;

public:
  explicit NativePainter(const fs::path &root) : output_(root / ".simulator") {
    fs::create_directories(output_);
  }
  bool row(int x, int y, const uint16_t *p, int n) override {
    if (x < 0 || y < 0 || y >= 240 || n < 0 || x + n > 320)
      return false;
    std::copy(p, p + n, pixels_.begin() + y * 320 + x);
    dirty_ = true;
    return true;
  }
  void clear(mvb::Rect r) override {
    if (r.w <= 0 || r.h <= 0)
      return;
    for (int y = r.y; y < r.y + r.h; y++)
      for (int x = r.x; x < r.x + r.w; x++)
        if (x >= 0 && x < 320 && y >= 0 && y < 240)
          pixels_[y * 320 + x] = 0;
    dirty_ = true;
  }
  void waiting(bool card, bool connected, const char *error) override {
    clear({0, 0, 320, 240});
    JsonDocument d;
    using mvb::messages::Key;
    d["simulator"] = true;
    d["title"] = mvb::messages::english(Key::Title);
    d["message"] = mvb::messages::english(Key::Waiting);
    d["instructions"] = mvb::messages::english(Key::Instructions);
    d["card_status"] =
        mvb::messages::english(card ? Key::SdReady : Key::SdMissing);
    d["connection_status"] =
        mvb::messages::english(connected ? Key::Connected : Key::Disconnected);
    if (error)
      d["error"] = error;
    else
      d["error"] = nullptr;
    std::ofstream f(output_ / "waiting.json");
    serializeJson(d, f);
  }
  void save() {
    if (!dirty_)
      return;
    dirty_ = false;
    std::ofstream f(output_ / "frame.ppm", std::ios::binary);
    f << "P6\n320 240\n255\n";
    for (auto p : pixels_) {
      char b[3] = {char(((p >> 11) & 31) * 255 / 31),
                   char(((p >> 5) & 63) * 255 / 63), char((p & 31) * 255 / 31)};
      f.write(b, 3);
    }
  }
};
class NativeRuntime : public mvb::Runtime {
  EVP_MD_CTX *hash_ = EVP_MD_CTX_new();

public:
  ~NativeRuntime() override { EVP_MD_CTX_free(hash_); }
  uint64_t now() override {
    return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::steady_clock::now().time_since_epoch())
                        .count());
  }
  uint32_t session_nonce() override {
    return uint32_t(now() ^ uint64_t(getpid()));
  }
  bool hash_begin() override {
    return hash_ && EVP_DigestInit_ex(hash_, EVP_sha256(), nullptr) == 1;
  }
  bool hash_update(const uint8_t *b, size_t n) override {
    return EVP_DigestUpdate(hash_, b, n) == 1;
  }
  bool hash_finish(char out[65]) override {
    uint8_t b[32];
    unsigned n = 0;
    if (EVP_DigestFinal_ex(hash_, b, &n) != 1 || n != 32)
      return false;
    for (int i = 0; i < 32; i++)
      std::snprintf(out + 2 * i, 3, "%02x", b[i]);
    return true;
  }
  void telemetry(JsonObject r) override {
    r["simulator"] = true;
    r["execution_environment"] = "native_host_test_directory";
    r["uptime_source"] = "host_monotonic_clock";
    r["free_heap_bytes"] = nullptr;
    r["minimum_free_heap_bytes"] = nullptr;
    r["cpu_utilization_percent"] = nullptr;
    r["temperature_c"] = nullptr;
    r["psram_bytes"] = nullptr;
    r["flash_bytes"] = nullptr;
    r["cpu_mhz"] = nullptr;
    r["unavailable_reason"] = "not_an_ESP32";
    r["wifi_status"] = "not_emulated";
    r["wifi_rssi_dbm"] = nullptr;
    r["uptime_us"] = now();
  }
};
class NativeOutput : public mvb::Output {
  uint8_t encoded_[mvb::MaxWire]{};

public:
  bool send(const mvb::Frame &frame) override {
    size_t n = mvb::encode(frame, encoded_);
    size_t offset = 0;
    while (offset < n) {
      ssize_t written = ::write(STDOUT_FILENO, encoded_ + offset, n - offset);
      if (written <= 0)
        return false;
      offset += size_t(written);
    }
    return true;
  }
};
} // namespace
int main(int argc, char **argv) {
  if (argc != 2 || !fs::is_directory(argv[1])) {
    std::fprintf(
        stderr,
        "Native simulator requires an existing isolated test directory.\n");
    return 2;
  }
  std::fprintf(stderr, "Muse Video Bridge native simulator: host "
                       "files/decoder, no ESP32 or SPI emulation.\n");
  NativeStorage storage(argv[1]);
  NativePainter painter(argv[1]);
  NativeRuntime runtime;
  NativeOutput output;
  auto engine =
      std::make_unique<mvb::Engine>(storage, painter, runtime, output);
  mvb::WireParser parser;
  mvb::Frame frame;
  engine->begin();
  for (;;) {
    pollfd p{STDIN_FILENO, POLLIN, 0};
    int ready = ::poll(&p, 1, 1);
    if (ready < 0)
      return 1;
    if (ready > 0 && (p.revents & (POLLIN | POLLHUP))) {
      uint8_t b[4096];
      ssize_t count = ::read(STDIN_FILENO, b, sizeof(b));
      if (count <= 0)
        break;
      for (ssize_t i = 0; i < count; i++)
        if (parser.feed(b[i], runtime.now(), frame))
          engine->command(frame);
    }
    engine->tick();
    painter.save();
  }
  return 0;
}
