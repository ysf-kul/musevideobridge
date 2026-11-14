#pragma once

// Experimental classic-ESP32 adapter. The caller owns mount/unmount and must
// close all Arduino File and POSIX handles before end(). Do not also initialize
// Arduino SPIClass/SD on SPI3; this adapter owns that bus and its DMA channel.
#if defined(ARDUINO_ARCH_ESP32) && defined(MVB_USE_IDF_SDSPI)
#include <FS.h>
#include <SPI.h>
#include <sd_defines.h>
#include <vfs_api.h>
#include <driver/sdspi_host.h>
#include <driver/spi_common.h>
#include <diskio_impl.h>
#include <diskio_sdmmc.h>
#include <esp_vfs_fat.h>
#include <ff.h>
#include <sdmmc_cmd.h>
#include <cstring>

#ifndef MVB_IDF_SD_DISK_STATUS_CHECK
#define MVB_IDF_SD_DISK_STATUS_CHECK 0
#endif

namespace mvb {
class IdfSd : public fs::FS {
  sdmmc_card_t card_{};
  FATFS *fat_ = nullptr;
  BYTE drive_ = FF_DRV_NOT_USED;
  char drive_path_[3] = {'0', ':', 0};
  int device_ = -1;
  bool bus_owned_ = false, device_owned_ = false;
  bool disk_owned_ = false, vfs_owned_ = false, fat_attached_ = false;
  bool ready_ = false, card_fault_ = false;
  uint32_t requested_hz_ = 0;
  esp_err_t last_error_ = ESP_OK, cleanup_error_ = ESP_OK;
  FRESULT fat_error_ = FR_OK;
  const char *error_stage_ = "none";

  bool failure(esp_err_t error, const char *stage) {
    // Preserve the operation error even when cleanup itself fails. A retained
    // resource is never silently reused or freed as somebody else's resource.
    end();
    last_error_ = error;
    error_stage_ = stage;
    return false;
  }
  bool cleanup_failure(esp_err_t error, const char *stage) {
    last_error_ = cleanup_error_ = error;
    error_stage_ = stage;
    return false;
  }
  bool space(uint64_t &total, uint64_t &used) {
    total = used = 0;
    if (!mounted())
      return false;
    DWORD free_clusters = 0;
    FATFS *info = nullptr;
    fat_error_ = f_getfree(drive_path_, &free_clusters, &info);
    if (fat_error_ != FR_OK || !info || info->n_fatent < 2 ||
        free_clusters > info->n_fatent - 2) {
      last_error_ = ESP_FAIL;
      error_stage_ = "fat_space";
      return false;
    }
    const uint64_t cluster_bytes = uint64_t(info->csize) * card_.csd.sector_size;
    total = uint64_t(info->n_fatent - 2) * cluster_bytes;
    used = uint64_t(info->n_fatent - 2 - free_clusters) * cluster_bytes;
    return true;
  }

public:
  IdfSd() : fs::FS(fs::FSImplPtr(new VFSImpl())) {}
  IdfSd(const IdfSd &) = delete;
  IdfSd &operator=(const IdfSd &) = delete;
  ~IdfSd() { end(); }

