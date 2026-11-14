# Installed SD driver investigation — 2026-11-13

The installed Arduino 3.3.12 / ESP-IDF 5.5.5 libraries expose a concrete software
problem worth measuring before attributing the low read rate to the card. Espressif
Newlib defaults to **128-byte stdio buffers**, and the SDK's FatFS VFS reports a
preferred block size of zero. The checked `SdFile` adapter originally used direct
`fopen`/`fread` without choosing a buffer size.

Arduino's ordinary FS wrapper explicitly substitutes a **4096-byte buffer** for
that same zero block-size case. Bypassing its wrapper to check `fflush`/`fsync`
errors therefore also bypassed this buffering behavior.

This was verified against the installed sources and compiled Xtensa libraries:

- `_fread_r` repeatedly copies from its FILE buffer and calls `__srefill_r`; it does
  not bypass the buffer for a large requested read.
- `__srefill_r` passes that buffer's size to the underlying read callback.
- Each FatFS `f_read` calls `validate`, which calls `ff_disk_status`. Arduino's
  implementation sends an SD **CMD13** status transaction.
- Arduino SD has a multi-sector **CMD18** path. A 128-byte FatFS read cannot enter
  its direct sector-transfer path, while larger aligned requests can.

A 153,600-byte read therefore needs roughly 1,200 refills at 128 bytes, versus 38
at 4096 or 19 at 8192 bytes. These are calculated refill counts, not measured driver
call counts. Alignment, sector caches, seeks and filesystem boundaries affect the
actual number. The hardware benchmark must establish the time saved.

The first comparison should retain the current SD driver and use a reusable
4096/8192-byte buffer on read-only handles, allocated once and checked against actual
free internal RAM. Buffering on upload writers and checked checkpoint durability
should retain their existing behavior. A direct POSIX `read`/`lseek` baseline on a
separate descriptor can also quantify stdio overhead.

If the remaining cost still warrants a different driver, the installed ESP-IDF
**SDSPI plus FatFS** is a practical next candidate. It can use DMA on the SD's
independent SPI3/VSPI bus while retaining `/sd` and the checked POSIX file adapter.
Its configurable disk-status checks let benchmarks isolate repeated status polling.
The SD metadata/directory adapter and mount/unmount ownership must be updated
carefully; keep formatting disabled and preserve read-error/removal detection.
Adding SdFat with dedicated SPI remains a further option if measurements justify it.

The existing Arduino payload path itself uses 64-byte FIFO transfers and busy waits,
without DMA. Source inspection alone cannot divide the remaining time among card
token latency, payload transfer, CRC16, status commands, FAT traversal and copying.
Useful instrumentation counts request sizes, single/multi-sector reads, status calls,
token polls and retries, with separate timing for each command, payload and CRC.
The SDK also supplies a read-only contiguous-file check to investigate fragmentation.

No serial device was opened or code changed by this inspection. Source hashes,
commands and six compiled call-path extracts are recorded in
[sd-driver-inspection.json](evidence/sd-driver-inspection.json). These findings support
the next real-device comparison; they do not by themselves prove that the card or
CPU imposes the measured limit.
