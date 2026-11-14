#include "idf_sd.hpp"
#include <algorithm>
#include <iostream>

int main(int argc, char **argv) {
  assert(argc == 2);
  const std::string test = argv[1];
  fake::clean();
  if (test == "configuration") {
    mvb::IdfSd sd;
    assert(!sd.begin(5, SPI, 10000000, "/sd", 5, true));
    assert(!sd.begin(6, SPI));
    assert(!sd.begin(5, SPI, 80000000));
    assert(!sd.begin(5, SPI, 10000000, "/other"));
    assert(!sd.begin(5, SPI, 10000000, "/sd", 0));
    assert(!sd.begin(5, SPI, 10000000, "/sd", 9));
    assert(sd.lastError() == ESP_ERR_INVALID_ARG && fake::calls.empty());
  } else if (test == "clock_and_space") {
    for (uint32_t hz : {10000000, 20000000, 40000000}) {
      mvb::IdfSd sd;
      assert(sd.begin(5, SPI, hz));
      assert(sd.mounted() && sd.dmaInitialized());
      assert(std::strcmp(sd.mountpoint(), "/sd") == 0);
      assert(sd.requestedClockHz() == hz);
      assert(sd.actualClockHz() == std::min(hz, uint32_t(20000000)));
      assert(fake::status_check == (MVB_IDF_SD_DISK_STATUS_CHECK != 0));
      assert(sd.cardType() == CARD_SDHC && fake::status_calls == 1);
      assert(sd.cardSize() == 4294967296ULL);
      assert(sd.totalBytes() == 4096000000ULL && sd.usedBytes() == 3072000000ULL);
      assert(sd.end() && fake::empty() && sd.mountpoint() == nullptr);
      fake::clean();
    }
  } else if (test == "startup_failures") {
    for (const char *stage : {"spi_bus_initialize", "sdspi_host_init", "sdspi_host_init_device",
                             "sdmmc_card_init", "fat_drive", "vfs_register", "fat_mount"}) {
      mvb::IdfSd sd;
      fake::failures.insert(stage);
      assert(!sd.begin());
      assert(!sd.mounted() && fake::empty());
      assert(sd.lastError() != ESP_OK && std::strcmp(sd.errorStage(), stage) == 0);
      assert(sd.cleanupError() == ESP_OK);
      if (std::strcmp(stage, "spi_bus_initialize") == 0)
        assert(fake::calls.size() == 1); // Never free an already-owned foreign bus.
      if (std::strcmp(stage, "fat_mount") == 0)
        assert(sd.lastFatError() == FR_NO_FILESYSTEM);
      fake::failures.clear();
      assert(sd.begin() && sd.end()); // Failure leaves a reusable, unmounted adapter.
      fake::clean();
    }
  } else if (test == "foreign_vfs") {
    mvb::IdfSd sd;
    fake::foreign_vfs = true;
    assert(!sd.begin() && fake::empty() && fake::foreign_vfs);
    assert(sd.lastError() == ESP_ERR_INVALID_STATE);
    assert(std::find(fake::calls.begin(), fake::calls.end(), "vfs_unregister") == fake::calls.end());
  } else if (test == "cleanup_failures") {
    for (const char *stage : {"fat_unmount", "vfs_unregister", "sdspi_remove_device", "spi_bus_free"}) {
      mvb::IdfSd sd;
      assert(sd.begin());
      fake::failures.insert(stage);
      assert(!sd.end() && !sd.mounted() && sd.mountpoint() == nullptr);
      assert(sd.cleanupError() == ESP_FAIL && sd.lastError() == ESP_FAIL);
      assert(std::strcmp(sd.errorStage(), stage) == 0 && fake::bus);
      if (std::strcmp(stage, "fat_unmount") == 0)
        assert(fake::device && fake::disk && fake::vfs && fake::fat);
      if (std::strcmp(stage, "vfs_unregister") == 0)
        assert(fake::device && fake::disk && fake::vfs && !fake::fat);
      if (std::strcmp(stage, "sdspi_remove_device") == 0)
        assert(fake::device && !fake::disk && !fake::vfs && !fake::fat);
      fake::failures.clear();
      assert(sd.end() && fake::empty()); // Retry releases only retained owned resources.
      assert(sd.begin() && sd.end());
      fake::clean();
    }
  } else if (test == "original_and_cleanup_error") {
    mvb::IdfSd sd;
    fake::failures = {"fat_mount", "fat_unmount"};
    assert(!sd.begin() && sd.lastError() == ESP_FAIL && sd.cleanupError() == ESP_FAIL);
    assert(std::strcmp(sd.errorStage(), "fat_mount") == 0 && fake::fat && fake::bus);
    fake::failures.clear();
    assert(sd.end() && fake::empty());
  } else if (test == "card_status") {
    mvb::IdfSd sd;
    assert(sd.begin());
    fake::failures.insert("card_status");
    assert(sd.cardType() == CARD_NONE && !sd.mounted() && sd.mountpoint() == nullptr);
    assert(sd.lastError() == ESP_FAIL && std::strcmp(sd.errorStage(), "card_status") == 0);
    assert(sd.cardSize() == 0 && sd.totalBytes() == 0 && sd.usedBytes() == 0);
    assert(fake::status_calls == 1); // A latched removal does not keep sending commands.
    fake::failures.clear();
    assert(sd.end() && sd.begin() && sd.cardType() == CARD_SDHC);
  } else if (test == "space_failure") {
    mvb::IdfSd sd;
    assert(sd.begin());
    fake::failures.insert("fat_space");
    assert(sd.totalBytes() == 0 && sd.lastFatError() == FR_DISK_ERR);
    fake::failures.clear();
    fake::free_clusters = fake::filesystem.n_fatent;
    assert(sd.usedBytes() == 0 && sd.lastError() == ESP_FAIL);
  } else {
    assert(false && "Unknown host test");
  }
  assert(fake::empty());
  std::cout << "PASS " << test << '\n';
}
