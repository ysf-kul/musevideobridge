"""Actual Engine/Video/prefetch startup ownership with synthetic read delays.

No device timing or FPS claims: the test clock and JPEG codec are substitutes.
"""
import configparser
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jpeg_warmup_test import CXX, ROOT, SOURCE as WARM_SOURCE, replace_once

SOURCE = WARM_SOURCE[:WARM_SOURCE.index("int main(int argc, char **argv)")]
SOURCE += r'''
std::vector<uint8_t> source_360() {
  std::vector<std::vector<uint8_t>> frames;
  for (unsigned n = 0; n < 360; ++n) frames.push_back(jpeg_with_comment(40 + n));
  auto bytes = container_bytes(frames); mvb::Rate rate{30, 1};
  mvb::put32(bytes.data() + 20, 30);
  mvb::put64(bytes.data() + 32, rate.pts(frames.size()));
  const size_t index = mvb::get64(bytes.data() + 48);
  for (size_t n = 0; n < frames.size(); ++n)
    mvb::put64(bytes.data() + index + n * 24 + 8, rate.pts(n));
  mvb::put32(bytes.data() + 72, mvb::crc32(bytes.data() + index, frames.size() * 24));
  mvb::put32(bytes.data() + 76, 0);
  mvb::put32(bytes.data() + 76, mvb::crc32(bytes.data(), 96));
  return bytes;
}
JsonDocument startup(Requests &r) { return r.command("{\"method\":\"get_startup_profile\"}"); }
void enable(Requests &r, bool enabled) {
  auto response = r.command(enabled
      ? "{\"method\":\"set_startup_options\",\"preload_first_frame\":true}"
      : "{\"method\":\"set_startup_options\",\"preload_first_frame\":false}");
#ifdef MVB_FIRST_FRAME_PRELOAD
  assert(response["ok"] == true && response["result"]["preload_first_frame"] == enabled);
#else
  assert(!enabled || response["error"]["code"] == "unsupported_configuration");
#endif
}
void setup(mvb::Engine &engine, Requests &r, bool use_worker = false) {
  ready(engine, r);
  assert(r.command("{\"method\":\"pause\"}")["ok"] == true);
  assert(r.command("{\"method\":\"set_decoder_options\",\"decoder\":\"new_jpeg\",\"max_mcus\":32,\"native_blocks\":true}")["ok"] == true);
  assert(r.command("{\"method\":\"set_fps\",\"numerator\":30,\"denominator\":1}")["ok"] == true);
  if (use_worker) {
    assert(r.command("{\"method\":\"set_prefetch_options\",\"enabled\":true}")["ok"] == true);
    assert(r.command("{\"method\":\"set_decoder_pipeline\",\"warmup\":true}")["ok"] == true);
  }
}
void completion(mvb::Engine &engine, Requests &r, Clock &clock) {
  for (unsigned n = 0; n < 40000; ++n) {
    engine.tick();
    auto status = r.command("{\"method\":\"get_status\"}");
    assert(status["result"]["state"] != "error");
    if (status["result"]["state"] == "ended") return;
    clock.advance(1000);
  }
  assert(false);
}
void cold(bool enabled, bool worker) {
  Clock clock; const auto bytes = source_360();
  Storage storage(clock, bytes); Runtime runtime(clock); OwnedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  setup(engine, r, worker); enable(r, enabled);
  first_read_delay = 90000; // Deliberate >2-frame artificial startup fault.
  const unsigned before_reads = first_reads, before_processes = processes, before_alloc = allocations;
  const uint64_t requested = clock.now();
  assert(r.command("{\"method\":\"restart\"}")["ok"] == true);
  const uint64_t replied = clock.now();
  auto prepared_document = startup(r);
  auto prepared = prepared_document["result"].as<JsonObject>();
  if (enabled) {
    assert(replied - requested >= first_read_delay);
    assert(prepared["verified"] == true && prepared["pending"] == true);
    assert(prepared["preload_us"].as<uint64_t>() >= first_read_delay);
    assert(prepared["preparation_us"].as<uint64_t>() >= prepared["preload_us"].as<uint64_t>());
    assert(first_reads == before_reads + 1 && processes == before_processes);
    assert(allocations == before_alloc); // Preloading allocates neither output nor frame storage.
    assert(prepared["record_stage_us"]["sd_read"].as<uint64_t>() >= first_read_delay);
  } else assert(first_reads == before_reads);
  // Queries preserve the verified first record and its generation.
  auto initial = warm_status(r);
  assert(initial["result"]["frames"] == 0);
  assert(measureJson(initial) < 2048);
  engine.tick();
  assert(first_reads == before_reads + 1); // Never read the preloaded record twice.
  if (enabled) {
    auto result = startup(r);
    assert(result["result"]["first_record_consumed"] == true && result["result"]["pending"] == false);
    auto stages = r.command("{\"method\":\"get_pipeline_profile\"}");
    assert(stages["result"]["average_us"]["frame_fetch_wait"].as<double>() < 100);
    assert(stages["result"]["average_us"]["sd_read"].as<double>() >= first_read_delay);
  }
  first_read_delay = 0;
  completion(engine, r, clock);
  auto benchmark = warm_status(r); auto status = r.command("{\"method\":\"get_status\"}");
  const uint64_t frames = benchmark["result"]["frames"], elapsed = benchmark["result"]["elapsed_us"];
  assert(status["result"]["frame_index"] == 359);
  if (enabled) {
    assert(frames == 360 && benchmark["result"]["dropped_frames"] == 0);
    const auto prep = benchmark["result"]["startup"];
    const uint64_t latency = prep["preparation_us"];
    const uint64_t inclusive = prep["elapsed_including_preparation_us"];
    assert(inclusive == elapsed + latency && latency >= 90000);
    const double expected = double(frames) * 1000000 / double(inclusive);
    assert(std::abs(prep["actual_fps_including_preparation"].as<double>() - expected) < 1e-5);
    assert(prep["actual_fps_including_preparation"].as<double>() < benchmark["result"]["actual_fps"].as<double>());
  } else assert(frames < 360 && benchmark["result"]["dropped_frames"].as<unsigned>() > 0);
  assert(storage.original_bytes() == bytes && !frame_open);
}
void starts() {
  Clock clock; Storage storage(clock, source_360()); Runtime runtime(clock);
  OwnedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  setup(engine, r); enable(r, true);
  for (const char *action : {"restart", "play", "resume"}) {
    assert(r.command("{\"method\":\"stop\"}")["ok"] == true);
    auto before = first_reads;
    std::string request = "{\"method\":\"" + std::string(action) + "\"}";
    assert(r.command(request.c_str())["ok"] == true);
    assert(startup(r)["result"]["pending"] == true && first_reads == before + 1);
    // Pause/resume before the first draw preserves the read and playhead.
    assert(r.command("{\"method\":\"pause\"}")["ok"] == true);
    assert(r.command("{\"method\":\"resume\"}")["ok"] == true);
    engine.tick(); assert(first_reads == before + 1);
    assert(startup(r)["result"]["first_record_consumed"] == true);
  }
  completion(engine, r, clock); const auto before = first_reads;
  assert(r.command("{\"method\":\"play\"}")["ok"] == true);
  assert(first_reads == before + 1 && startup(r)["result"]["pending"] == true);
  engine.tick(); assert(r.command("{\"method\":\"get_status\"}")["result"]["frame_index"] == 0);
  assert(r.command("{\"method\":\"pause\"}")["ok"] == true);
  const auto current_reads = first_reads;
  assert(r.command("{\"method\":\"resume\"}")["ok"] == true);
  assert(first_reads == current_reads); // Ordinary resume never restarts/preloads.
}
void auto_ready() {
  Clock clock; Storage storage(clock, source_360()); Runtime runtime(clock);
  OwnedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  enable(r, true); first_read_delay = 90000;
  ready(engine, r); first_read_delay = 0;
  auto profile = startup(r);
  assert(profile["result"]["trigger"] == "auto_ready");
  assert(profile["result"]["verified"] == true && profile["result"]["first_record_consumed"] == true);
  assert(warm_status(r)["result"]["frames"] == 1);
  assert(r.command("{\"method\":\"get_status\"}")["result"]["frame_index"] == 0);
}
void startup_invalidations() {
  for (const char *command : {
      "{\"method\":\"stop\"}", "{\"method\":\"seek\",\"position_us\":500000}",
      "{\"method\":\"set_position\",\"x\":30,\"y\":30}",
      "{\"method\":\"set_fps\",\"numerator\":7,\"denominator\":1}",
      "{\"method\":\"probe_pipeline\",\"stage\":\"sd\",\"repetitions\":1}",
      "{\"method\":\"probe_pipeline\",\"stage\":\"decode\",\"repetitions\":1}",
      "{\"method\":\"set_prefetch_options\",\"enabled\":false}",
      "{\"method\":\"select_video\",\"sha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}"}) {
    Clock clock; Storage storage(clock, source_360(), true); Runtime runtime(clock);
    OwnedPainter painter(clock); Output output;
    mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
    setup(engine, r); enable(r, true);
    assert(r.command("{\"method\":\"restart\"}")["ok"] == true);
    assert(startup(r)["result"]["pending"] == true);
    assert(r.command(command)["ok"] == true);
    assert(startup(r)["result"]["pending"] == false && !borrowed);
  }
}
void failures() {
  for (bool corrupt : {false, true}) {
    Clock clock; const auto bytes = source_360(); Storage storage(clock, bytes);
    Runtime runtime(clock); OwnedPainter painter(clock); Output output;
    mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
    setup(engine, r); enable(r, true);
    short_first_read = !corrupt; corrupt_first_read = corrupt;
    const unsigned before = processes;
    auto response = r.command("{\"method\":\"restart\"}");
    short_first_read = corrupt_first_read = false;
    assert(response["ok"] == false);
    assert(response["error"]["code"] == (corrupt ? "crc_mismatch" : "io_error"));
    auto profile = startup(r);
    assert(profile["result"]["attempted"] == true && profile["result"]["verified"] == false);
    assert(profile["result"]["pending"] == false && processes == before && !borrowed);
    assert(warm_status(r)["result"]["frames"] == 0);
    assert(r.command("{\"method\":\"get_status\"}")["result"]["state"] == "error");
    assert(storage.original_bytes() == bytes);
  }
}
void toggles() {
  Clock clock; Storage storage(clock, source_360()); Runtime runtime(clock);
  OwnedPainter painter(clock); Output output;
  mvb::Engine engine(storage, painter, runtime, output); engine.begin(); Requests r(engine, output);
  auto initial = startup(r);
  assert(initial["result"]["enabled"] == false);
  for (const char *bad : {"0", "1", "-1", "null", "\"true\""}) {
    std::string cmd = "{\"method\":\"set_startup_options\",\"preload_first_frame\":" + std::string(bad) + "}";
    assert(r.command(cmd.c_str())["error"]["code"] == "invalid_parameter");
  }
  enable(r, true); enable(r, false);
#ifndef MVB_FIRST_FRAME_PRELOAD
  assert(initial["result"]["available"] == false);
  setup(engine, r); const auto before = first_reads;
  assert(r.command("{\"method\":\"restart\"}")["ok"] == true);
  assert(first_reads == before);
  engine.tick(); assert(first_reads == before + 1);
  assert(warm_status(r)["result"]["startup"].isNull());
#endif
}
int main(int argc, char **argv) {
  assert(argc == 2); std::string test = argv[1];
  if (test == "cold") cold(true, false);
  else if (test == "cold_worker") cold(true, true);
  else if (test == "off") cold(false, false);
  else if (test == "starts") starts();
  else if (test == "auto") auto_ready();
  else if (test == "invalidations") startup_invalidations();
  else if (test == "failures") failures();
  else if (test == "toggles") toggles();
  else return 2;
}
'''


