# Optional ESP-IDF SDSPI experiment

The experimental [idf_sd.hpp](../firmware/include/idf_sd.hpp) adapter retains the
existing `/sd` FAT VFS and Arduino `FS` / `VFSImpl` / `File` metadata interface while
replacing the Arduino SD payload driver with ESP-IDF SDSPI. Its purpose is to
measure whether DMA and a different SD transaction strategy remove the remaining
software read cost. No performance gain or physical clock stability is claimed
from the implementation or host tests.

The mandatory Muse files, installed SDK, main application, engine and PlatformIO
configuration were not changed by this adapter implementation. Root integration
and physical qualification are separate steps. The source investigation is in
[SD_DRIVER_INVESTIGATION.md](SD_DRIVER_INVESTIGATION.md).

## Interface and ownership

`mvb::IdfSd` derives from the existing Arduino `fs::FS` and creates its ordinary
`VFSImpl`. Thus `open`, `exists`, `mkdir`, `remove`, `rename`, directory iteration
and returned `File` objects preserve that framework interface. It also provides
the existing SD-style `begin`, `cardType`, `cardSize`, `numSectors`, `sectorSize`,
`totalBytes` and `usedBytes` calls. `end()` deliberately returns a checked Boolean.
The checked POSIX playback/upload reader, writer, checkpoint and selection code
can keep its `/sd/...` paths and `fflush` / `fsync` checks.

The header is inactive unless both `ARDUINO_ARCH_ESP32` and `MVB_USE_IDF_SDSPI` are
defined. For this CYD variant it exclusively owns **SPI3/VSPI**, SCK18, MOSI23,
MISO19 and CS5, with `SPI_DMA_CH_AUTO` and a 16384-byte configured transfer ceiling.
Unused quad/octal pins are explicitly disabled. TFT SPI2/HSPI ownership remains
separate. A successful DMA-capable bus initialization is reported, but its flag
alone does not prove physically completed SD transfers or throughput.

`begin` accepts only CS5, `/sd`, 1–8 open files and requested 10/20/40 MHz. Passing
the compatibility SPIClass argument does not initialize or use Arduino's SPI bus.
The caller must skip its previous `sd_bus.begin(...)` in this optional branch.
Both mount and remount require **all Arduino File and POSIX handles closed** and
any prefetch worker acknowledged/joined before resources are changed. The wrapper
is noncopyable because the disk driver stores its card structure address.

Suggested integration, to be made and build-tested by the firmware owner:

```cpp
#ifdef MVB_USE_IDF_SDSPI
#include "idf_sd.hpp"
mvb::IdfSd SD;
#else
#include <SD.h>
#endif
```

Before every mount, check the optional `SD.end()` result and expose its detailed
error if cleanup fails. Do not continue into an Arduino fallback on an ambiguously
retained bus/VFS resource. Existing diagnostic reads of SPIClass state must use
the IDF adapter's `requestedClockHz()` and `actualClockHz()` for this branch;
`actualClockHz()` reports the initialized card/host's real-frequency field. A
requested 40 MHz can negotiate a lower actual rate. Neither value is an external
wire-clock measurement.

## Mount, removal and cleanup

The adapter explicitly performs bus initialization, SDSPI device attachment, card
initialization, free FAT drive selection, disk registration, VFS registration and
`f_mount`. It retains a bounded card structure rather than allocating a video-sized
buffer. Allocation by the SDK's device/DMA/VFS layers remains checked and must be
included in measured runtime RAM. The ordinary Arduino VFSImpl retains its own
file buffering behavior; the checked direct POSIX/stdio adapter remains separately
responsible for its buffer policy.

There is **no formatting, partitioning, raw-write or erase API** in this wrapper.
A caller requesting format is rejected before any bus operation. An unrecognized
filesystem fails rather than being replaced. Existing VFS registration conflicts
fail closed; only registration that this object completed can be unregistered.

