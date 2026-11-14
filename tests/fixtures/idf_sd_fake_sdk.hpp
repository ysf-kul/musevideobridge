#pragma once
// Host fault injection only. The separate Xtensa syntax check uses real headers.
#include <cassert>
#include <cstdint>
#include <cstring>
#include <memory>
#include <set>
#include <string>
#include <vector>
using esp_err_t = int;
constexpr int ESP_OK = 0, ESP_FAIL = 1, ESP_ERR_INVALID_ARG = 2;
constexpr int ESP_ERR_INVALID_STATE = 3, ESP_ERR_NOT_SUPPORTED = 4;
using BYTE = unsigned char;
using DWORD = uint32_t;
using FRESULT = int;
constexpr int FR_OK = 0, FR_DISK_ERR = 1, FR_NO_FILESYSTEM = 13;
constexpr int FF_DRV_NOT_USED = 255, FF_VOLUMES = 10;
constexpr int SPI3_HOST = 2, SPI_DMA_CH_AUTO = 3, SPICOMMON_BUSFLAG_MASTER = 1;
constexpr int GPIO_NUM_5 = 5;
enum sdcard_type_t { CARD_NONE, CARD_MMC, CARD_SD, CARD_SDHC, CARD_UNKNOWN };
struct SPIClass {};
inline SPIClass SPI;
struct spi_bus_config_t {
  int mosi_io_num, miso_io_num, sclk_io_num, quadwp_io_num, quadhd_io_num;
  int data4_io_num, data5_io_num, data6_io_num, data7_io_num;
  int max_transfer_sz;
  uint32_t flags;
};
struct sdspi_device_config_t { int host_id = 0, gpio_cs = 0; };
struct sdmmc_host_t { int slot = 0, max_freq_khz = 0; };
struct sdmmc_card_t {
  sdmmc_host_t host;
  uint32_t ocr = 0, is_mem = 0, is_mmc = 0;
  struct { uint32_t capacity = 0, sector_size = 0; } csd;
  int real_freq_khz = 0;
};
struct FATFS { uint32_t n_fatent = 1000002, csize = 8; };
struct esp_vfs_fat_conf_t { const char *base_path, *fat_drive; size_t max_files; };
#define SDSPI_DEVICE_CONFIG_DEFAULT() sdspi_device_config_t{}
#define SDSPI_HOST_DEFAULT() sdmmc_host_t{}
namespace fs {
class FSImpl {
  const char *mount_ = nullptr;
public:
  virtual ~FSImpl() = default;
  void mountpoint(const char *value) { mount_ = value; }
  const char *mountpoint() { return mount_; }
};
using FSImplPtr = std::shared_ptr<FSImpl>;
class FS {
protected:
  FSImplPtr _impl;
public:
  explicit FS(FSImplPtr value) : _impl(value) {}
  const char *mountpoint() { return _impl->mountpoint(); }
};
}
class VFSImpl : public fs::FSImpl {};
namespace fake {
inline std::set<std::string> failures;
inline std::vector<std::string> calls;
inline bool bus = false, device = false, disk = false, vfs = false, fat = false;
inline bool foreign_vfs = false, status_check = false;
inline FATFS filesystem;
inline uint32_t free_clusters = 250000;
inline int status_calls = 0;
inline bool fail(const char *name) {
  calls.emplace_back(name);
  return failures.count(name) != 0;
}
inline void clean() {
  assert(!bus && !device && !disk && !vfs && !fat);
  calls.clear(); failures.clear(); foreign_vfs = false;
  status_calls = 0; free_clusters = 250000;
}
inline bool empty() { return !bus && !device && !disk && !vfs && !fat; }
}
inline esp_err_t spi_bus_initialize(int host, const spi_bus_config_t *bus, int dma) {
  assert(host == SPI3_HOST && dma == SPI_DMA_CH_AUTO);
  assert(bus->mosi_io_num == 23 && bus->miso_io_num == 19 && bus->sclk_io_num == 18);
  assert(bus->quadwp_io_num == -1 && bus->quadhd_io_num == -1);
  assert(bus->data4_io_num == -1 && bus->data7_io_num == -1);
  assert(bus->max_transfer_sz == 16384 && bus->flags == SPICOMMON_BUSFLAG_MASTER);
  if (fake::fail("spi_bus_initialize")) return ESP_ERR_INVALID_STATE;
  assert(!fake::bus); fake::bus = true; return ESP_OK;
}
inline esp_err_t spi_bus_free(int host) {
  assert(host == SPI3_HOST && fake::bus && !fake::device);
  if (fake::fail("spi_bus_free")) return ESP_FAIL;
  fake::bus = false; return ESP_OK;
}
inline esp_err_t sdspi_host_init() { return fake::fail("sdspi_host_init") ? ESP_FAIL : ESP_OK; }
inline esp_err_t sdspi_host_init_device(const sdspi_device_config_t *config, int *handle) {
  assert(fake::bus && config->host_id == SPI3_HOST && config->gpio_cs == 5);
  if (fake::fail("sdspi_host_init_device")) return ESP_FAIL;
  fake::device = true; *handle = 17; return ESP_OK;
}
inline esp_err_t sdspi_host_remove_device(int handle) {
  assert(handle == 17 && fake::device && !fake::disk && !fake::vfs && !fake::fat);
  if (fake::fail("sdspi_remove_device")) return ESP_FAIL;
  fake::device = false; return ESP_OK;
}
inline esp_err_t sdmmc_card_init(const sdmmc_host_t *host, sdmmc_card_t *card) {
  assert(host->slot == 17 && fake::device);
  if (fake::fail("sdmmc_card_init")) return ESP_FAIL;
  card->host = *host; card->is_mem = 1; card->ocr = 1U << 30;
  card->csd.capacity = 8388608; card->csd.sector_size = 512;
  card->real_freq_khz = host->max_freq_khz > 20000 ? 20000 : host->max_freq_khz;
  return ESP_OK;
}
inline esp_err_t sdmmc_get_status(sdmmc_card_t *) {
  fake::status_calls++; return fake::fail("card_status") ? ESP_FAIL : ESP_OK;
}
inline esp_err_t ff_diskio_get_drive(BYTE *drive) {
  if (fake::fail("fat_drive")) return ESP_FAIL;
  *drive = 3; return ESP_OK;
}
inline void ff_diskio_register_sdmmc(BYTE drive, sdmmc_card_t *) {
  assert(drive == 3 && fake::device); fake::calls.emplace_back("disk_register"); fake::disk = true;
}
inline void ff_diskio_unregister(BYTE drive) {
  assert(drive == 3 && fake::disk && !fake::vfs && !fake::fat);
  fake::calls.emplace_back("disk_unregister"); fake::disk = false;
}
inline void ff_sdmmc_set_disk_status_check(BYTE drive, bool enabled) {
  assert(drive == 3); fake::status_check = enabled;
}
inline esp_err_t esp_vfs_fat_register_cfg(const esp_vfs_fat_conf_t *config, FATFS **fs) {
  assert(std::strcmp(config->base_path, "/sd") == 0 && std::strcmp(config->fat_drive, "3:") == 0);
  assert(config->max_files == 5 && fake::disk);
  if (fake::fail("vfs_register") || fake::foreign_vfs) return ESP_ERR_INVALID_STATE;
  *fs = &fake::filesystem; fake::vfs = true; return ESP_OK;
}
inline esp_err_t esp_vfs_fat_unregister_path(const char *path) {
  assert(std::strcmp(path, "/sd") == 0 && fake::vfs && !fake::fat && !fake::foreign_vfs);
  if (fake::fail("vfs_unregister")) return ESP_FAIL;
  fake::vfs = false; return ESP_OK;
}
inline FRESULT f_mount(FATFS *fs, const char *drive, int immediate) {
  assert(std::strcmp(drive, "3:") == 0);
  if (fs) {
    assert(immediate == 1 && fake::vfs && fake::disk);
    fake::fat = true;
    return fake::fail("fat_mount") ? FR_NO_FILESYSTEM : FR_OK;
  }
  assert(immediate == 0 && fake::fat && fake::vfs);
  if (fake::fail("fat_unmount")) return FR_DISK_ERR;
  fake::fat = false; return FR_OK;
}
inline FRESULT f_getfree(const char *drive, DWORD *free, FATFS **fs) {
  assert(std::strcmp(drive, "3:") == 0 && fake::fat);
  if (fake::fail("fat_space")) return FR_DISK_ERR;
  *free = fake::free_clusters; *fs = &fake::filesystem; return FR_OK;
}