class PreloadEnvironmentTests(unittest.TestCase):
    def test_experiment_is_separate_and_starts_disabled(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "firmware/platformio.ini")
        self.assertEqual(config["env:cyd_jpeg_dma_direct_preload"]["extends"], "env:cyd_jpeg_dma_direct")
        self.assertIn("-DMVB_FIRST_FRAME_PRELOAD", config["env:cyd_jpeg_dma_direct_preload"]["build_flags"])
        self.assertNotIn("MVB_FIRST_FRAME_PRELOAD", config["env:cyd_optimized"]["build_flags"])
        self.assertIn("first_frame_preload_selected_ = false;", (ROOT / "firmware/include/engine.hpp").read_text())


@unittest.skipUnless(CXX, "A C++ compiler is required")
class FirstFramePreloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dependency = ROOT / "firmware/.pio/libdeps/cyd_ili9341"
        if not (dependency / "JPEGDEC/src/JPEGDEC.cpp").is_file():
            raise unittest.SkipTest("Install pinned PlatformIO dependencies")
        cls.directory = tempfile.TemporaryDirectory(prefix="muse-first-frame-preload-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
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
                adapter = replace_once(adapter, "size_t read(uint8_t *data, size_t count) override {", r'''
size_t read(uint8_t *data, size_t count) override {
  const uintptr_t active = reinterpret_cast<uintptr_t>(borrowed.load());
  const uintptr_t dest = reinterpret_cast<uintptr_t>(data);
  assert(!active || dest + count <= active || dest >= active + borrowed_size.load());
  bool first_record = offset_ == 96 && count > 16;
  if (first_record) {
    ++first_reads; clock_.advance(first_read_delay); profile_.read_us += first_read_delay;
    if (short_first_read) return 0;
  }
''')
                adapter = replace_once(adapter, "clock_.advance(17); profile_.read_us += 17;", "if (first_record && corrupt_first_read && n > 20) data[19] ^= 1;\n    clock_.advance(17); profile_.read_us += 17;")
                adapter = r'''
extern std::atomic<const uint8_t *> borrowed;
extern std::atomic<size_t> borrowed_size;
unsigned first_reads = 0;
uint64_t first_read_delay = 0;
bool short_first_read = false, corrupt_first_read = false;
''' + adapter
            (directory / f"{name}-adapters.hpp").write_text(adapter)
        (directory / "driver.cpp").write_text(SOURCE)
        cls.executables = {}
        for guard in (True, False):
            executable = directory / ("preload" if guard else "ordinary")
            command = [CXX, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-pthread",
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-DMVB_ENABLE_NEW_JPEG",
                       "-DMVB_FRAME_PREFETCH", "-DMVB_FRAME_RECORD_READ", "-DMVB_JPEG_FIRST_BLOCK_WARMUP",
                       "-D__LINUX__", "-DNO_SIMD", "-include", str(ROOT / "firmware/include/jpeg_portable.hpp"),
                       "-I", str(directory), "-I", str(ROOT / "firmware/include"),
                       "-I", str(dependency / "ArduinoJson/src"), "-I", str(dependency / "JPEGDEC/src"),
                       str(directory / "driver.cpp"), str(dependency / "JPEGDEC/src/JPEGDEC.cpp"),
                       "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-sign-compare", "-Wno-type-limits",
                       "-o", str(executable)]
            if guard:
                command.append("-DMVB_FIRST_FRAME_PRELOAD")
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.executables[guard] = executable

    def scenario(self, test, guard=True):
        result = subprocess.run([str(self.executables[guard]), test], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_read_precedes_clock_and_360_frames_are_preserved_with_inclusive_rate(self):
        self.scenario("cold")

    def test_initial_result_hands_off_to_existing_prefetch_and_warmup_without_overwriting_input(self):
        self.scenario("cold_worker")

    def test_unselected_path_keeps_read_inside_first_playback_service(self):
        self.scenario("off")

    def test_restart_stopped_ended_and_pause_resume_keep_correct_first_record_ownership(self):
        self.scenario("starts")

    def test_selection_before_auto_ready_performs_work_before_media_clock(self):
        self.scenario("auto")

    def test_probes_controls_seek_select_and_prefetch_changes_invalidate_pending_record(self):
        self.scenario("invalidations")

    def test_corrupt_or_short_first_read_rejects_start_without_presenting_a_frame(self):
        self.scenario("failures")

    def test_strict_options_start_disabled_and_guard_off_preserves_existing_behavior(self):
        self.scenario("toggles")
        self.scenario("toggles", guard=False)


if __name__ == "__main__":
    unittest.main()
