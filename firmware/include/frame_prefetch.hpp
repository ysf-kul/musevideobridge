#pragma once
#include "video.hpp"
#include <atomic>
#include <new>

#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>
#endif

namespace mvb {
// One reusable compressed slot and one outstanding, borrowed-buffer read.
// join() is mandatory before the caller touches Video/File/storage or recycles
// either slot. A slow/failed read is never cancelled by deleting its task.
class FramePrefetch {
public:
  struct Result {
    bool ok = false;
    uint32_t index = 0;
    uint8_t *record = nullptr;
    size_t capacity = 0, length = 0;
    uint64_t wall_us = 0;
    FrameProfile profile;
    const char *error = nullptr;
    uint32_t stack_free_bytes = 0;
  };
  static constexpr uint32_t StackBytes = 4096;
  static constexpr size_t MaximumSlotBytes =
      MaxJpeg + Video::RecordHeaderBytes + 3;
  static constexpr bool available() {
#if defined(MVB_FRAME_PREFETCH) &&                                         \
    (defined(ARDUINO_ARCH_ESP32) || !defined(ARDUINO))
    return true;
#else
    return false;
#endif
  }

private:
  enum class State { Idle, Requested, Running, Complete, Stop, Stopped };
  std::atomic<State> state_{State::Idle};
  uint8_t *buffer_ = nullptr;
  size_t capacity_ = 0;
  Video *video_ = nullptr;
  bool coalesced_ = true;
  Result result_;
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
  TaskHandle_t task_ = nullptr;
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
  std::thread thread_;
  std::mutex wake_mutex_;
  std::condition_variable wake_;
#endif
  class WorkerClock : public ProfileClock {
  public:
    uint64_t now() override {
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
      return uint64_t(esp_timer_get_time());
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
      return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
                          std::chrono::steady_clock::now().time_since_epoch())
                          .count());
#else
      return 0;
#endif
    }
  };
  void wait_once() {
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
    vTaskDelay(1);
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
    std::this_thread::yield();
#endif
  }
  void notify() {
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
    xTaskNotifyGive(task_);
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
    std::lock_guard<std::mutex> lock(wake_mutex_);
    wake_.notify_one();
#endif
  }
  void run() {
    WorkerClock clock;
    for (;;) {
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
      ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
      std::unique_lock<std::mutex> lock(wake_mutex_);
      wake_.wait(lock, [&] {
        State state = state_.load(std::memory_order_acquire);
        return state == State::Requested || state == State::Stop;
      });
      lock.unlock();
#else
      return;
#endif
      if (state_.load(std::memory_order_acquire) == State::Stop) {
        // No references to the owner are accessed after this publication.
        state_.store(State::Stopped, std::memory_order_release);
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
        vTaskDelete(nullptr);
#endif
        return;
      }
      state_.store(State::Running, std::memory_order_release);
      uint64_t start = clock.now();
      if (coalesced_)
        result_.ok = video_->frame_record(
            result_.index, result_.record, result_.capacity, result_.length,
            &clock, &result_.profile);
      else
        result_.ok = video_->frame(result_.index, result_.record, result_.length,
                                   &clock, &result_.profile);
      result_.wall_us = clock.now() - start;
      result_.error = result_.ok ? nullptr : video_->error;
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
      // This SDK reports stack high-water marks in bytes, unlike upstream
      // FreeRTOS's word convention. The task's requested stack is also bytes.
      result_.stack_free_bytes = uxTaskGetStackHighWaterMark(nullptr);
#endif
      state_.store(State::Complete, std::memory_order_release);
    }
  }
  bool worker_exists() const {
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
    return task_ != nullptr;
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
    return thread_.joinable();
#else
    return false;
#endif
  }

public:
  FramePrefetch() = default;
  FramePrefetch(const FramePrefetch &) = delete;
  FramePrefetch &operator=(const FramePrefetch &) = delete;
  ~FramePrefetch() { release(); }
  uint8_t *buffer() const { return buffer_; }
  size_t capacity() const { return capacity_; }
  bool enabled() const { return buffer_ && worker_exists(); }
  bool configure(size_t bytes) {
    if (!available() || bytes < Video::RecordHeaderBytes + 4 ||
        bytes > MaximumSlotBytes)
      return false;
    if (enabled() && capacity_ >= bytes)
      return true;
    release();
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
    // Preserve an internal reserve for serial, SD, decoders and diagnostics.
    // The second slot is sized from verified compressed-frame metadata.
    if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) <
        32768 + bytes + StackBytes + 1024)
      return false;
    buffer_ = static_cast<uint8_t *>(
        heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
    buffer_ = new (std::nothrow) uint8_t[bytes];
#endif
    if (!buffer_)
      return false;
    capacity_ = bytes;
    state_.store(State::Idle, std::memory_order_release);
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
    if (xTaskCreatePinnedToCore(
            [](void *context) { static_cast<FramePrefetch *>(context)->run(); },
            "mvb-prefetch", StackBytes, this, 0, &task_, 0) != pdPASS) {
      task_ = nullptr;
      release();
      return false;
    }
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
    try {
      thread_ = std::thread([this] { run(); });
    } catch (...) {
      release();
      return false;
    }
#endif
    return enabled();
  }
  bool start(Video &video, uint32_t index, uint8_t *record, size_t capacity,
             bool coalesced) {
    if (!enabled() || state_.load(std::memory_order_acquire) != State::Idle ||
        !video.ready || !record || index >= video.header.frames ||
        capacity < video.header.max_jpeg +
                       (coalesced ? Video::RecordHeaderBytes + 3 : 0))
      return false;
    video_ = &video;
    coalesced_ = coalesced;
    result_ = {};
    result_.index = index;
    result_.record = record;
    result_.capacity = capacity;
    state_.store(State::Requested, std::memory_order_release);
    notify();
    return true;
  }
  bool join(Result &result) {
    State state = state_.load(std::memory_order_acquire);
    if (state == State::Idle || !worker_exists())
      return false;
    while (state != State::Complete) {
      wait_once();
      state = state_.load(std::memory_order_acquire);
    }
    result = result_;
    state_.store(State::Idle, std::memory_order_release);
    return true;
  }
  void release() {
    if (worker_exists()) {
      Result ignored;
      join(ignored);
      state_.store(State::Stop, std::memory_order_release);
      notify();
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
      while (state_.load(std::memory_order_acquire) != State::Stopped)
        wait_once();
      task_ = nullptr;
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
      thread_.join();
#endif
    }
#if defined(MVB_FRAME_PREFETCH) && defined(ARDUINO_ARCH_ESP32)
    if (buffer_)
      heap_caps_free(buffer_);
#elif defined(MVB_FRAME_PREFETCH) && !defined(ARDUINO)
    delete[] buffer_;
#endif
    buffer_ = nullptr;
    capacity_ = 0;
    state_.store(State::Idle, std::memory_order_release);
  }
};
} // namespace mvb
