#!/usr/bin/env python3
"""SD adapter lifetime/fault tests and real SDK syntax; no physical device access."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
CASES = ("configuration", "clock_and_space", "startup_failures", "foreign_vfs",
         "cleanup_failures", "original_and_cleanup_error", "card_status", "space_failure")


class IdfSdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="muse-idf-sd-host-")
        cls.folder = Path(cls.temporary.name)
        for name in ("FS.h", "SPI.h", "sd_defines.h", "vfs_api.h", "driver/sdspi_host.h",
                     "driver/spi_common.h", "diskio_impl.h", "diskio_sdmmc.h",
                     "esp_vfs_fat.h", "ff.h", "sdmmc_cmd.h"):
            target = cls.folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('#pragma once\n#include "idf_sd_fake_sdk.hpp"\n')
        cls.binary = cls.folder / "lifecycle"
        subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                        "-DARDUINO_ARCH_ESP32", "-DMVB_USE_IDF_SDSPI",
                        f"-I{cls.folder}", f"-I{FIXTURES}", f"-I{ROOT / 'firmware/include'}",
                        str(FIXTURES / "idf_sd_lifecycle.cpp"), "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_lifecycle_and_storage_faults(self):
        for case in CASES:
            with self.subTest(case=case):
                env = os.environ.copy()
                env["ASAN_OPTIONS"] = "detect_leaks=0"
                env["UBSAN_OPTIONS"] = "halt_on_error=1"
                subprocess.run([str(self.binary), case], check=True, env=env, timeout=15)

    def test_guard_does_not_require_sdk_in_normal_host_build(self):
        source = self.folder / "guard.cpp"
        source.write_text('#include "idf_sd.hpp"\nint main() {}\n')
        subprocess.run(["g++", "-std=c++17", "-fsyntax-only",
                        f"-I{ROOT / 'firmware/include'}", str(source)], check=True)

    def test_enabled_fatfs_status_check_configuration(self):
        binary = self.folder / "status-enabled"
        subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-DARDUINO_ARCH_ESP32", "-DMVB_USE_IDF_SDSPI",
                        "-DMVB_IDF_SD_DISK_STATUS_CHECK=1",
                        f"-I{self.folder}", f"-I{FIXTURES}", f"-I{ROOT / 'firmware/include'}",
                        str(FIXTURES / "idf_sd_lifecycle.cpp"), "-o", str(binary)], check=True)
        subprocess.run([str(binary), "clock_and_space"], check=True, timeout=15)

    def test_real_installed_xtensa_sdk_syntax(self):
        packages = ROOT / ".dev-tools/platformio-core/packages"
        compiler = packages / "toolchain-xtensa-esp-elf/bin/xtensa-esp32-elf-g++"
        sdk = (packages / "framework-arduinoespressif32-esp32-libs").resolve()
        arduino = (packages / "framework-arduinoespressif32").resolve()
        if not compiler.exists() or not sdk.exists() or not arduino.exists():
            self.skipTest("Installed ESP32 SDK/compiler unavailable; host lifecycle tests remain valid")
        includes = [ROOT / "firmware/include", arduino / "cores/esp32", arduino / "variants/esp32",
                    arduino / "libraries/FS/src", arduino / "libraries/SPI/src", arduino / "libraries/SD/src",
                    sdk / "dio_qspi/include"]
        other_targets = {"esp32s2", "esp32s3", "esp32c2", "esp32c3", "esp32c5", "esp32c6", "esp32h2", "esp32p4"}
        includes += [p for p in (sdk / "include").rglob("*")
                     if p.is_dir() and p.name == "include" and not other_targets.intersection(p.parts)]
        includes += [sdk / p for p in (
            "include/fatfs/src", "include/fatfs/vfs", "include/fatfs/diskio",
            "include/freertos/config/include/freertos",
            "include/freertos/FreeRTOS-Kernel/portable/xtensa/include/freertos",
            "include/soc/esp32/register", "include/newlib/platform_include",
            "include/esp_rom/esp32/include/esp32", "include/lwip/lwip/src/include")]
        source = self.folder / "real-sdk.cpp"
        source.write_text('#include "idf_sd.hpp"\n'
                          'void check(mvb::IdfSd &sd, SPIClass &spi) {\n'
                          'sd.begin(5,spi,10000000,"/sd",5,false); sd.exists("/a");\n'
                          'auto f=sd.open("/"); f.openNextFile().close(); sd.mkdir("/a");\n'
                          'sd.rename("/a","/b"); sd.remove("/a"); sd.cardType();\n'
                          'sd.cardSize(); sd.totalBytes(); sd.usedBytes(); sd.end(); }\n')
        subprocess.run([str(compiler), "-std=gnu++2b", "-fsyntax-only", "-DARDUINO_ARCH_ESP32",
                        "-DARDUINO=10812", "-DESP32", "-DMVB_USE_IDF_SDSPI"] +
                       [f"-I{p}" for p in dict.fromkeys(includes) if p.exists()] + [str(source)], check=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