Cleanup checks FAT unmount, VFS unregister, SDSPI device removal and SPI bus free,
in that dependency order. Failure stops dependent destruction and retains ownership
for an explicit retry. The Arduino mountpoint is cleared immediately to prevent
new FS operations during cleanup. Diagnostics distinguish `lastError`, the
independent `cleanupError`, `lastFatError` and `errorStage`; cleanup cannot replace
the original mount failure in the reported operation error. The object should
remain alive if cleanup fails; a caller must resolve/reset the device before
destroying an object that still owns an active resource.

The inspected upstream ESP-IDF v5.5.5 convenience SDSPI mount function allocates
its context after successful disk/VFS/FAT setup and has incomplete late-allocation
cleanup. Its unmount path also frees card/context before returning the VFS
unregister result. The wrapper therefore uses the documented lower-level APIs
instead of depending on ambiguous convenience-helper ownership. This observation
comes from the matching upstream version's source, not a claim that that rare
failure was induced on this physical board.

For performance comparison, per-FatFS-operation status polling defaults to disabled
(`MVB_IDF_SD_DISK_STATUS_CHECK=0`); it can be compiled as 1 for a measured comparison.
`cardType()` still sends a real `sdmmc_get_status`, rather than returning only
cached card identity. A failed status response latches removal/error, clears the
Arduino mountpoint and prevents further metadata reads until remount. SD read/write
CRC/response/timeout errors continue through the SDK/FatFS checked file path even
when repeated status polling is disabled. Cached filesystem data alone cannot
prove a card remains inserted; caller status/removal checks remain required.

`totalBytes`/`usedBytes` use checked `f_getfree` on the actual assigned FAT drive,
with 64-bit cluster/sector arithmetic and invalid free-cluster rejection. A failed
query returns zero and retains diagnostics. `fsync` plus synchronous SD writes do
not guarantee transactional recovery from sudden card power loss.

## Verification boundaries

Run without hardware or a full firmware build:

```sh
python3 tests/idf_sd_test.py
```

Host tests compile the real adapter with controlled SDK fault stubs and exercise
eight lifecycle/storage scenarios under ASan/UBSan (`detect_leaks=0`, so no leak-check
claim). They cover every startup failure, forbidden format/invalid configuration,
preservation of a foreign bus/VFS, cleanup retry order, original versus cleanup
errors, status-failure latching, nonzero drive selection and 64-bit card/filesystem
sizes. A separate configuration test enables actual FatFS status polling. A normal
host-build guard test confirms that the unused experiment needs no SDK headers.

The standalone Xtensa syntax test uses the **actual installed Arduino 3.3.12 /
ESP-IDF 5.5.5 headers and compiler**, including a consumer of the existing File /
directory/rename/space APIs. It is skipped with an explicit reason if those tools
are absent. It is not a complete linked firmware build or a physical SD test.

Initial syntax/lifecycle evidence is in
idf-sd-source (local-only evidence: `../.verification/session-2026-11-13/idf-sd-source/`).
Matching-version upstream source references and SHA-256:

| Source | Digest |
| --- | --- |
| [vfs_fat_sdmmc.c](https://github.com/espressif/esp-idf/blob/v5.5.5/components/fatfs/vfs/vfs_fat_sdmmc.c) | `5668001c1e9b0a9908e9636b9c64b5de9953e8872f97334c19b68dc1a13b2a1b` |
| [diskio_sdmmc.c](https://github.com/espressif/esp-idf/blob/v5.5.5/components/fatfs/diskio/diskio_sdmmc.c) | `63b039b6b79715efd8c3de91d3c7099fbcd5c426ce834b0c70e167811447f83f` |
| [sdspi_host.c](https://github.com/espressif/esp-idf/blob/v5.5.5/components/esp_driver_sdspi/src/sdspi_host.c) | `04d72fe94ce57f287701e2519b78ed5e531b1e2eb59d7ce73493f3f85b9e4e1d` |

Physical qualification must compare sequential 153600-byte read time, per-frame
read/seek/CRC/decode/render costs, runtime heap, negotiated clocks, error counters
and actual completed FPS against the existing driver on the same video. It must
also verify USB incremental writes, stored SHA-256, publication/playback and
preservation of unrelated card files before this experiment becomes a default.
