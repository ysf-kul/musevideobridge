#pragma once
constexpr int MC_DATUM = 4, TL_DATUM = 0;
// Delayed host peripheral for the actual TftPainter class. No SPI/FPS claims.
#include "dma_ownership.hpp"
#include "messages.hpp"
#include "theme.hpp"
#include "wire.hpp"
#include <ArduinoJson.h>
#include <algorithm>
#include <array>
#include <cassert>
#include <cstring>
#include <deque>
#include <map>
#include <vector>

constexpr int HSPI = 0, VSPI = 1, HSPI_HOST = 0, SPI_DMA_CH_AUTO = 1;
constexpr int MALLOC_CAP_INTERNAL = 1, MALLOC_CAP_8BIT = 2, MALLOC_CAP_DMA = 4;
constexpr int ESP_OK = 0, SPI_DEVICE_NO_DUMMY = 1, portMAX_DELAY = 1000;
constexpr int OUTPUT = 1, HIGH = 1, TFT_BLACK = 0;
using esp_err_t = int;
#ifndef MAX_BUFFERED_PIXELS
#define MAX_BUFFERED_PIXELS 5120
#endif
#define SPI_CLOCK_REG(host) (host)
uint32_t clock_registers[2]{};
#define READ_PERI_REG(reg) (clock_registers[reg])
#define WRITE_PERI_REG(reg, value) (clock_registers[reg] = (value))
#define pdMS_TO_TICKS(ms) (ms)
uint32_t getApbFrequency() { return 80000000; }
uint32_t spiFrequencyToClockDiv(int, uint32_t hz) {
  return hz == 80000000 ? uint32_t(1) << 31 : (80000000 / hz - 1) << 12;
}
int64_t esp_timer_get_time() { static int64_t now = 0; return ++now; }
void pinMode(int, int) {}
void digitalWrite(int, int) {}

