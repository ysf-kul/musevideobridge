#include "checked_stdio.hpp"
#include "dma_ownership.hpp"
#include "engine.hpp"
#include "messages.hpp"
#include "theme.hpp"
#include <Arduino.h>
#ifdef MVB_USE_IDF_SDSPI
#include "idf_sd.hpp"
#else
#include <SD.h>
#endif
#include <SPI.h>
#include <TFT_eSPI.h>
#include <atomic>
#include <cerrno>
#include <driver/spi_master.h>
#include <esp_chip_info.h>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <esp_wifi.h>
#include <fcntl.h>
#include <mbedtls/sha256.h>
#include <sys/stat.h>
#include <unistd.h>
#ifndef MVB_UART_BAUD
#define MVB_UART_BAUD 115200
#endif
#ifndef MVB_DMA_QUEUE_DEPTH
#define MVB_DMA_QUEUE_DEPTH 1
#endif
static_assert(MVB_DMA_QUEUE_DEPTH == 1 || MVB_DMA_QUEUE_DEPTH == 2);

namespace {
#ifdef MVB_USE_IDF_SDSPI
mvb::IdfSd SD;
#endif
std::atomic<uint32_t> uart_overruns{0}, uart_errors{0}, wire_errors{0},
    inbound_drops{0}, reply_drops{0}, event_drops{0};
std::atomic<int> sd_errno{0};
#ifdef MVB_ADAPTIVE_LOOP_YIELD
// Accessed only by the main loop/its Runtime command handlers. Counters are
// cumulative since boot; interval selection does not touch media or SPI.
struct AdaptiveLoopDiagnostics {
#ifdef MVB_QUALIFIED_FAST_DEFAULTS
  uint32_t idle_yield_us = maximum_interval() >= 1000000  ? 1000000
                           : maximum_interval() >= 250000 ? 250000
                                                          : 50000;
#else
  uint32_t idle_yield_us = 50000;
#endif
  uint64_t last_idle_yield = 0, busy_sleeps = 0, quiet_sleeps = 0, yields = 0,
           sleep_us = 0;
  static constexpr uint32_t maximum_interval() {
#ifdef CONFIG_ESP_TASK_WDT_TIMEOUT_S
    return uint32_t(
        std::min(uint64_t(1000000),
                 uint64_t(CONFIG_ESP_TASK_WDT_TIMEOUT_S) * 1000000 / 4));
#else
    // Do not lengthen the existing interval without a known configured bound.
    return 50000;
#endif
  }
  bool configure(uint32_t interval) {
    if ((interval != 50000 && interval != 250000 && interval != 1000000) ||
        interval > maximum_interval())
      return false;
    idle_yield_us = interval;
    return true;
  }
  bool should_sleep(uint64_t started, uint64_t finished) const {
    return finished - started < 1000 ||
           finished - last_idle_yield >= idle_yield_us;
  }
  void completed_sleep(uint64_t started, uint64_t finished, bool quiet) {
    if (quiet)
      ++quiet_sleeps;
    else
      ++busy_sleeps;
    sleep_us += finished - started;
    last_idle_yield = finished;
  }
  void status(JsonObject r) const {
    r["available"] = true;
    r["idle_yield_us"] = idle_yield_us;
    r["busy_sleeps"] = busy_sleeps;
    r["quiet_sleeps"] = quiet_sleeps;
    r["yields"] = yields;
    r["sleep_us"] = sleep_us;
    r["sleep_timing_scope"] = "delay_wall_time_us";
    r["counter_scope"] = "since_boot_main_loop";
    r["maximum_allowed_idle_yield_us"] = maximum_interval();
#ifdef CONFIG_ESP_TASK_WDT_TIMEOUT_S
    r["task_wdt_config_timeout_us"] =
        uint64_t(CONFIG_ESP_TASK_WDT_TIMEOUT_S) * 1000000;
#else
    r["task_wdt_config_timeout_us"] = nullptr;
#endif
    r["task_wdt_timeout_scope"] = "compile_time_sdk_config";
#if defined(CONFIG_ESP_TASK_WDT_EN) && CONFIG_ESP_TASK_WDT_EN
    r["task_wdt_enabled_config"] = true;
#else
    r["task_wdt_enabled_config"] = false;
#endif
#if defined(CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU0) &&                       \
    CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU0
    r["idle_cpu0_monitored_config"] = true;
#else
    r["idle_cpu0_monitored_config"] = false;
#endif
#if defined(CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU1) &&                       \
    CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU1
    r["idle_cpu1_monitored_config"] = true;
#else
    r["idle_cpu1_monitored_config"] = false;
#endif
  }
} adaptive_loop;
#endif
void record_sd_error() { sd_errno.store(errno ? errno : EIO); }
class SdFile : public mvb::File {
  FILE *file_ = nullptr;
  mvb::IoProfile profile_;
  uint32_t read_position_ = 0;
  bool read_position_known_ = false;
  uint8_t *read_buffer_ = nullptr;
  bool direct_read_ = false;

public:
  bool *mounted = nullptr;
  ~SdFile() override {
    close();
    if (read_buffer_)
      heap_caps_free(read_buffer_);
  }
  bool open(const char *path, const char *mode, bool exclusive = false) {
    close();
    char vfs_path[128];
    int length = std::snprintf(vfs_path, sizeof(vfs_path), "/sd%s", path);
    if (length < 0 || size_t(length) >= sizeof(vfs_path))
      return false;
    errno = 0;
    if (exclusive) {
      int fd = ::open(vfs_path, O_CREAT | O_EXCL | O_RDWR, 0666);
      if (fd < 0) {
        record_sd_error();
        return false;
      }
      file_ = ::fdopen(fd, mode);
      if (!file_) {
        record_sd_error();
        ::close(fd);
      }
    } else {
      file_ = std::fopen(vfs_path, mode);
      if (!file_)
        record_sd_error();
    }
#ifdef MVB_SD_POSIX_READ
    direct_read_ = file_ && !std::strcmp(mode, "rb");
    if (direct_read_ && std::setvbuf(file_, nullptr, _IONBF, 0) != 0) {
      close();
      return false;
    }
#endif
#ifdef MVB_STDIO_READ_BUFFER_BYTES
    if (file_ && !std::strcmp(mode, "rb")) {
      if (!read_buffer_ &&
          heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) >=
              32768 + MVB_STDIO_READ_BUFFER_BYTES)
        read_buffer_ = static_cast<uint8_t *>(
            heap_caps_malloc(MVB_STDIO_READ_BUFFER_BYTES,
                             MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
      if (read_buffer_ &&
          std::setvbuf(file_, reinterpret_cast<char *>(read_buffer_), _IOFBF,
                       MVB_STDIO_READ_BUFFER_BYTES) != 0) {
        close();
        return false;
      }
    }
#endif
    read_position_ = 0;
    read_position_known_ = file_ && !std::strcmp(mode, "rb");
    return file_ != nullptr;
  }
  size_t read_buffer_bytes() const {
#ifdef MVB_STDIO_READ_BUFFER_BYTES
    return read_buffer_ ? MVB_STDIO_READ_BUFFER_BYTES : 0;
#else
    return 0;
#endif
  }
  uint64_t size() override {
    errno = file_ ? 0 : EBADF;
    struct stat info{};
    if (!file_ || ::fstat(::fileno(file_), &info) != 0 || info.st_size < 0) {
      record_sd_error();
      return 0;
    }
    return uint64_t(info.st_size);
  }
  bool seek(uint32_t offset) override {
#ifdef MVB_SD_SEEK_CACHE
    // A sequential record already starts at the current logical stdio offset.
    // Avoid fseek discarding its buffered sector. Random/index seeks still use
    // the normal checked driver path, and writes never use this shortcut.
    if (file_ && read_position_known_ && offset == read_position_)
      return true;
#endif
    uint64_t start = esp_timer_get_time();
    errno = file_ ? 0 : EBADF;
    bool ok = file_ &&
              (direct_read_ ? ::lseek(::fileno(file_), off_t(offset),
                                      SEEK_SET) == off_t(offset)
                            : std::fseek(file_, long(offset), SEEK_SET) == 0);
    if (ok && read_position_known_)
      read_position_ = offset;
    else if (!ok)
      read_position_known_ = false;
    if (!ok)
      record_sd_error();
    profile_.seek_us += uint64_t(esp_timer_get_time()) - start;
    profile_.seek_calls++;
    return ok;
  }
  size_t read(uint8_t *data, size_t n) override {
    uint64_t start = esp_timer_get_time();
    errno = file_ ? 0 : EBADF;
    ssize_t direct =
        file_ && direct_read_ ? ::read(::fileno(file_), data, n) : 0;
    size_t got = !file_         ? 0
                 : direct_read_ ? (direct < 0 ? 0 : size_t(direct))
                                : std::fread(data, 1, n, file_);
    if (read_position_known_)
      read_position_ += uint32_t(got);
    profile_.read_us += uint64_t(esp_timer_get_time()) - start;
    profile_.read_bytes += got;
    profile_.read_calls++;
    if (n && got != n) {
      record_sd_error();
      if (mounted)
        *mounted = false;
    }
    return got;
  }
  size_t write(const uint8_t *data, size_t n) override {
    read_position_known_ = false;
    direct_read_ = false;
    uint64_t start = esp_timer_get_time();
    errno = file_ ? 0 : EBADF;
    size_t got = file_ ? std::fwrite(data, 1, n, file_) : 0;
    profile_.write_us += uint64_t(esp_timer_get_time()) - start;
    profile_.written_bytes += got;
    if (got != n)
      record_sd_error();
    return got;
  }
  bool flush() override {
    // Arduino File.flush() discards fflush/fsync return codes. Use the mounted
    // FAT VFS directly so failed checkpoints can never advertise durability.
    errno = 0;
    uint64_t start = esp_timer_get_time();
    bool ok = mvb::sync_file(file_);
    profile_.flush_us += uint64_t(esp_timer_get_time()) - start;
    if (!ok)
      record_sd_error();
    return ok;
  }
  mvb::IoProfile io_profile() const override {
    auto result = profile_;
    result.available = true;
    return result;
  }
  void close() override {
    if (file_) {
      errno = 0;
      if (std::fclose(file_) != 0)
        record_sd_error();
      file_ = nullptr;
    }
    read_position_known_ = false;
    direct_read_ = false;
  }
};
SPIClass sd_bus(VSPI);
class SdStorage : public mvb::Storage {
  SdFile reader_, writer_;
  bool mounted_ = false;
#ifdef MVB_QUALIFIED_DEFAULTS
  uint32_t clock_hz_ = 20000000;
#else
  uint32_t clock_hz_ = 10000000;
#endif

public:
  size_t read_buffer_bytes() const { return reader_.read_buffer_bytes(); }
  bool mount() override {
    reader_.mounted = &mounted_;
    writer_.mounted = &mounted_;
    reader_.close();
    writer_.close();
#ifdef MVB_USE_IDF_SDSPI
    if (!SD.end()) {
      mounted_ = false;
      return false;
    }
#else
    SD.end();
    sd_bus.begin(18, 19, 23, 5);
#endif
    // Never format; original reference uses its independent VSPI card wiring.
    mounted_ = SD.begin(5, sd_bus, clock_hz_, "/sd", 5, false) &&
               SD.cardType() != CARD_NONE;
    return mounted_;
  }
  bool mounted() override { return mounted_; }
  uint32_t clock_hz() const { return clock_hz_; }
  bool configure_clock(uint32_t hz) override {
    clock_hz_ = hz;
    return mount();
  }
  uint64_t capacity() override { return mounted_ ? SD.totalBytes() : 0; }
  uint64_t free_bytes() override {
    if (!mounted_)
      return 0;
    uint64_t total = SD.totalBytes(), used = SD.usedBytes();
    return total >= used ? total - used : 0;
  }
  mvb::File *read_file(const char *path) override {
    return reader_.open(path, "rb") ? &reader_ : nullptr;
  }
  mvb::File *write_file(const char *path, bool resume) override {
    return writer_.open(path, resume ? "r+b" : "w+b", !resume) ? &writer_
                                                               : nullptr;
  }
  bool exists(const char *path) override {
    bool yes = mounted_ && SD.exists(path);
    if (SD.cardType() == CARD_NONE)
      mounted_ = false;
    return yes;
  }
  bool rename_new(const char *from, const char *to) override {
    return !SD.exists(to) && SD.rename(from, to);
  }
  bool remove_owned(const char *path) override {
    if (std::strncmp(path, "/muse-video/.upload", 19))
      return false;
    return !SD.exists(path) || SD.remove(path);
  }
  bool mkdir_library() override {
    return SD.exists("/muse-video") || SD.mkdir("/muse-video");
  }
  bool first_video(char *out, size_t n, const char *ignored) override {
    ::File active = SD.open("/muse-video/active.json");
    if (active && active.size() <= 512) {
      JsonDocument d;
      auto e = deserializeJson(d, active);
      active.close();
      const char *hash = d["sha256"].as<const char *>();
      if (!e && mvb::selection_valid(d)) {
        std::snprintf(out, n, "/muse-video/%s.mvb", hash);
        if (SD.exists(out) && std::strcmp(out, ignored))
          return true;
      }
    }
    char best[96]{};
    ::File dir = SD.open("/muse-video");
    if (!dir || !dir.isDirectory())
      return false;
    ::File item;
    while ((item = dir.openNextFile())) {
      const char *name = item.name();
      const char *slash = std::strrchr(name, '/');
      if (slash)
        name = slash + 1;
      if (!item.isDirectory() && std::strlen(name) == 68 &&
          !std::strcmp(name + 64, ".mvb")) {
        char hash[65];
        std::memcpy(hash, name, 64);
        hash[64] = 0;
        if (mvb::digest_valid(hash)) {
          char path[96];
          std::snprintf(path, sizeof(path), "/muse-video/%s", name);
          if (std::strcmp(path, ignored) &&
              (!best[0] || std::strcmp(path, best) < 0))
            std::snprintf(best, sizeof(best), "%s", path);
        }
      }
      item.close();
    }
    dir.close();
    if (!best[0])
      return false;
    std::snprintf(out, n, "%s", best);
    return true;
  }
  bool checkpoint_file(const char *path, const uint8_t *data, size_t n,
                       bool append) override {
    if (std::strncmp(path, "/muse-video/.upload", 19) ||
        (!append && SD.exists(path)))
      return false;
    SdFile f;
    if (!f.open(path, append ? "ab" : "w+b", !append))
      return false;
    bool ok = f.write(data, n) == n;
    ok = f.flush() && ok;
    f.close();
    return ok;
  }
  bool select(const char *digest) override {
    ::File old = SD.open("/muse-video/active.json");
    if (old) {
      if (old.size() > 512) {
        old.close();
        return false;
      }
      JsonDocument d;
      auto error = deserializeJson(d, old);
      old.close();
      if (error || d["schema_version"] != 1 || !mvb::digest_valid(d["sha256"]))
        return false;
      char previous[80];
      std::snprintf(previous, sizeof(previous),
                    "/muse-video/active.previous-%llu.json",
                    static_cast<unsigned long long>(esp_timer_get_time()));
      if (!rename_new("/muse-video/active.json", previous))
        return false;
    }
    if (SD.exists("/muse-video/.active.part"))
      return false;
    SdFile f;
    if (!f.open("/muse-video/.active.part", "w+b", true))
      return false;
    JsonDocument d;
    d["schema_version"] = 1;
    d["sha256"] = digest;
    char filename[69];
    std::snprintf(filename, sizeof(filename), "%s.mvb", digest);
    d["filename"] = filename;
    char json[512];
    size_t length = serializeJson(d, json, sizeof(json));
    bool ok =
        length == measureJson(d) &&
        f.write(reinterpret_cast<const uint8_t *>(json), length) == length;
    ok = f.flush() && ok;
    f.close();
    return ok &&
           rename_new("/muse-video/.active.part", "/muse-video/active.json");
  }
  void recovery_info(JsonObject r) {
    r["available"] = mounted_;
    r["legacy_journal_exists"] = exists("/muse-video/.upload.meta");
    if (!r["legacy_journal_exists"].as<bool>())
      return;
    SdFile meta;
    uint8_t b[96];
    bool valid = meta.open("/muse-video/.upload.meta", "rb");
    uint64_t bytes = valid ? meta.size() : 0;
    valid = valid && bytes >= 96 && bytes <= 3200000 && bytes % 96 == 0 &&
            meta.seek(uint32_t(bytes - 96)) && meta.read(b, 96) == 96;
    meta.close();
    valid = valid && !std::memcmp(b, "MVBUP1\0\0", 8) &&
            mvb::crc32(b, 88) == mvb::get32(b + 88) && !mvb::get32(b + 92) &&
            mvb::get32(b + 80) <= mvb::get64(b + 72);
    char hash[65]{};
    if (valid) {
      std::memcpy(hash, b + 8, 64);
      valid = mvb::digest_valid(hash);
    }
    r["legacy_journal_valid"] = valid;
    if (!valid)
      return;
    r["sha256"] = hash;
    r["size"] = mvb::get64(b + 72);
    r["checkpointed_offset"] = mvb::get32(b + 80);
    char partial[96];
    std::snprintf(partial, sizeof(partial), "/muse-video/.upload-%s.part",
                  hash);
    r["partial_exists"] = exists(partial);
    SdFile media;
    if (media.open(partial, "rb"))
      r["partial_bytes"] = media.size();
    else
      r["partial_bytes"] = nullptr;
  }
} storage;

class TftPainter : public mvb::Painter {
  TFT_eSPI tft_;
  mvb::RenderOptions options_;
  mvb::DisplayProfile profile_;
  bool frame_active_ = false, dma_failed_ = false;
  bool dma_completion_unknown_ = false;
  bool integrity_active_ = false;
  mvb::IntegrityProfile integrity_profile_;
  mvb::Rect integrity_region_{};
  int integrity_next_x_ = 0, integrity_next_y_ = 0, integrity_band_height_ = 0;
  uint32_t integrity_crc_state_ = 0xffffffffu;
  spi_device_handle_t dma_device_ = nullptr;
  bool dma_bus_owned_ = false;
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
  static constexpr unsigned DmaBufferSlots = 3;
#else
  static constexpr unsigned DmaBufferSlots = 2;
#endif
  spi_transaction_t dma_transactions_[DmaBufferSlots]{};
  mvb::BasicDmaOwnership<DmaBufferSlots> dma_ownership_{MVB_DMA_QUEUE_DEPTH};
  uint16_t *dma_buffers_[DmaBufferSlots]{};
  void *dma_allocations_[DmaBufferSlots]{};
  unsigned allocated_slots_ = 0;
  uint64_t direct_submissions_ = 0, copied_submissions_ = 0;
  unsigned next_buffer_ = 0;
  uint32_t observed_clock_ = 0;
  uint32_t observed_command_clock_ = 0, pixel_divider_ = 0,
           command_divider_ = 0;
  bool stream_active_ = false;
  mvb::Rect stream_region_{};
  int stream_next_y_ = 0;
  uint16_t expected_pixels_[9]{};
  bool expected_valid_[9]{};
  uint64_t completed_display_frames_ = 0, verified_display_frames_ = 0,
           video_samples_checked_ = 0, video_samples_mismatched_ = 0,
           video_verification_us_ = 0;
  int first_bad_x_ = -1, first_bad_y_ = -1;
  uint16_t first_bad_expected_ = 0, first_bad_actual_ = 0;
  static constexpr size_t BufferPixels = MAX_BUFFERED_PIXELS;
  alignas(4) uint16_t tail_[322]{};
  static uint32_t register_clock_hz() {
    uint32_t reg = READ_PERI_REG(SPI_CLOCK_REG(HSPI));
    return reg & (1u << 31) ? getApbFrequency()
                            : getApbFrequency() / (((reg >> 18) & 0x1fff) + 1) /
                                  (((reg >> 12) & 0x3f) + 1);
  }
  void refresh_dividers() {
    auto bus = TFT_eSPI::getSPIinstance().bus();
    pixel_divider_ = spiFrequencyToClockDiv(bus, options_.spi_hz);
    command_divider_ = spiFrequencyToClockDiv(bus, options_.command_spi_hz);
  }
  void set_window(int x, int y, int width, int height) {
    WRITE_PERI_REG(SPI_CLOCK_REG(HSPI), command_divider_);
    tft_.setAddrWindow(x, y, width, height);
    observed_command_clock_ = register_clock_hz();
    profile_.windows++;
    // IDF caches device clock setup. Direct command writes changed the real
    // register, so restore the pixel divider explicitly before every transfer.
    WRITE_PERI_REG(SPI_CLOCK_REG(HSPI), pixel_divider_);
  }
  void block_window(int x, int y, int width, int height) {
    if (stream_active_ && x == stream_region_.x && y == stream_next_y_ &&
        width == stream_region_.w &&
        y + height <= stream_region_.y + stream_region_.h) {
      stream_next_y_ += height;
      return;
    }
    // A new address window must never overtake queued pixel data. Native
    // sequential strips avoid this wait and keep the same controller window.
    if (!wait_dma())
      return;
    stream_active_ = false;
    set_window(x, y, width, height);
  }
  void remember_samples(int x, int y, const uint16_t *pixels, int width,
                        int height, int stride) {
    if (!options_.verify_every_frames)
      return;
    for (unsigned i = 0; i < 9; i++) {
      int px = i % 3 == 0 ? 30 : i % 3 == 1 ? 160 : 290;
      int py = i / 3 == 0 ? 2 : i / 3 == 1 ? 122 : 238;
      if (px < x || px >= x + width || py < y || py >= y + height)
        continue;
      uint16_t pixel = pixels[(py - y) * stride + px - x];
      expected_pixels_[i] =
          options_.big_endian ? uint16_t(pixel << 8 | pixel >> 8) : pixel;
      expected_valid_[i] = true;
    }
  }
  void verify_video_samples() {
    uint64_t at = esp_timer_get_time();
    verified_display_frames_++;
    for (unsigned i = 0; i < 9; i++) {
      if (!expected_valid_[i])
        continue;
      int x = i % 3 == 0 ? 30 : i % 3 == 1 ? 160 : 290;
      int y = i / 3 == 0 ? 2 : i / 3 == 1 ? 122 : 238;
      uint16_t actual = tft_.readPixel(x, y);
      video_samples_checked_++;
      if (actual != expected_pixels_[i]) {
        video_samples_mismatched_++;
        if (first_bad_x_ < 0) {
          first_bad_x_ = x;
          first_bad_y_ = y;
          first_bad_expected_ = expected_pixels_[i];
          first_bad_actual_ = actual;
        }
      }
    }
    video_verification_us_ += uint64_t(esp_timer_get_time()) - at;
  }
  void push_span(const uint16_t *pixels, uint32_t count) {
    if (!options_.big_endian) {
      tft_.pushPixels(pixels, count);
      profile_.pushes++;
      return;
    }
    // The pinned ESP32 bulk writer reads uint32_t words and may fetch two
    // padding pixels for its remainder. Keep clipped/odd rows within owned RAM.
    if ((reinterpret_cast<uintptr_t>(pixels) & 3) == 0 && count >= 32) {
      uint32_t bulk = count & ~uint32_t(31);
      tft_.pushPixels(pixels, bulk);
      profile_.pushes++;
      pixels += bulk;
      count -= bulk;
    }
    while (count) {
      uint32_t n = std::min(uint32_t(320), count);
      uint64_t at = esp_timer_get_time();
      std::memcpy(tail_, pixels, n * 2);
      tail_[n] = tail_[n + 1] = 0;
      profile_.copy_us += uint64_t(esp_timer_get_time()) - at;
      tft_.pushPixels(tail_, n);
      profile_.pushes++;
      count -= n;
      pixels += n;
    }
  }
  bool wait_one_dma() {
    if (dma_completion_unknown_)
      return false;
    if (!dma_ownership_.pending())
      return !dma_failed_;
    uint64_t start = esp_timer_get_time();
    spi_transaction_t *done = nullptr;
    esp_err_t result =
        spi_device_get_trans_result(dma_device_, &done, pdMS_TO_TICKS(100));
    profile_.wait_us += uint64_t(esp_timer_get_time()) - start;
    unsigned slot = decltype(dma_ownership_)::InvalidSlot;
    for (unsigned i = 0; i < allocated_slots_; i++)
      if (done == &dma_transactions_[i])
        slot = i;
    if (result != ESP_OK || !dma_ownership_.complete(slot)) {
      dma_failed_ = true;
      // A consumed unexpected result cannot later become the expected one.
      // Retain every owned buffer and prohibit polling writes until reset.
      dma_completion_unknown_ = true;
    }
    return !dma_failed_;
  }
  bool wait_dma() {
    while (dma_ownership_.pending())
      if (!wait_one_dma())
        return false;
    return !dma_failed_;
  }
  bool release_dma() {
    // Retain buffers and host if completion cannot be established. Clearing
    // bookkeeping or freeing RAM would not stop a potentially active DMA.
    if (dma_completion_unknown_)
      return false;
    wait_dma();
    if (dma_ownership_.pending() || dma_ownership_.active_leases())
      return false;
    if (dma_device_) {
      if (spi_bus_remove_device(dma_device_) != ESP_OK)
        return false;
      dma_device_ = nullptr;
    }
    if (dma_bus_owned_) {
      if (spi_bus_free(HSPI_HOST) != ESP_OK)
        return false;
      dma_bus_owned_ = false;
      // IDF frees/gates the hardware host while SPIClass still caches its bus.
      // Reacquire it before returning to the polled TFT library path.
      auto &spi = TFT_eSPI::getSPIinstance();
      spi.end();
      spi.begin(14, 12, 13, -1);
      tft_.init();
      tft_.setRotation(1);
      tft_.setSwapBytes(!options_.big_endian);
    }
    for (unsigned slot = 0; slot < DmaBufferSlots; ++slot) {
      if (dma_allocations_[slot])
        heap_caps_free(dma_allocations_[slot]);
      dma_allocations_[slot] = nullptr;
      dma_buffers_[slot] = nullptr;
    }
    allocated_slots_ = 0;
    dma_ownership_.reset(true);
    return true;
  }
  bool initialize_dma(uint32_t hz, bool zero_copy) {
    // Two decoder-tile buffers, never a full framebuffer. Fail without
    // rebooting if internal DMA memory or the SPI host cannot be acquired.
    const unsigned slots = zero_copy ? DmaBufferSlots : 2;
    const size_t padding = zero_copy ? 15 : 0;
    if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) <
        32768 + slots * (BufferPixels * sizeof(uint16_t) + padding))
      return false;
    for (unsigned slot = 0; slot < slots; ++slot) {
      // IDF 4.x does not consistently expose heap_caps_aligned_alloc. Keep
      // the allocation base separately; every SDK output starts at 16 bytes.
      dma_allocations_[slot] =
          heap_caps_malloc(BufferPixels * sizeof(uint16_t) + padding,
                           MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
      if (!dma_allocations_[slot]) {
        release_dma();
        return false;
      }
      dma_buffers_[slot] =
          zero_copy
              ? reinterpret_cast<uint16_t *>(
                    (reinterpret_cast<uintptr_t>(dma_allocations_[slot]) + 15) &
                    ~uintptr_t(15))
              : static_cast<uint16_t *>(dma_allocations_[slot]);
    }
    allocated_slots_ = slots;
    spi_bus_config_t bus{};
    bus.mosi_io_num = 13;
    bus.miso_io_num = 12;
    bus.sclk_io_num = 14;
    bus.quadwp_io_num = -1;
    bus.quadhd_io_num = -1;
    bus.max_transfer_sz = BufferPixels * sizeof(uint16_t);
    if (spi_bus_initialize(HSPI_HOST, &bus, SPI_DMA_CH_AUTO) != ESP_OK) {
      release_dma();
      return false;
    }
    dma_bus_owned_ = true;
    spi_device_interface_config_t device{};
    device.clock_speed_hz = int(hz);
    device.mode = 0;
    device.spics_io_num = -1;
    device.queue_size = MVB_DMA_QUEUE_DEPTH;
    device.flags = SPI_DEVICE_NO_DUMMY;
    if (spi_bus_add_device(HSPI_HOST, &device, &dma_device_) != ESP_OK) {
      if (!release_dma())
        return false;
      return false;
    }
    dma_failed_ = false;
    dma_completion_unknown_ = false;
    return true;
  }
  void sample_clock() { observed_clock_ = register_clock_hz(); }

public:
  void begin() {
    pinMode(33, OUTPUT);
    digitalWrite(33, HIGH);
    tft_.init();
    tft_.setRotation(1);
    tft_.setSwapBytes(true);
    refresh_dividers();
    tft_.fillScreen(TFT_BLACK);
    pinMode(21, OUTPUT);
    digitalWrite(21, HIGH);
  }
  bool big_endian() const override { return options_.big_endian; }
  bool configured_render_options(mvb::RenderOptions &options) const override {
    options = options_;
    return true;
  }
  bool output_leases_available() const override {
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
    return options_.zero_copy && options_.mode == mvb::RenderMode::Dma &&
           options_.big_endian && dma_device_ && allocated_slots_ == 3 &&
           !integrity_active_ && !dma_failed_ && !dma_completion_unknown_ &&
           !dma_ownership_.faulted();
#else
    return false;
#endif
  }
  bool acquire_output_lease(size_t pixels, mvb::PixelLease &lease) override {
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
    if (lease || !pixels || pixels > BufferPixels || !output_leases_available())
      return false;
    while (true) {
      for (unsigned step = 0; step < allocated_slots_; ++step) {
        const unsigned slot = (next_buffer_ + step) % allocated_slots_;
        if (!dma_ownership_.writable(slot))
          continue;
        const uint32_t token = dma_ownership_.lease(slot);
        if (!token)
          return false;
        next_buffer_ = slot + 1;
        lease = {dma_buffers_[slot], BufferPixels, slot, token};
        return true;
      }
      // Leases are released explicitly by the codec, never by a DMA wait.
      if (!dma_ownership_.pending() || !wait_one_dma())
        return false;
    }
#else
    (void)pixels;
    (void)lease;
    return false;
#endif
  }
  bool release_output_lease(mvb::PixelLease &lease) override {
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
    if (!lease || lease.slot >= allocated_slots_ ||
        lease.pixels != dma_buffers_[lease.slot] ||
        lease.capacity_pixels != BufferPixels ||
        !dma_ownership_.release_lease(lease.slot, lease.token))
      return false;
    lease = {};
    return true;
#else
    (void)lease;
    return false;
#endif
  }
  bool leased_block(int x, int y, int width, int height,
                    mvb::PixelLease &lease) override {
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
    if (!frame_active_ || !output_leases_available() || !lease ||
        lease.slot >= allocated_slots_ ||
        lease.pixels != dma_buffers_[lease.slot] ||
        lease.capacity_pixels != BufferPixels ||
        !dma_ownership_.matches_lease(lease.slot, lease.token) || x < 0 ||
        y < 0 || width < 1 || height < 1 || x + width > 320 ||
        y + height > 240 || size_t(width) * height > BufferPixels ||
        (reinterpret_cast<uintptr_t>(lease.pixels) & 15))
      return false;
    uint64_t start = esp_timer_get_time();
    while (dma_ownership_.pending() >= dma_ownership_.depth())
      if (!wait_one_dma())
        return false;
    block_window(x, y, width, height);
    if (dma_failed_ || dma_ownership_.faulted())
      return false;
    remember_samples(x, y, lease.pixels, width, height, width);
    auto &transaction = dma_transactions_[lease.slot];
    transaction = {};
    transaction.length = width * height * 16;
    transaction.tx_buffer = lease.pixels;
    if (spi_device_queue_trans(dma_device_, &transaction, portMAX_DELAY) !=
        ESP_OK) {
      dma_failed_ = true;
      return false;
    }
    if (!dma_ownership_.submit_lease(lease.slot, lease.token)) {
      // Accepted hardware work must keep all backing memory until reset.
      dma_failed_ = true;
      dma_completion_unknown_ = true;
      return false;
    }
    lease = {};
    ++direct_submissions_;
    ++profile_.pushes;
    profile_.pixels += uint64_t(width) * height;
    profile_.submit_us += uint64_t(esp_timer_get_time()) - start;
    return true;
#else
    (void)x;
    (void)y;
    (void)width;
    (void)height;
    (void)lease;
    return false;
#endif
  }
  bool begin_integrity_check() override {
    if (integrity_active_ || frame_active_ || dma_ownership_.active_leases() ||
        !wait_dma())
      return false;
    integrity_profile_ = {};
    integrity_profile_.available = true;
    integrity_region_ = {};
    integrity_band_height_ = 0;
    integrity_crc_state_ = 0xffffffffu;
    integrity_active_ = true;
    return true;
  }
  mvb::IntegrityProfile end_integrity_check() override {
    auto result = integrity_profile_;
    result.displayed_crc32 = integrity_crc_state_ ^ 0xffffffffu;
    integrity_active_ = false;
    return result;
  }
  mvb::DisplayProfile display_profile() const override { return profile_; }
  bool configure(const mvb::RenderOptions &options) override {
    if (dma_completion_unknown_ || dma_ownership_.active_leases())
      return false;
#ifndef MVB_JPEG_DMA_DIRECT_OUTPUT
    if (options.zero_copy)
      return false;
#endif
    if (options.zero_copy && (options.mode != mvb::RenderMode::Dma ||
                              !options.big_endian || MVB_DMA_QUEUE_DEPTH != 2))
      return false;
    if (options.mode == mvb::RenderMode::Legacy &&
        (options.spi_hz != 20000000 || options.command_spi_hz != 20000000))
      return false;
    if (options.mode == mvb::RenderMode::Dma &&
        (!dma_device_ || options.spi_hz != options_.spi_hz ||
         options.zero_copy != options_.zero_copy || dma_failed_)) {
      if (!release_dma())
        return false;
      if (!initialize_dma(options.spi_hz, options.zero_copy)) {
        options_.mode = mvb::RenderMode::Blocks;
        return false;
      }
    } else if (options.mode != mvb::RenderMode::Dma && dma_device_) {
      if (!release_dma())
        return false;
    }
    options_ = options;
    direct_submissions_ = copied_submissions_ = 0;
    completed_display_frames_ = verified_display_frames_ =
        video_samples_checked_ = video_samples_mismatched_ =
            video_verification_us_ = 0;
    first_bad_x_ = first_bad_y_ = -1;
    refresh_dividers();
    if (options.mode != mvb::RenderMode::Dma)
      dma_failed_ = false;
    tft_.setSwapBytes(!options_.big_endian);
    return true;
  }
  void begin_frame() override {
    profile_ = {};
    profile_.available = true;
    if (dma_completion_unknown_ || integrity_active_)
      return;
    stream_active_ = false;
    std::memset(expected_valid_, 0, sizeof(expected_valid_));
    uint64_t start = esp_timer_get_time();
    if (options_.mode != mvb::RenderMode::Legacy) {
      tft_.startWrite();
      // SPIClass::setFrequency() locks the parameter mutex already held by
      // startWrite()/beginTransaction(). Set the divider while we own the bus.
      WRITE_PERI_REG(SPI_CLOCK_REG(HSPI), command_divider_);
      frame_active_ = true;
    }
    profile_.submit_us += uint64_t(esp_timer_get_time()) - start;
  }
  void begin_image(mvb::Rect region) override {
    begin_frame();
    if (integrity_active_) {
      integrity_region_ = region;
      integrity_next_x_ = region.x;
      integrity_next_y_ = region.y;
      return;
    }
    if (dma_completion_unknown_ || integrity_active_ ||
        options_.mode == mvb::RenderMode::Legacy || region.w <= 0 ||
        region.h <= 0)
      return;
    uint64_t start = esp_timer_get_time();
    stream_region_ = region;
    stream_next_y_ = region.y;
    set_window(region.x, region.y, region.w, region.h);
    stream_active_ = true;
    profile_.submit_us += uint64_t(esp_timer_get_time()) - start;
  }
  bool end_frame() override {
    if (integrity_active_) {
      integrity_profile_.available =
          integrity_profile_.available && integrity_region_.w > 0 &&
          integrity_region_.h > 0 && integrity_next_x_ == integrity_region_.x &&
          integrity_next_y_ == integrity_region_.y + integrity_region_.h;
      return integrity_profile_.available;
    }
    uint64_t start = esp_timer_get_time();
    bool complete = wait_dma();
    sample_clock();
    if (frame_active_ && !dma_ownership_.pending())
      tft_.endWrite();
    if (!dma_ownership_.pending())
      frame_active_ = false;
    stream_active_ = false;
    if (complete) {
      completed_display_frames_++;
      if (options_.verify_every_frames &&
          completed_display_frames_ % options_.verify_every_frames == 0)
        verify_video_samples();
    }
    profile_.submit_us += uint64_t(esp_timer_get_time()) - start;
    return complete;
  }
  void info(JsonObject r) {
    r["available"] = true;
    r["configured_controller"] = "ILI9341";
    r["mode"] = options_.mode == mvb::RenderMode::Dma      ? "dma"
                : options_.mode == mvb::RenderMode::Legacy ? "legacy"
                                                           : "blocks";
    r["big_endian"] = options_.big_endian;
    r["requested_spi_hz"] = options_.spi_hz;
    r["requested_command_spi_hz"] = options_.command_spi_hz;
    r["observed_register_spi_hz"] = observed_clock_;
    r["observed_command_register_spi_hz"] = observed_command_clock_;
    r["clock_scope"] = "ESP32_SPI_clock_divider_register";
    r["dma_initialized"] = dma_device_ != nullptr;
    r["dma_buffer_bytes"] = allocated_slots_ * BufferPixels * 2;
    r["dma_allocation_bytes"] =
        allocated_slots_ * (BufferPixels * 2 + (options_.zero_copy ? 15 : 0));
    r["dma_buffer_slots"] = allocated_slots_;
    r["zero_copy_available"] = mvb::NewJpeg::direct_output_available();
    r["zero_copy_selected"] = options_.zero_copy;
    r["zero_copy_direct_submissions"] = direct_submissions_;
    r["copied_dma_submissions"] = copied_submissions_;
    r["zero_copy_active_leases"] = dma_ownership_.active_leases();
    r["zero_copy_used"] = direct_submissions_ > 0;
    r["dma_submissions"] = dma_ownership_.submissions();
    r["dma_completions"] = dma_ownership_.completions();
    r["dma_ownership_errors"] = dma_ownership_.errors();
    r["dma_queue_depth"] = dma_ownership_.depth();
    r["dma_pending"] = dma_ownership_.pending();
    const unsigned alignment_mask = options_.zero_copy ? 15 : 3;
    r["dma_alignment_requirement_bytes"] = alignment_mask + 1;
    if (dma_device_) {
      bool aligned = true;
      for (unsigned slot = 0; slot < allocated_slots_; ++slot)
        aligned =
            aligned && dma_buffers_[slot] &&
            !(reinterpret_cast<uintptr_t>(dma_buffers_[slot]) & alignment_mask);
      r["dma_buffers_aligned"] = aligned;
    } else
      r["dma_buffers_aligned"] = nullptr;
    r["video_verified_frames"] = verified_display_frames_;
    r["video_pixel_samples_checked"] = video_samples_checked_;
    r["video_pixel_mismatches"] = video_samples_mismatched_;
    r["video_verification_us_nested"] = video_verification_us_;
    if (first_bad_x_ >= 0) {
      auto failure = r["first_video_pixel_mismatch"].to<JsonObject>();
      failure["x"] = first_bad_x_;
      failure["y"] = first_bad_y_;
      failure["expected"] = first_bad_expected_;
      failure["actual"] = first_bad_actual_;
    }
    r["dma_failed"] = dma_failed_;
    r["dma_completion_unconfirmed_reset_required"] = dma_completion_unknown_;
    r["continuous_transaction"] = options_.mode != mvb::RenderMode::Legacy;
    r["sd_spi_hz"] = storage.clock_hz();
#ifdef MVB_USE_IDF_SDSPI
    r["sd_driver"] = "esp_idf_sdspi_dma";
    r["sd_dma_initialized"] = SD.dmaInitialized();
    r["sd_driver_actual_spi_hz"] = SD.actualClockHz();
    r["sd_driver_error"] = SD.lastError();
    r["sd_cleanup_error"] = SD.cleanupError();
    r["sd_fat_error"] = SD.lastFatError();
    r["sd_error_stage"] = SD.errorStage();
#else
    r["sd_driver"] = "arduino_sd_fifo";
    r["sd_dma_initialized"] = false;
#endif
    r["sd_stdio_read_buffer_bytes"] = storage.read_buffer_bytes();
#ifdef MVB_SD_POSIX_READ
    r["sd_read_api"] = "posix_read_lseek";
#else
    r["sd_read_api"] = "stdio_fread_fseek";
#endif
    uint32_t sd_reg = READ_PERI_REG(SPI_CLOCK_REG(VSPI));
    r["sd_observed_register_spi_hz"] =
        (sd_reg & (1u << 31))
            ? getApbFrequency()
            : getApbFrequency() / (((sd_reg >> 18) & 0x1fff) + 1) /
                  (((sd_reg >> 12) & 0x3f) + 1);
#ifdef __OPTIMIZE_SIZE__
    r["compiler_optimization"] = "size";
#else
    r["compiler_optimization"] = "speed";
#endif
    r["tft_pins"] = "SCK14 MOSI13 MISO12 CS15 DC2 BL21 HSPI";
    r["sd_pins"] = "SCK18 MOSI23 MISO19 CS5 VSPI";
    if (dma_completion_unknown_ || !wait_dma()) {
      r["dma_failed"] = dma_failed_;
      r["dma_completion_unconfirmed_reset_required"] = dma_completion_unknown_;
      r["controller_id_read"] = nullptr;
      r["physical_controller_verified"] = nullptr;
      r["controller_id_status"] = "dma_completion_unconfirmed_reset_required";
      return;
    }
    uint16_t id =
        uint16_t(tft_.readcommand8(0xd3, 2)) << 8 | tft_.readcommand8(0xd3, 3);
    r["controller_id_read"] = id;
    r["physical_controller_verified"] = id == 0x9341;
    r["controller_id_status"] = id == 0 || id == 0xffff ? "inconclusive"
                                : id == 0x9341          ? "ILI9341_readback"
                                                        : "unrecognized";
  }
  bool probe(JsonObject r, unsigned repetitions) {
    uint16_t stripe[320 * 6];
    uint64_t started = esp_timer_get_time(), submitted = 0, copies = 0,
             waits = 0, windows = 0, pushes = 0;
    for (unsigned pass = 0; pass < repetitions; pass++) {
      begin_image({0, 0, 320, 240});
      for (int y = 0; y < 240; y += 6) {
        for (int line = 0; line < 6; line++)
          for (int x = 0; x < 320; x++) {
            uint16_t color = x < 106 ? 0xf800 : x < 213 ? 0x07e0 : 0x001f;
            stripe[line * 320 + x] =
                options_.big_endian ? uint16_t(color << 8 | color >> 8) : color;
          }
        if (!block(0, y, stripe, 320, 6, 320)) {
          end_frame();
          return false;
        }
      }
      if (!end_frame())
        return false;
      submitted += profile_.submit_us;
      copies += profile_.copy_us;
      waits += profile_.wait_us;
      windows += profile_.windows;
      pushes += profile_.pushes;
    }
    r["full_screen_writes"] = repetitions;
    r["wall_us"] = uint64_t(esp_timer_get_time()) - started;
    r["display_us"] = submitted;
    r["copy_us_nested"] = copies;
    r["wait_us_nested"] = waits;
    r["address_windows"] = windows;
    r["pixel_pushes"] = pushes;
    r["scope"] = "RGB565_stripe_TFT_probe_not_video_FPS";
    // Read the real controller GRAM after DMA completion. Primary colours
    // check byte order and whether fast writes reached the intended pixels.
    // This verification is separate from the measured transfer wall time.
    uint64_t readback_started = esp_timer_get_time();
    bool verified = true;
    auto samples = r["gram_readback"].to<JsonArray>();
    for (int y : {2, 122, 238}) {
      for (int x : {30, 160, 290}) {
        uint16_t expected = x < 106 ? 0xf800 : x < 213 ? 0x07e0 : 0x001f;
        uint16_t actual = tft_.readPixel(x, y);
        auto sample = samples.add<JsonObject>();
        sample["x"] = x;
        sample["y"] = y;
        sample["expected_rgb565"] = expected;
        sample["read_rgb565"] = actual;
        verified = verified && actual == expected;
      }
    }
    r["gram_readback_verified"] = verified;
    r["gram_readback_us"] = uint64_t(esp_timer_get_time()) - readback_started;
    return true;
  }
  bool row(int x, int y, const uint16_t *pixels, int n) override {
    if (x < 0 || y < 0 || y >= 240 || n < 0 || x + n > 320)
      return false;
    return n == 0 || block(x, y, pixels, n, 1, n);
  }
  bool block(int x, int y, const uint16_t *pixels, int width, int height,
             int stride) override {
    if (dma_completion_unknown_ ||
        (options_.mode == mvb::RenderMode::Dma && dma_ownership_.faulted()))
      return false;
    if (x < 0 || y < 0 || width < 1 || height < 1 || x + width > 320 ||
        y + height > 240 || stride < width)
      return false;
    if (integrity_active_) {
      // Ordered MCU bands prove complete coverage without a framebuffer or
      // per-pixel coverage map. Repeated/skipped rectangles fail the probe.
      if (x != integrity_next_x_ || y != integrity_next_y_ ||
          x + width > integrity_region_.x + integrity_region_.w ||
          y + height > integrity_region_.y + integrity_region_.h ||
          (integrity_band_height_ && height != integrity_band_height_)) {
        integrity_profile_.available = false;
        return false;
      }
      integrity_band_height_ = height;
      // Re-decode the paused frame without writes and compare actual GRAM.
      // Reuse a completed DMA buffer, or the existing row-tail on polling
      // paths.
      uint16_t *readback = dma_buffers_[0] ? dma_buffers_[0] : tail_;
      size_t capacity = dma_buffers_[0] ? BufferPixels : 322;
      int rows = int(capacity / size_t(width));
      if (!rows || dma_ownership_.pending() || dma_ownership_.active_leases()) {
        integrity_profile_.available = false;
        return false;
      }
      for (int offset = 0; offset < height; offset += rows) {
        int count = std::min(rows, height - offset);
        uint64_t read_at = esp_timer_get_time();
        tft_.readRect(x, y + offset, width, count, readback);
        integrity_profile_.read_us += uint64_t(esp_timer_get_time()) - read_at;
        integrity_crc_state_ = mvb::crc_update(
            integrity_crc_state_, reinterpret_cast<const uint8_t *>(readback),
            size_t(width) * count * sizeof(uint16_t));
        for (int line = 0; line < count; line++)
          for (int column = 0; column < width; column++) {
            uint16_t actual = readback[line * width + column];
            if (!options_.big_endian)
              actual = uint16_t(actual << 8 | actual >> 8);
            integrity_profile_.pixels_checked++;
            if (actual != pixels[(offset + line) * stride + column])
              integrity_profile_.pixels_mismatched++;
          }
      }
      integrity_next_x_ += width;
      if (integrity_next_x_ == integrity_region_.x + integrity_region_.w) {
        integrity_next_x_ = integrity_region_.x;
        integrity_next_y_ += integrity_band_height_;
        integrity_band_height_ = 0;
      }
      return true;
    }
    uint64_t start = esp_timer_get_time();
    profile_.pixels += uint64_t(width) * height;
    remember_samples(x, y, pixels, width, height, stride);
    if (options_.mode == mvb::RenderMode::Legacy) {
      profile_.windows++;
      if (stride == width)
        tft_.pushImage(x, y, width, height, pixels);
      else {
        profile_.windows += height - 1;
        for (int line = 0; line < height; line++)
          tft_.pushImage(x, y + line, width, 1, pixels + line * stride);
      }
      profile_.pushes +=
          height; // Pinned const/PROGMEM overload copies each row.
    } else if (options_.mode == mvb::RenderMode::Dma) {
      if (size_t(width) * height > BufferPixels || !dma_device_)
        return false;
      unsigned buffer_index = next_buffer_++ % allocated_slots_;
      // With two queued descriptors, the oldest slot stays owned until its
      // exact completion arrives; the other slot can keep transferring.
      while (!dma_ownership_.writable(buffer_index)) {
        if (dma_ownership_.leased(buffer_index)) {
          // A future warmed strip may be held across end_frame. The copied
          // fallback must select another slot rather than overwrite it.
          bool found = false;
          for (unsigned slot = 0; slot < allocated_slots_; ++slot)
            if (!dma_ownership_.leased(slot)) {
              buffer_index = slot;
              found = true;
              break;
            }
          if (!found)
            return false;
        } else if (!wait_one_dma())
          return false;
      }
      uint16_t *buffer = dma_buffers_[buffer_index];
      uint64_t copy_at = esp_timer_get_time();
      for (int line = 0; line < height; line++) {
        if (options_.big_endian)
          std::memcpy(buffer + line * width, pixels + line * stride, width * 2);
        else
          for (int column = 0; column < width; column++) {
            uint16_t pixel = pixels[line * stride + column];
            buffer[line * width + column] = uint16_t(pixel << 8 | pixel >> 8);
          }
      }
      profile_.copy_us += uint64_t(esp_timer_get_time()) - copy_at;
      while (dma_ownership_.pending() >= dma_ownership_.depth())
        if (!wait_one_dma())
          return false;
      block_window(x, y, width, height);
      if (dma_failed_ || !dma_ownership_.can_submit(buffer_index))
        return false;
      auto &transaction = dma_transactions_[buffer_index];
      transaction = {};
      transaction.length = width * height * 16;
      transaction.tx_buffer = buffer;
      if (spi_device_queue_trans(dma_device_, &transaction, portMAX_DELAY) !=
          ESP_OK) {
        dma_failed_ = true;
        return false;
      }
      if (!dma_ownership_.submit(buffer_index)) {
        dma_failed_ = true;
        dma_completion_unknown_ = true;
        return false;
      }
      copied_submissions_++;
      profile_.pushes++;
    } else {
      // pushImage(const uint16_t*) selects the library's PROGMEM row-copy
      // overload even for decoder RAM. pushPixels accepts const RAM directly.
      block_window(x, y, width, height);
      if (stride == width) {
        push_span(pixels, uint32_t(width) * height);
      } else
        for (int line = 0; line < height; line++) {
          push_span(pixels + line * stride, width);
        }
    }
    profile_.submit_us += uint64_t(esp_timer_get_time()) - start;
    return true;
  }
  void clear(mvb::Rect r) override {
    if (dma_completion_unknown_ || integrity_active_)
      return;
    if (r.w > 0 && r.h > 0)
      tft_.fillRect(r.x, r.y, r.w, r.h, TFT_BLACK);
  }
  void waiting(bool card, bool connected, const char *error) override {
    if (dma_completion_unknown_ || integrity_active_)
      return;
    using mvb::messages::Key;
    tft_.fillScreen(mvb::theme::surface);
    tft_.setTextColor(mvb::theme::on_surface, mvb::theme::surface);
    tft_.setTextFont(2);
    tft_.setCursor(mvb::theme::margin, mvb::theme::title_y);
    tft_.print(mvb::messages::english(Key::Title));
    tft_.setCursor(mvb::theme::margin, mvb::theme::message_y);
    tft_.print(mvb::messages::english(Key::Waiting));
    tft_.setTextFont(1);
    tft_.setCursor(mvb::theme::margin, mvb::theme::instructions_y);
    tft_.setTextWrap(true);
    tft_.print(mvb::messages::english(Key::Instructions));
    tft_.setCursor(mvb::theme::margin, mvb::theme::sd_y);
    tft_.print(mvb::messages::english(card ? Key::SdReady : Key::SdMissing));
    tft_.setCursor(mvb::theme::margin, mvb::theme::connection_y);
    tft_.print(
        mvb::messages::english(connected ? Key::Connected : Key::Disconnected));
    if (error) {
      tft_.setCursor(mvb::theme::margin, mvb::theme::error_y);
      tft_.print(mvb::messages::english(Key::Error));
      tft_.print(": ");
      tft_.print(error);
    }
  }
} painter;
class EspRuntime : public mvb::Runtime {
  mbedtls_sha256_context hash_;
  uint64_t space_at_ = 0, sd_total_ = 0, sd_free_ = 0;
#if CONFIG_FREERTOS_USE_TRACE_FACILITY &&                                      \
    CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS
  TaskStatus_t tasks_[32]{};
  configRUN_TIME_COUNTER_TYPE last_idle_[portNUM_PROCESSORS]{}, last_total_ = 0;
  bool cpu_sampled_ = false;
  uint64_t cpu_at_ = 0;
  double cpu_percent_ = 0;
  bool cpu_available_ = false;
#endif
  void cpu_telemetry(JsonObject r) {
    r["cpu_utilization_percent"] = nullptr;
#if CONFIG_FREERTOS_USE_TRACE_FACILITY &&                                      \
    CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS
    if (cpu_at_ && now() - cpu_at_ < 500000) {
      if (cpu_available_)
        r["cpu_utilization_percent"] = cpu_percent_;
      r["cpu_utilization_status"] = cpu_available_
                                        ? "measured_idle_runtime_delta"
                                        : "awaiting_measurement_window";
      return;
    }
    cpu_at_ = now();
    cpu_available_ = false;
    configRUN_TIME_COUNTER_TYPE total = 0, idle[portNUM_PROCESSORS]{};
    auto n = uxTaskGetSystemState(tasks_, 32, &total);
    if (!n) {
      cpu_sampled_ = false;
      r["cpu_utilization_status"] = "task_snapshot_capacity_exceeded";
      return;
    }
    unsigned found = 0;
    for (unsigned core = 0; core < portNUM_PROCESSORS; core++) {
      auto handle = xTaskGetIdleTaskHandleForCore(core);
      for (unsigned i = 0; i < n; i++)
        if (tasks_[i].xHandle == handle) {
          idle[core] = tasks_[i].ulRunTimeCounter;
          found++;
          break;
        }
    }
    if (found != portNUM_PROCESSORS) {
      cpu_sampled_ = false;
      r["cpu_utilization_status"] = "idle_task_unavailable";
      return;
    }
    auto elapsed = configRUN_TIME_COUNTER_TYPE(total - last_total_);
    double idle_delta = 0;
    for (unsigned core = 0; core < portNUM_PROCESSORS; core++)
      idle_delta += configRUN_TIME_COUNTER_TYPE(idle[core] - last_idle_[core]);
    if (cpu_sampled_ && elapsed) {
      double utilization =
          100.0 * (1.0 - idle_delta / (double(elapsed) * portNUM_PROCESSORS));
      if (utilization >= 0 && utilization <= 100) {
        r["cpu_utilization_percent"] = utilization;
        cpu_percent_ = utilization;
        cpu_available_ = true;
        r["cpu_utilization_status"] = "measured_idle_runtime_delta";
      } else
        r["cpu_utilization_status"] = "inconsistent_runtime_window";
    } else
      r["cpu_utilization_status"] = "awaiting_measurement_window";
    last_total_ = total;
    for (unsigned core = 0; core < portNUM_PROCESSORS; core++)
      last_idle_[core] = idle[core];
    cpu_sampled_ = true;
    r["cpu_utilization_scope"] = "mean_of_both_cores";
    r["cpu_measurement_window_us"] = elapsed;
#else
    r["cpu_utilization_status"] = "runtime_stats_not_enabled";
#endif
  }

public:
  EspRuntime() { mbedtls_sha256_init(&hash_); }
  uint64_t now() override { return uint64_t(esp_timer_get_time()); }
  bool configure_loop_options(uint32_t interval) override {
#ifdef MVB_ADAPTIVE_LOOP_YIELD
    return adaptive_loop.configure(interval);
#else
    (void)interval;
    return false;
#endif
  }
  void loop_diagnostics(JsonObject r) override {
#ifdef MVB_ADAPTIVE_LOOP_YIELD
    adaptive_loop.status(r);
#else
    r["available"] = false;
#endif
  }
  uint32_t configured_idle_yield_us() const override {
#ifdef MVB_ADAPTIVE_LOOP_YIELD
    return adaptive_loop.idle_yield_us;
#else
    return 0;
#endif
  }
  uint32_t session_nonce() override { return esp_random(); }
  bool hash_begin() override { return mbedtls_sha256_starts(&hash_, 0) == 0; }
  bool hash_update(const uint8_t *b, size_t n) override {
    return mbedtls_sha256_update(&hash_, b, n) == 0;
  }
  bool hash_finish(char out[65]) override {
    uint8_t b[32];
    if (mbedtls_sha256_finish(&hash_, b))
      return false;
    for (int i = 0; i < 32; i++)
      std::snprintf(out + 2 * i, 3, "%02x", b[i]);
    return true;
  }
  void serial_diagnostics(JsonObject r) override {
    r["available"] = true;
    r["baud"] = MVB_UART_BAUD;
    r["rx_buffer_bytes"] = 4096;
    r["uart_overruns"] = uart_overruns.load();
    r["uart_errors"] = uart_errors.load();
    r["invalid_wire_frames"] = wire_errors.load();
    r["inbound_queue_drops"] = inbound_drops.load();
    r["reply_queue_drops"] = reply_drops.load();
    r["event_queue_drops"] = event_drops.load();
    r["last_sd_errno"] = sd_errno.load();
  }
  void display_info(JsonObject r) override { painter.info(r); }
  uint32_t configured_baud() const override { return MVB_UART_BAUD; }
  void recovery_info(JsonObject r) override { storage.recovery_info(r); }
  bool display_probe(JsonObject r, unsigned repetitions) override {
    return painter.probe(r, repetitions);
  }
  void telemetry(JsonObject r) override {
    constexpr uint32_t caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
    r["simulator"] = false;
    r["free_heap_bytes"] = heap_caps_get_free_size(caps);
    r["minimum_free_heap_bytes"] = heap_caps_get_minimum_free_size(caps);
    r["largest_free_block_bytes"] = heap_caps_get_largest_free_block(caps);
    r["allocator_total_bytes"] = heap_caps_get_total_size(caps);
    r["soc_sram_bytes"] = 532480;
    r["psram_bytes"] = ESP.getPsramSize();
    r["free_psram_bytes"] = ESP.getFreePsram();
    r["cpu_mhz"] = getCpuFrequencyMhz();
    r["flash_bytes"] = ESP.getFlashChipSize();
    cpu_telemetry(r);
    r["temperature_c"] = nullptr;
    r["temperature_status"] = "unsupported_calibrated_classic_esp32_sensor";
    r["wifi_rssi_dbm"] = nullptr;
    wifi_mode_t mode = WIFI_MODE_NULL;
    auto wifi_error = esp_wifi_get_mode(&mode);
    if (wifi_error == ESP_ERR_WIFI_NOT_INIT ||
        (wifi_error == ESP_OK && mode == WIFI_MODE_NULL))
      r["wifi_status"] = "disabled";
    else if (wifi_error != ESP_OK) {
      r["wifi_status"] = nullptr;
      r["wifi_error"] = wifi_error;
    } else {
      wifi_ap_record_t ap{};
      if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        r["wifi_status"] = "connected";
        r["wifi_rssi_dbm"] = ap.rssi;
      } else
        r["wifi_status"] =
            mode == WIFI_MODE_AP ? "access_point" : "disconnected";
    }

