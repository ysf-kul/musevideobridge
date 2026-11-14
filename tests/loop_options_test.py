"""Actual adaptive loop policy and engine commands with host-only hardware substitutes."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from jpeg_warmup_test import CXX, ROOT, SOURCE as WARM_SOURCE, replace_once

MAIN = (ROOT / "firmware/src/main.cpp").read_text()
START = MAIN.index("struct AdaptiveLoopDiagnostics {")
END = MAIN.index("} adaptive_loop;", START) + len("} adaptive_loop;")
POLICY = "#ifdef MVB_ADAPTIVE_LOOP_YIELD\n" + MAIN[START:END] + "\n#endif\n"
START = MAIN.index("  bool configure_loop_options(uint32_t interval) override {")
END = MAIN.index("  uint32_t session_nonce() override", START)
METHODS = MAIN[START:END]
PRELUDE = "#include <ArduinoJson.h>\n#include <algorithm>\n#include <cstdint>\n#include <cassert>\n"
SOURCE = PRELUDE + POLICY + WARM_SOURCE[:WARM_SOURCE.index("int main(int argc, char **argv)")]
SOURCE += "class LoopRuntime : public Runtime { public: explicit LoopRuntime(Clock &c) : Runtime(c) {}\n" + METHODS + "};\n"
SOURCE += r'''
void commands(bool supported) {
  Clock clock; Storage storage(clock, video_bytes(), true); Output output;
  OwnedPainter painter(clock); LoopRuntime actual_runtime(clock); Runtime host_runtime(clock);
  mvb::Runtime &runtime = supported ? static_cast<mvb::Runtime &>(actual_runtime)
                                   : static_cast<mvb::Runtime &>(host_runtime);
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  select(engine, r); engine.tick();
  unsigned old_closes = closes, old_processes = processes;
  assert(warm_status(r)["result"]["decoder_pipeline"]["cached"] == true);
  auto initial = r.command("{\"method\":\"get_loop_diagnostics\"}");
#ifdef MVB_ADAPTIVE_LOOP_YIELD
  bool available = supported;
#else
  bool available = false;
#endif
  assert(initial["result"]["available"] == available);
  for (unsigned interval : {50000u, 250000u, 1000000u}) {
    std::string command = "{\"method\":\"set_loop_options\",\"idle_yield_us\":" + std::to_string(interval) + "}";
    auto reply = r.command(command.c_str());
    if (available) {
      assert(reply["ok"] == true && reply["result"]["idle_yield_us"] == interval);
      assert(reply["result"]["task_wdt_config_timeout_us"] == 5000000);
      assert(reply["result"]["busy_sleeps"] == 0 && reply["result"]["quiet_sleeps"] == 0);
    } else assert(reply["error"]["code"] == "unsupported_configuration");
    assert(warm_status(r)["result"]["decoder_pipeline"]["cached"] == true);
  }
  for (const char *value : {"0", "49999", "50001", "250001", "1000001", "-1", "\"50000\"", "null"}) {
    std::string command = "{\"method\":\"set_loop_options\",\"idle_yield_us\":" + std::string(value) + "}";
    assert(r.command(command.c_str())["error"]["code"] == "invalid_parameter");
  }
  assert(closes == old_closes && processes == old_processes);
  assert(warm_status(r)["result"]["decoder_pipeline"]["cached"] == true);
  clock.advance(100000); engine.tick();
  assert(r.command("{\"method\":\"get_status\"}")["result"]["state"] == "playing");
}
int main(int argc, char **argv) {
  assert(argc == 2); commands(std::string(argv[1]) == "supported");
}
'''

POLICY_SOURCE = PRELUDE + POLICY + r'''
int main() {
#ifdef MVB_QUALIFIED_FAST_DEFAULTS
  const unsigned safe_default = adaptive_loop.maximum_interval() >= 1000000 ? 1000000 :
      adaptive_loop.maximum_interval() >= 250000 ? 250000 : 50000;
  assert(adaptive_loop.idle_yield_us == safe_default);
  assert(adaptive_loop.configure(50000)); // Preserve the existing policy oracle.
#else
  assert(adaptive_loop.idle_yield_us == 50000);
#endif
  assert(!adaptive_loop.should_sleep(0, 33000));
  assert(adaptive_loop.should_sleep(33000, 66000));
  adaptive_loop.completed_sleep(66000, 67050, false);
  assert(adaptive_loop.busy_sleeps == 1 && adaptive_loop.quiet_sleeps == 0);
  assert(adaptive_loop.should_sleep(68000, 68050)); // Quiet work always sleeps.
  adaptive_loop.completed_sleep(68050, 69000, true);
  assert(adaptive_loop.sleep_us == 2000 && adaptive_loop.last_idle_yield == 69000);
  assert(adaptive_loop.quiet_sleeps == 1);
  ++adaptive_loop.yields;
  const unsigned limit = adaptive_loop.maximum_interval();
  assert(!adaptive_loop.configure(0) && !adaptive_loop.configure(50001));
  if (limit >= 250000) {
    assert(adaptive_loop.configure(250000));
    assert(!adaptive_loop.should_sleep(69000, 102000));
    assert(adaptive_loop.should_sleep(300000, 350000));
  } else assert(!adaptive_loop.configure(250000));
  const unsigned previous = adaptive_loop.idle_yield_us;
  if (limit < 1000000) {
    assert(!adaptive_loop.configure(1000000));
    assert(adaptive_loop.idle_yield_us == previous);
  } else assert(adaptive_loop.configure(1000000));
  JsonDocument doc; adaptive_loop.status(doc.to<JsonObject>());
  assert(doc["sleep_us"] == 2000 && doc["yields"] == 1);
  assert(doc["busy_sleeps"] == 1 && doc["quiet_sleeps"] == 1);
  assert(doc["maximum_allowed_idle_yield_us"] == limit);
#ifdef CONFIG_ESP_TASK_WDT_TIMEOUT_S
  assert(doc["task_wdt_config_timeout_us"] == uint64_t(CONFIG_ESP_TASK_WDT_TIMEOUT_S) * 1000000);
#else
  assert(doc["task_wdt_config_timeout_us"].isNull());
  assert(limit == 50000);
#endif
}
'''


@unittest.skipUnless(CXX, "A C++ compiler is required")
class LoopOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dependency = ROOT / "firmware/.pio/libdeps/cyd_ili9341"
        if not (dependency / "JPEGDEC/src/JPEGDEC.cpp").is_file():
            raise unittest.SkipTest("Install pinned PlatformIO dependencies")
        cls.directory = tempfile.TemporaryDirectory(prefix="muse-loop-options-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        cls.directory_path, cls.dependency = directory, dependency
        (directory / "engine.hpp").write_text((ROOT / "firmware/include/engine.hpp").read_text())
        header = (ROOT / "firmware/include/new_jpeg.hpp").read_text()
        gate = ("#if defined(MVB_ENABLE_NEW_JPEG) && defined(ARDUINO_ARCH_ESP32) &&             "
                + chr(92) + "\n    defined(CONFIG_IDF_TARGET_ESP32)")
        header = replace_once(header, gate, "#if defined(MVB_ENABLE_NEW_JPEG)")
        header = replace_once(header, '"../vendor/esp_new_jpeg/include/esp_jpeg_dec.h"',
                              '"' + str(ROOT / "firmware/vendor/esp_new_jpeg/include/esp_jpeg_dec.h") + '"')
        (directory / "new_jpeg.hpp").write_text(header)
        for name in ("core", "profile"):
            adapter = (ROOT / f"firmware/native/{name}_tests.cpp").read_text()
            before, after = adapter.rsplit("}", 1)
            adapter = before + "return 0;\n}" + after
            if name == "profile":
                adapter = replace_once(adapter, "uint64_t time = 10000000;", "std::atomic<uint64_t> time{10000000};")
            (directory / f"{name}-adapters.hpp").write_text(adapter)
        (directory / "driver.cpp").write_text(SOURCE)
        cls.executables = {}
        for guard in (True, False):
            executable = directory / ("adaptive" if guard else "traditional")
            command = cls.base_command() + ["-pthread", "-DMVB_ENABLE_NEW_JPEG", "-DMVB_FRAME_PREFETCH",
                       "-DMVB_FRAME_RECORD_READ", "-DMVB_JPEG_FIRST_BLOCK_WARMUP", "-D__LINUX__", "-DNO_SIMD",
                       "-include", str(ROOT / "firmware/include/jpeg_portable.hpp"),
                       "-I", str(directory), "-I", str(ROOT / "firmware/include"),
                       "-I", str(dependency / "JPEGDEC/src"), str(directory / "driver.cpp"),
                       str(dependency / "JPEGDEC/src/JPEGDEC.cpp"), "-o", str(executable)]
            if guard:
                command += ["-DMVB_ADAPTIVE_LOOP_YIELD", "-DCONFIG_ESP_TASK_WDT_TIMEOUT_S=5"]
            cls.compile(command)
            cls.executables[guard] = executable

    @classmethod
    def base_command(cls):
        return [CXX, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-sign-compare", "-Wno-type-limits",
                "-I", str(cls.dependency / "ArduinoJson/src")]

    @classmethod
    def compile(cls, command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def execute(self, command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_runtime_commands_preserve_cached_pixels_and_compressed_prefetch(self):
        self.execute([str(self.executables[True]), "supported"])

    def test_default_host_adapter_and_guard_disabled_report_unsupported(self):
        self.execute([str(self.executables[True]), "unsupported"])
        self.execute([str(self.executables[False]), "supported"])

    def test_actual_policy_counts_delays_and_enforces_configured_watchdog_margin(self):
        source = self.directory_path / "policy.cpp"
        source.write_text(POLICY_SOURCE)
        for timeout in (5, 1, None):
            for fast in (False, True):
                executable = self.directory_path / f"policy-{timeout}-{fast}"
                command = self.base_command() + ["-DMVB_ADAPTIVE_LOOP_YIELD", str(source), "-o", str(executable)]
                if timeout is not None:
                    command.append(f"-DCONFIG_ESP_TASK_WDT_TIMEOUT_S={timeout}")
                if fast:
                    command.append("-DMVB_QUALIFIED_FAST_DEFAULTS")
                self.compile(command)
                self.execute([str(executable)])


if __name__ == "__main__":
    unittest.main()