  bool begin(uint8_t cs = 5, SPIClass & /*compatibility_only*/ = SPI,
             uint32_t frequency = 10000000, const char *path = "/sd",
             uint8_t max_files = 5, bool format_if_empty = false) {
    if (format_if_empty || cs != 5 || !path || std::strcmp(path, "/sd") ||
        !max_files || max_files > 8 ||
        (frequency != 10000000 && frequency != 20000000 && frequency != 40000000)) {
      last_error_ = ESP_ERR_INVALID_ARG;
      error_stage_ = "configuration";
      return false;
    }
    if (mounted() && requested_hz_ == frequency)
      return cardType() != CARD_NONE;
    if (!end())
      return false;
    card_ = {};
    card_fault_ = false;
    fat_error_ = FR_OK;
    requested_hz_ = frequency;
    spi_bus_config_t bus{};
    bus.mosi_io_num = 23;
    bus.miso_io_num = 19;
    bus.sclk_io_num = 18;
    bus.quadwp_io_num = bus.quadhd_io_num = -1;
    bus.data4_io_num = bus.data5_io_num = bus.data6_io_num = bus.data7_io_num = -1;
    bus.max_transfer_sz = 16384;
    bus.flags = SPICOMMON_BUSFLAG_MASTER;
    auto error = spi_bus_initialize(SPI3_HOST, &bus, SPI_DMA_CH_AUTO);
    if (error != ESP_OK)
      return failure(error, "spi_bus_initialize");
    bus_owned_ = true;
    error = sdspi_host_init();
    if (error != ESP_OK)
      return failure(error, "sdspi_host_init");
    sdspi_device_config_t device = SDSPI_DEVICE_CONFIG_DEFAULT();
    device.host_id = SPI3_HOST;
    device.gpio_cs = GPIO_NUM_5;
    error = sdspi_host_init_device(&device, &device_);
    if (error != ESP_OK)
      return failure(error, "sdspi_host_init_device");
    device_owned_ = true;
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = device_;
    host.max_freq_khz = int(frequency / 1000);
    error = sdmmc_card_init(&host, &card_);
    if (error != ESP_OK)
      return failure(error, "sdmmc_card_init");
    if (!card_.is_mem || !card_.csd.sector_size || !card_.csd.capacity)
      return failure(ESP_ERR_NOT_SUPPORTED, "memory_card_required");
    error = ff_diskio_get_drive(&drive_);
    if (error != ESP_OK || drive_ >= FF_VOLUMES)
      return failure(error == ESP_OK ? ESP_ERR_INVALID_STATE : error, "fat_drive");
    drive_path_[0] = char('0' + drive_);
    ff_diskio_register_sdmmc(drive_, &card_);
    disk_owned_ = true;
    ff_sdmmc_set_disk_status_check(drive_, MVB_IDF_SD_DISK_STATUS_CHECK != 0);
    const esp_vfs_fat_conf_t config{path, drive_path_, max_files};
    error = esp_vfs_fat_register_cfg(&config, &fat_);
    if (error != ESP_OK)
      return failure(error, "vfs_register");
    vfs_owned_ = true;
    fat_attached_ = true;
    fat_error_ = f_mount(fat_, drive_path_, 1);
    if (fat_error_ != FR_OK)
      return failure(ESP_FAIL, "fat_mount");
    // Stable literal: FSImpl stores the mountpoint pointer without copying it.
    _impl->mountpoint("/sd");
    ready_ = true;
    last_error_ = cleanup_error_ = ESP_OK;
    error_stage_ = "none";
    return true;
  }

  bool end() {
    ready_ = false;
    _impl->mountpoint(nullptr);
    cleanup_error_ = ESP_OK;
    if (fat_attached_) {
      const auto result = f_mount(nullptr, drive_path_, 0);
      if (result != FR_OK) {
        fat_error_ = result;
        return cleanup_failure(ESP_FAIL, "fat_unmount");
      }
      fat_attached_ = false;
    }
    if (vfs_owned_) {
      auto error = esp_vfs_fat_unregister_path("/sd");
      if (error != ESP_OK)
        return cleanup_failure(error, "vfs_unregister");
      vfs_owned_ = false;
      fat_ = nullptr;
    }
    if (disk_owned_) {
      ff_diskio_unregister(drive_);
      disk_owned_ = false;
      drive_ = FF_DRV_NOT_USED;
    }
    if (device_owned_) {
      auto error = sdspi_host_remove_device(device_);
      if (error != ESP_OK)
        return cleanup_failure(error, "sdspi_remove_device");
      device_owned_ = false;
      device_ = -1;
    }
    if (bus_owned_) {
      auto error = spi_bus_free(SPI3_HOST);
      if (error != ESP_OK)
        return cleanup_failure(error, "spi_bus_free");
      bus_owned_ = false;
    }
    card_fault_ = false;
    last_error_ = ESP_OK;
    error_stage_ = "none";
    return true;
  }

  bool mounted() const { return ready_ && !card_fault_; }
  sdcard_type_t cardType() {
    if (!mounted())
      return CARD_NONE;
    auto error = sdmmc_get_status(&card_);
    if (error != ESP_OK) {
      card_fault_ = true;
      _impl->mountpoint(nullptr);
      last_error_ = error;
      error_stage_ = "card_status";
      return CARD_NONE;
    }
    return card_.is_mmc ? CARD_MMC : (card_.ocr & (1U << 30)) ? CARD_SDHC : CARD_SD;
  }
  uint64_t cardSize() {
    return cardType() == CARD_NONE ? 0 : uint64_t(card_.csd.capacity) * card_.csd.sector_size;
  }
  size_t numSectors() const { return mounted() ? card_.csd.capacity : 0; }
  size_t sectorSize() const { return mounted() ? card_.csd.sector_size : 0; }
  uint64_t totalBytes() { uint64_t total, used; return space(total, used) ? total : 0; }
  uint64_t usedBytes() { uint64_t total, used; return space(total, used) ? used : 0; }
  uint32_t requestedClockHz() const { return requested_hz_; }
  uint32_t actualClockHz() const {
    return mounted() && card_.real_freq_khz > 0 ? uint32_t(card_.real_freq_khz) * 1000 : 0;
  }
  bool dmaInitialized() const { return bus_owned_ && device_owned_; }
  esp_err_t lastError() const { return last_error_; }
  esp_err_t cleanupError() const { return cleanup_error_; }
  FRESULT lastFatError() const { return fat_error_; }
  const char *errorStage() const { return error_stage_; }
};
} // namespace mvb
#endif