struct spi_transaction_t { unsigned length = 0; const void *tx_buffer = nullptr; };
using spi_device_handle_t = void *;
struct spi_bus_config_t {
  int mosi_io_num, miso_io_num, sclk_io_num, quadwp_io_num, quadhd_io_num,
      max_transfer_sz;
};
struct spi_device_interface_config_t {
  int clock_speed_hz, mode, spics_io_num, queue_size, flags;
};
struct FakeWindow {
  int x = 0, y = 0, width = 0, height = 0;
  size_t cursor = 0;
} window;
std::vector<uint16_t> gram(320 * 240);
std::vector<unsigned> pixel_writes(320 * 240);
std::vector<unsigned> pixel_rectangle_clears(320 * 240);
struct PendingTransfer {
  spi_transaction_t *descriptor;
  const uint16_t *pixels;
  unsigned length;
  FakeWindow address;
  std::vector<uint16_t> expected;
};
std::deque<PendingTransfer> pending_transfers;
std::map<void *, size_t> dma_allocations;
size_t free_heap = 200000;
int fail_allocation_at = -1, dma_allocation_calls = 0;
bool fail_queue = false, fail_completion = false;
bool transaction_open = false, fake_bus_owned = false;
uint64_t submitted_transfers = 0, completed_transfers = 0;
void write_pixels(FakeWindow address, const uint16_t *pixels, size_t count,
                  bool bytes_swapped) {
  assert(address.width > 0 && address.height > 0);
  assert(address.cursor + count <= size_t(address.width) * address.height);
  for (size_t i = 0; i < count; ++i) {
    const size_t offset = address.cursor + i;
    const size_t pos = (address.y + offset / address.width) * 320 +
                       address.x + offset % address.width;
    uint16_t value = pixels[i];
    gram[pos] = bytes_swapped ? uint16_t(value << 8 | value >> 8) : value;
    ++pixel_writes[pos];
  }
}
size_t heap_caps_get_free_size(int) { return free_heap; }
void *heap_caps_malloc(size_t bytes, int capabilities) {
  assert(capabilities == (MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
  if (dma_allocation_calls++ == fail_allocation_at) return nullptr;
  void *allocation = std::malloc(bytes);
  assert(allocation);
  dma_allocations[allocation] = bytes;
  return allocation;
}
void heap_caps_free(void *allocation) {
  assert(pending_transfers.empty());
  assert(dma_allocations.erase(allocation) == 1);
  std::free(allocation);
}
esp_err_t spi_bus_initialize(int, spi_bus_config_t *bus, int) {
  assert(!fake_bus_owned && bus->max_transfer_sz == MAX_BUFFERED_PIXELS * 2);
  fake_bus_owned = true;
  return ESP_OK;
}
esp_err_t spi_bus_free(int) {
  assert(fake_bus_owned && pending_transfers.empty());
  fake_bus_owned = false;
  return ESP_OK;
}
esp_err_t spi_bus_add_device(int, spi_device_interface_config_t *config,
                              spi_device_handle_t *device) {
  assert(fake_bus_owned && config->queue_size == MVB_DMA_QUEUE_DEPTH);
  *device = reinterpret_cast<void *>(uintptr_t(1));
  return ESP_OK;
}
esp_err_t spi_bus_remove_device(spi_device_handle_t device) {
  assert(device && pending_transfers.empty());
  return ESP_OK;
}
esp_err_t spi_device_queue_trans(spi_device_handle_t device,
                                 spi_transaction_t *descriptor, int) {
  assert(device && descriptor && descriptor->tx_buffer && transaction_open);
  if (fail_queue) return 1;
  assert(pending_transfers.size() < MVB_DMA_QUEUE_DEPTH);
  const auto *pixels = static_cast<const uint16_t *>(descriptor->tx_buffer);
  const size_t count = descriptor->length / 16;
  assert(count && count <= MAX_BUFFERED_PIXELS);
  PendingTransfer transfer{descriptor, pixels, descriptor->length, window,
                           std::vector<uint16_t>(pixels, pixels + count)};
  pending_transfers.push_back(transfer);
  window.cursor += count;
  ++submitted_transfers;
  return ESP_OK;
}
esp_err_t spi_device_get_trans_result(spi_device_handle_t device,
                                       spi_transaction_t **done, int) {
  assert(device && !pending_transfers.empty());
  if (fail_completion) return 1;
  const auto &transfer = pending_transfers.front();
  // Both the borrowed pixels and the persistent descriptor must remain intact.
  assert(transfer.descriptor->length == transfer.length);
  assert(transfer.descriptor->tx_buffer == transfer.pixels);
  assert(std::equal(transfer.expected.begin(), transfer.expected.end(),
                    transfer.pixels));
  write_pixels(transfer.address, transfer.pixels, transfer.expected.size(), true);
  *done = transfer.descriptor;
  pending_transfers.pop_front();
  ++completed_transfers;
  return ESP_OK;
}
struct FakeSpi {
  int bus() { return 0; }
  void end() {}
  void begin(int, int, int, int) {}
} fake_spi;
class TFT_eSPI {
  bool swap_ = false;
public:
  static FakeSpi &getSPIinstance() { return fake_spi; }
  void init() {}
  void setRotation(int) {}
  void setSwapBytes(bool swap) { swap_ = swap; }
  void fillScreen(uint16_t value) { std::fill(gram.begin(), gram.end(), value); }
  void fillRect(int x, int y, int w, int h, uint16_t value) {
    assert(pending_transfers.empty());
    for (int row = 0; row < h; ++row) {
      std::fill_n(gram.begin() + (y + row) * 320 + x, w, value);
      for (int column = 0; column < w; ++column)
        ++pixel_rectangle_clears[(y + row) * 320 + x + column];
    }
  }
  void startWrite() { assert(!transaction_open); transaction_open = true; }
  void endWrite() { assert(transaction_open && pending_transfers.empty()); transaction_open = false; }
  void setAddrWindow(int x, int y, int w, int h) {
    assert(pending_transfers.empty()); window = {x, y, w, h, 0};
  }
  void pushPixels(const uint16_t *pixels, size_t count) {
    write_pixels(window, pixels, count, !swap_); window.cursor += count;
  }
  void pushImage(int x, int y, int w, int h, const uint16_t *pixels) {
    write_pixels({x, y, w, h, 0}, pixels, size_t(w) * h, !swap_);
  }
  uint16_t readPixel(int x, int y) { return gram[y * 320 + x]; }
  void readRect(int x, int y, int width, int height, uint16_t *output) {
    assert(pending_transfers.empty());
    for (int line = 0; line < height; ++line)
      for (int column = 0; column < width; ++column) {
        uint16_t value = gram[(y + line) * 320 + x + column];
        output[line * width + column] = uint16_t(value << 8 | value >> 8);
      }
  }
  uint8_t readcommand8(int, int index) { return index == 2 ? 0x93 : 0x41; }
  void setTextColor(uint16_t, uint16_t = 0) {}
  void setTextDatum(int) {}
  void fillRoundRect(int x, int y, int w, int h, int, uint16_t color) {
    fillRect(x, y, w, h, color);
  }
  void drawString(const char *, int, int) {}
  void setTextFont(int) {}
  void setCursor(int, int) {}
  void setTextWrap(bool) {}
  void print(const char *) {}
  void print(int) {}
};
struct Storage {
  uint32_t clock_hz() { return 20000000; }
  unsigned read_buffer_bytes() { return 0; }
} storage;