    r["uptime_us"] = now();
    if (storage.mounted()) {
      if (!space_at_ || now() - space_at_ > 10000000) {
        sd_total_ = storage.capacity();
        sd_free_ = storage.free_bytes();
        space_at_ = now();
      }
      r["sd_card_bytes"] = SD.cardSize();
      r["sd_total_bytes"] = sd_total_;
      r["sd_free_bytes"] = sd_free_;
    } else {
      r["sd_card_bytes"] = nullptr;
      r["sd_total_bytes"] = nullptr;
      r["sd_free_bytes"] = nullptr;
    }
  }
} runtime;

QueueHandle_t inbound, outbound, events;
class SerialOutput : public mvb::Output {
public:
  bool send(const mvb::Frame &frame) override {
    bool sent = xQueueSend(frame.kind == 3 ? events : outbound, &frame,
                           pdMS_TO_TICKS(frame.kind == 3 ? 0 : 100)) == pdTRUE;
    if (!sent)
      (frame.kind == 3 ? event_drops : reply_drops).fetch_add(1);
    return sent;
  }
} output;
mvb::Engine *engine = nullptr;
void serial_task(void *) {
  static mvb::WireParser parser;
  static mvb::Frame frame;
  static uint8_t wire[mvb::MaxWire];
  size_t pending = 0, sent = 0;
  for (;;) {
    int budget = 4096;
    while (Serial.available() && budget-- > 0) {
      uint8_t b = uint8_t(Serial.read());
      if (parser.feed(b, runtime.now(), frame)) {
        if (xQueueSend(inbound, &frame, pdMS_TO_TICKS(10)) != pdTRUE) {
          inbound_drops.fetch_add(1);
        }
      }
    }
    wire_errors.store(parser.invalid_frames);
    if (sent == pending) {
      pending = sent = 0;
      if (xQueueReceive(outbound, &frame, 0) == pdTRUE ||
          xQueueReceive(events, &frame, 0) == pdTRUE)
        pending = mvb::encode(frame, wire);
    }
    // A full telemetry frame can occupy UART TX for ~180 ms at 115200.
    // Never stop draining RX for that duration: transmit only available space,
    // retain partial writes, and keep each COBS frame contiguous.
    int room = Serial.availableForWrite();
    if (pending > sent && room > 0) {
      size_t count = std::min(pending - sent, size_t(room));
      sent += Serial.write(wire + sent, count);
    }
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}
} // namespace
void setup() {
  // Only framed errors are sent once the application owns the serial channel.
  esp_log_level_set("*", ESP_LOG_NONE);
  if (Serial.setRxBufferSize(4096) != 4096) {
    painter.begin();
    painter.waiting(false, false, "insufficient_memory");
    return;
  }
  Serial.begin(MVB_UART_BAUD);
  Serial.onReceiveError([](hardwareSerial_error_t error) {
    if (error == UART_BUFFER_FULL_ERROR || error == UART_FIFO_OVF_ERROR)
      uart_overruns.fetch_add(1);
    else if (error != UART_NO_ERROR)
      uart_errors.fetch_add(1);
  });
  painter.begin();
  if (!mvb::crc_contract_self_test()) {
    painter.waiting(false, false, "crc_self_test_failed");
    return;
  }
  inbound = xQueueCreate(4, sizeof(mvb::Frame));
  outbound = xQueueCreate(4, sizeof(mvb::Frame));
  events = xQueueCreate(2, sizeof(mvb::Frame));
  if (!inbound || !outbound || !events) {
    painter.waiting(false, false, "insufficient_memory");
    return;
  }
  engine = new (std::nothrow) mvb::Engine(storage, painter, runtime, output);
  if (!engine) {
    painter.waiting(false, false, "insufficient_memory");
    return;
  }
  engine->begin();
  if (xTaskCreatePinnedToCore(serial_task, "mvb_serial", 8192, nullptr, 1,
                              nullptr, 0) != pdPASS) {
    delete engine;
    engine = nullptr;
    painter.waiting(storage.mounted(), false, "insufficient_memory");
  }
}
void loop() {
  if (!engine) {
    delay(100);
    return;
  }
  static mvb::Frame frame;
  int count = 4;
  while (count-- && xQueueReceive(inbound, &frame, 0) == pdTRUE)
    engine->command(frame);
#ifdef MVB_ADAPTIVE_LOOP_YIELD
  uint64_t work_started = esp_timer_get_time();
  engine->tick();
  uint64_t finished = esp_timer_get_time();
  // Quiet ticks sleep normally. Busy playback still gives the idle task a
  // timed slice at the selected interval, rather than adding a tick per frame.
  if (adaptive_loop.should_sleep(work_started, finished)) {
    const uint64_t sleep_started = esp_timer_get_time();
    delay(1);
    adaptive_loop.completed_sleep(sleep_started, esp_timer_get_time(),
                                   finished - work_started < 1000);
  } else {
    ++adaptive_loop.yields;
    taskYIELD();
  }
#else
  engine->tick();
  delay(1);
#endif
}
