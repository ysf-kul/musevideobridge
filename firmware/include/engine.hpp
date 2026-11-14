#pragma once
#include "frame_prefetch.hpp"
#include "new_jpeg.hpp"
#include "playlist.hpp"
#include "queue_record.hpp"
#include "render.hpp"
#include "rom_jpeg.hpp"
#include "timing.hpp"
#include "tjpgd_fast.hpp"
#include <ArduinoJson.h>
#include <JPEGDEC.h>
#include <cmath>
#include <cstdio>
#include <memory>
#include <new>

namespace mvb {
class Storage {
public:
  virtual ~Storage() = default;
  virtual bool mount() = 0;
  virtual bool mounted() = 0;
  virtual uint64_t capacity() = 0;
  virtual uint64_t free_bytes() = 0;
  virtual File *read_file(const char *) = 0;
  virtual File *write_file(const char *, bool append) = 0;
  virtual bool exists(const char *) = 0;
  virtual bool rename_new(const char *, const char *) = 0;
  virtual bool remove_owned(const char *) = 0;
  virtual bool mkdir_library() = 0;
  virtual bool first_video(char *out, size_t capacity, const char *ignored) = 0;
  virtual bool checkpoint_file(const char *, const uint8_t *, size_t,
                               bool append) = 0;
  virtual bool select(const char *digest) = 0;
  virtual bool queue_store_available() const { return false; }
  virtual bool read_queue(unsigned, uint8_t *, size_t) { return false; }
  virtual bool write_queue(unsigned, const uint8_t *, size_t) { return false; }
  virtual size_t video_ids(char (*)[65], size_t) { return 0; }
  virtual bool configure_clock(uint32_t) { return false; }
};
class Runtime : public ProfileClock {
public:
  virtual ~Runtime() = default;
  virtual uint64_t now() = 0;
  virtual void telemetry(JsonObject) = 0;
  virtual void serial_diagnostics(JsonObject r) { r["available"] = false; }
  virtual void display_info(JsonObject r) { r["available"] = false; }
  virtual void recovery_info(JsonObject r) { r["available"] = false; }
  virtual uint32_t configured_baud() const { return 115200; }
  virtual bool display_probe(JsonObject, unsigned) { return false; }
  virtual bool configure_loop_options(uint32_t) { return false; }
  virtual void loop_diagnostics(JsonObject r) { r["available"] = false; }
  virtual uint32_t configured_idle_yield_us() const { return 0; }
  virtual bool hash_begin() = 0;
  virtual bool hash_update(const uint8_t *, size_t) = 0;
  virtual bool hash_finish(char out[65]) = 0;
  virtual uint32_t session_nonce() = 0;
};
class Output {
public:
  virtual ~Output() = default;
  virtual bool send(const Frame &) = 0;
};
inline bool digest_valid(const char *s) {
  if (!s || std::strlen(s) != 64)
    return false;
  for (size_t i = 0; i < 64; i++)
    if (!(s[i] >= '0' && s[i] <= '9') && !(s[i] >= 'a' && s[i] <= 'f'))
      return false;
  return true;
}
inline bool selection_valid(const JsonDocument &d) {
  const char *hash = d["sha256"].as<const char *>();
  const char *name = d["filename"].as<const char *>();
  if (!d.is<JsonObjectConst>() || d.size() != 3 || d["schema_version"] != 1 ||
      !digest_valid(hash) || d["sha256"].as<JsonString>().size() != 64 ||
      !name || d["filename"].as<JsonString>().size() != 68)
    return false;
  char expected[69];
  std::snprintf(expected, sizeof(expected), "%s.mvb", hash);
  return !std::strcmp(expected, name);
}
inline bool utf8(const uint8_t *s, size_t n) {
  for (size_t i = 0; i < n;) {
    uint32_t b = s[i++], code = b;
    size_t more = 0;
    if (b < 128)
      continue;
    else if (b >= 0xc2 && b <= 0xdf) {
      more = 1;
      code = b & 31;
    } else if (b >= 0xe0 && b <= 0xef) {
      more = 2;
      code = b & 15;
    } else if (b >= 0xf0 && b <= 0xf4) {
      more = 3;
      code = b & 7;
    } else
      return false;
    if (i + more > n)
      return false;
    for (size_t k = 0; k < more; k++) {
      if ((s[i] & 0xc0) != 0x80)
        return false;
      code = (code << 6) | (s[i++] & 63);
    }
    if ((more == 2 && code < 0x800) || (more == 3 && code < 0x10000) ||
        code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff))
      return false;
  }
  return true;
}
class Engine {
  Storage &storage_;
  Painter &painter_;
  Runtime &runtime_;
  Output &output_;
  TileRenderer renderer_;
  JPEGDEC jpeg_;
  enum class Decoder { Jpegdec, Rom, TjpgdFast, NewJpeg };
  Decoder decoder_ = Decoder::Jpegdec;
  bool target_fps_explicit_ = false;
  const char *default_display_fallback_ = nullptr,
             *default_decoder_fallback_ = nullptr,
             *default_prefetch_fallback_ = nullptr;
  std::unique_ptr<RomJpeg> rom_jpeg_;
  std::unique_ptr<TjpgdFast> tjpgd_fast_;
  std::unique_ptr<NewJpeg> new_jpeg_;
#ifdef MVB_ALIGN_COMPRESSED_RECORDS
  alignas(16)
#endif
  uint8_t jpeg_bytes_[MaxJpeg + Video::RecordHeaderBytes + 3]{};
  uint8_t *jpeg_payload_ = jpeg_bytes_;
  Video video_;
  FramePrefetch prefetch_;
  FramePrefetch::Result prefetched_;
  bool prefetched_valid_ = false, current_record_valid_ = false;
  uint8_t *current_record_ = jpeg_bytes_;
  size_t current_record_capacity_ = sizeof(jpeg_bytes_), current_jpeg_length_ = 0;
  uint32_t retained_frame_ = 0;
  uint64_t prefetch_requests_ = 0, prefetch_hits_ = 0, prefetch_misses_ = 0;
  uint64_t prefetch_errors_ = 0, prefetch_join_us_ = 0;
  uint64_t benchmark_fetch_wait_us_ = 0, prefetch_background_us_ = 0;
  uint32_t prefetch_stack_free_min_ = FramePrefetch::StackBytes;
#ifdef MVB_FIRST_FRAME_PRELOAD
  bool first_frame_preload_selected_ = false;
  struct StartupPreparation {
    bool attempted = false, prepared = false, consumed = false;
    uint32_t generation = 0;
    uint64_t latency_us = 0, preload_us = 0;
    size_t jpeg_bytes = 0;
    const char *trigger = nullptr, *error = nullptr;
    FrameProfile profile;
  } startup_;
#endif
  bool first_block_warmup_selected_ = false, render_started_ = false;
  struct WarmFrame {
    bool valid = false, big_endian = false;
    uint32_t index = 0, generation = 0, revision = 0;
    const uint8_t *payload = nullptr;
    size_t length = 0;
    Transform geometry;
    uint64_t open_us = 0, decode_us = 0;
  } warm_frame_;
  uint64_t warmup_attempts_ = 0, warmup_hits_ = 0, warmup_misses_ = 0,
           warmup_aborts_ = 0, warmup_errors_ = 0, warmup_work_us_ = 0;
  uint64_t codec_open_baseline_ = 0;
  File *file_ = nullptr;
  Timeline timeline_;
  const char *state_ = "idle";
  const char *error_ = nullptr;
  char current_[96]{}, blocked_[96]{}, digest_[65]{};
  char client_nonce_[80]{};
  uint32_t high_sequence_ = 0, error_count_ = 0, context_sequence_ = 0,
           context_crc_ = 0;
  bool upload_owned_ = false, fps_available_ = false;
  uint32_t session_ = 0, revision_ = 0, generation_ = 0;
  uint64_t last_contact_ = 0, last_poll_ = 0, last_telemetry_ = 0,
           last_frame_event_ = 0;
  uint32_t current_frame_ = 0;
  bool has_frame_ = false, redraw_ = false, draw_enabled_ = true,
       connected_ = false, draw_failed_ = false;
  uint64_t draw_us_ = 0, read_us_ = 0, decode_us_ = 0, read_bytes_ = 0;
  uint64_t color_us_ = 0, benchmark_color_us_ = 0;
  uint64_t adapter_copy_us_ = 0, benchmark_adapter_copy_us_ = 0;
  uint64_t fps_start_ = 0;
  uint32_t fps_count_ = 0;
  double actual_fps_ = 0;
  unsigned decoder_mcus_ = 32;
  uint64_t benchmark_started_ = 0, benchmark_finished_ = 0;
  uint64_t benchmark_frames_ = 0, benchmark_read_ = 0, benchmark_decode_ = 0,
           benchmark_draw_ = 0;
  FrameProfile frame_profile_, pipeline_profile_;
  uint64_t benchmark_telemetry_us_ = 0;
  uint32_t telemetry_ms_ = 1000, frame_ms_ = 100;
  bool waiting_dirty_ = true;
  enum class Work {
    None,
    LoadHash,
    LoadIndex,
    ResumeHash,
    UploadHash,
    UploadIndex,
    UploadFrames,
    ExistingHash
  };
  Work work_ = Work::None;
  uint64_t work_offset_ = 0;
  uint32_t verify_frame_ = 0;
  struct Receipt {
    uint32_t sequence = 0, crc = 0;
    uint16_t length = 0;
    uint8_t data[512]{};
  } receipts_[8];
  size_t receipt_next_ = 0;
  const char *upload_state_ = "none";
  const char *upload_error_ = nullptr;
  const char *checkpoint_error_ = "sd_checkpoint_failed";
  uint32_t transfer_id_ = 0, accepted_ = 0, checkpointed_ = 0,
           prefix_crc_ = 0xffffffffu;
  uint64_t upload_size_ = 0, verified_bytes_ = 0;
  uint32_t chunk_sequence_ = 0, chunk_crc_ = 0;
  uint8_t chunk_ack_[16]{};
  char upload_digest_[65]{}, partial_[96]{}, journal_[96]{};
  File *writer_ = nullptr;
  bool finalizing_ = false;
  Playlist playlist_;
  bool queue_initialized_ = false, load_autoplay_ = true;
  uint32_t queue_generation_ = 0;
  const char *queue_error_ = nullptr;
  bool save_queue(const Playlist &queue, int selected_position = -2) {
    if (!storage_.queue_store_available()) return true;
    if (queue_generation_ == UINT32_MAX) { queue_error_ = "queue_save_failed"; return false; }
    // Bounded temporary storage at queue changes only. FAT calls share the
    // Arduino task stack with command parsing; leave that stack room for the SDK.
    std::unique_ptr<uint8_t[]> record(new (std::nothrow) uint8_t[QueueRecordBytes]);
    if (!record) { queue_error_ = "queue_save_failed"; return false; }
    uint8_t *bytes = record.get();
    encode_queue(bytes, queue, queue_generation_ + 1);
    if (selected_position != -2) {
      // Stage a position change without copying the 800-byte playlist onto the
      // caller's stack. Commit the in-memory position only after the SD write.
      put32(bytes + 16, uint32_t(selected_position));
      put32(bytes + 804, crc32(bytes, 804));
    }
    if (!storage_.write_queue((queue_generation_ + 1) % 2, bytes, QueueRecordBytes)) {
      queue_error_ = "queue_save_failed"; return false;
    }
    ++queue_generation_; queue_error_ = nullptr; return true;
  }
  void restore_queue() {
    if (!storage_.mounted()) return;
    uint8_t bytes[QueueRecordBytes];
    for (unsigned slot = 0; slot < 2; ++slot) {
      Playlist candidate; uint32_t generation = 0;
      if (storage_.read_queue(slot, bytes, sizeof(bytes)) && decode_queue(bytes, candidate, generation) && generation > queue_generation_) {
        playlist_ = candidate; queue_generation_ = generation;
      }
    }
    if (queue_generation_) {
      // Preserve a deliberate empty queue. Missing media is skipped by navigation.
      queue_initialized_ = true;
      return;
    }
    char ids[Playlist::Capacity][65]{};
    size_t count = storage_.video_ids(ids, Playlist::Capacity);
    // Keep the legacy active selection in a full discovered library. Otherwise
    // loading it could leave the playhead outside the twelve discovered IDs.
    char active[96];
    if (storage_.first_video(active, sizeof(active), "")) {
      const char *name = std::strrchr(active, '/'); name = name ? name + 1 : active;
      if (std::strlen(name) == 68 && !std::strcmp(name + 64, ".mvb")) {
        char id[65]; std::memcpy(id, name, 64); id[64] = 0;
        if (digest_valid(id)) playlist_.append(id);
      }
    }
    for (size_t i = 0; i < count; ++i) if (digest_valid(ids[i])) playlist_.append(ids[i]);
  }
  bool controls_visible_ = false, controls_dirty_ = false;
  uint64_t controls_until_ = 0, last_state_event_ = 0, last_preparation_draw_ = 0;
  uint32_t sent_revision_ = UINT32_MAX, sent_queue_revision_ = UINT32_MAX;
  const char *drawn_stage_ = nullptr;
  int drawn_percent_ = -2;

  const char *preparation_stage() const {
    if (work_ == Work::ResumeHash) return "resuming_upload";
    if (!std::strcmp(upload_state_, "receiving")) return "uploading";
    if (work_ == Work::UploadHash || work_ == Work::ExistingHash)
      return "verifying_upload";
    if (work_ == Work::UploadIndex || work_ == Work::UploadFrames)
      return "validating_video";
    if (work_ == Work::LoadHash) return "loading_video";
    if (work_ == Work::LoadIndex) return "preparing_playback";
    if (!std::strcmp(state_, "error") || !std::strcmp(upload_state_, "error"))
      return "error";
    return file_ && video_.ready ? "ready" : "waiting";
  }
  int preparation_percent() const {
    uint64_t done = 0, total = 0;
    if (!std::strcmp(upload_state_, "receiving")) {
      done = accepted_; total = upload_size_;
    } else if (work_ == Work::ResumeHash) {
      done = work_offset_; total = checkpointed_;
    } else if (work_ == Work::LoadHash || work_ == Work::UploadHash ||
               work_ == Work::ExistingHash) {
      done = work_offset_; total = file_ ? file_->size() : 0;
    }
    return total ? int(std::min(uint64_t(100), done * 100 / total)) : -1;
  }
  void preparation_status(JsonObject r) {
    r["stage"] = preparation_stage();
    int percent = preparation_percent();
    if (percent >= 0) r["percent"] = percent;
    else r["percent"] = nullptr;
    r["transfer_id"] = transfer_id_;
    r["video_id"] = transfer_active() ? upload_digest_ : digest_;
  }
  int adjacent_position(int direction) {
    int position = playlist_.position;
    if (position < 0) return -1;
    for (size_t i = 0; i < playlist_.count; ++i) {
      position += direction;
      if (position < 0 || position >= int(playlist_.count)) {
        if (playlist_.mode != LoopMode::Queue) return -1;
        position = direction > 0 ? 0 : int(playlist_.count) - 1;
      }
      char path[96];
      std::snprintf(path, sizeof(path), "/muse-video/%s.mvb", playlist_.ids[position]);
      if (storage_.exists(path)) return position;
    }
    return -1;
  }
  void queue_status(JsonObject r, bool order) {
    r["queue_persistent"] = storage_.queue_store_available();
    r["queue_error"] = queue_error_;
    r["queue_revision"] = playlist_.revision;
    r["queue_position"] = playlist_.position;
    r["loop_mode"] = loop_name(playlist_.mode);
    const int current = playlist_.position;
    const int previous = adjacent_position(-1), next = adjacent_position(1);
    if (current >= 0) r["current_video_id"] = playlist_.ids[current];
    else r["current_video_id"] = nullptr;
    if (previous >= 0) r["previous_video_id"] = playlist_.ids[previous];
    else r["previous_video_id"] = nullptr;
    if (next >= 0) r["next_video_id"] = playlist_.ids[next];
    else r["next_video_id"] = nullptr;
    if (order) {
      auto ids = r["queue"].to<JsonArray>();
      for (size_t i = 0; i < playlist_.count; ++i) ids.add(playlist_.ids[i]);
    }
  }
  // Serialization occurs on state changes/queries, never on frame completion.
  [[gnu::noinline]] void playback_snapshot(JsonObject r, bool order = true) {
    queue_status(r, order);
    r["state"] = state_;
    r["state_revision"] = revision_;
    r["playhead_generation"] = generation_;
    r["content_sha256"] = digest_[0] ? digest_ : nullptr;
    if (has_frame_) {
      r["frame_index"] = current_frame_;
      r["frame_pts_us"] = video_.header.fps.pts(current_frame_);
    } else {
      r["frame_index"] = nullptr; r["frame_pts_us"] = nullptr;
    }
    r["position_us"] = timeline_.position(runtime_.now());
    preparation_status(r["preparation"].to<JsonObject>());
  }
  bool select_queue(int position, bool autoplay, bool persist = true) {
    if (position < 0 || position >= int(playlist_.count) || transfer_active())
      return false;
    char path[96];
    std::snprintf(path, sizeof(path), "/muse-video/%s.mvb", playlist_.ids[position]);
    if (!storage_.exists(path)) return false;
    if (persist && (position != playlist_.position || !queue_generation_) &&
        !save_queue(playlist_, position)) return false;
    playlist_.position = position;
    load_autoplay_ = autoplay;
    return begin_load(path);
  }
  bool adjacent_video(int direction, bool autoplay) {
    // State snapshots and controls use the same available-file search.
    return select_queue(adjacent_position(direction), autoplay);
  }
  bool playback_action(const char *method) {
    if (!video_.ready || work_ != Work::None || !file_ || transfer_active())
      return false;
    invalidate_warmup();
    uint64_t now = runtime_.now();
    if (!std::strcmp(method, "pause")) {
      benchmark_finished_ = now;
      timeline_.pause(now);
      state_ = "paused";
    } else if (!std::strcmp(method, "stop")) {
      prefetched_valid_ = current_record_valid_ = false;
      timeline_.stop(); state_ = "stopped"; has_frame_ = false;
      ++generation_;
      painter_.clear({0, 0, 320, 240});
    } else {
      if (!std::strcmp(method, "restart") || !std::strcmp(state_, "ended") ||
          !std::strcmp(state_, "stopped")) {
        prefetched_valid_ = current_record_valid_ = false;
        timeline_.stop(); ++generation_;
        // Startup diagnostics retain this pointer beyond the command's stack.
        if (!prepare_first_record(!std::strcmp(method, "restart") ? "restart" : "play_from_end_or_stop", now)) return false;
        now = runtime_.now();
      }
#ifdef MVB_FIRST_FRAME_PRELOAD
      else if (!startup_record_pending()) startup_ = {};
#endif
      timeline_.start(now); state_ = "playing";
      fps_start_ = now; fps_count_ = 0; actual_fps_ = 0; fps_available_ = false;
      reset_benchmark();
    }
    controls_dirty_ = controls_visible_;
    return true;
  }

  bool transfer_active() const {
    return !std::strcmp(upload_state_, "receiving") ||
           !std::strcmp(upload_state_, "resuming") ||
           !std::strcmp(upload_state_, "verifying");
  }
  const char *decoder_name() const {
    switch (decoder_) {
    case Decoder::Rom:
      return "rom";
    case Decoder::TjpgdFast:
      return "tjpgd_fast";
    case Decoder::NewJpeg:
      return "new_jpeg";
    default:
      return "jpegdec";
    }
  }
  void qualified_defaults_status(JsonObject r) {
#ifdef MVB_QUALIFIED_DEFAULTS
    r["enabled"] = true;
    r["target_fps_source"] = target_fps_explicit_ ? "explicit_control" : "encoded_video";
#else
    r["enabled"] = false;
    r["target_fps_source"] = "legacy_or_explicit_control";
#endif
    r["decoder"] = decoder_name();
    r["prefetch_enabled"] = prefetch_.enabled();
    r["warmup_selected"] = first_block_warmup_selected_;
    r["warmup_blocks"] = new_jpeg_ ? new_jpeg_->warmup_blocks() : 1;
    r["display_fallback"] = default_display_fallback_;
    RenderOptions display;
    const bool display_known = painter_.configured_render_options(display);
    r["display_options_available"] = display_known;
    if (display_known) {
      r["display_mode"] = display.mode == RenderMode::Dma ? "dma" :
          display.mode == RenderMode::Legacy ? "legacy" : "blocks";
      r["display_big_endian"] = display.big_endian;
      r["display_zero_copy"] = display.zero_copy;
      r["display_spi_hz"] = display.spi_hz;
      r["display_command_spi_hz"] = display.command_spi_hz;
    }
    const uint32_t idle_us = runtime_.configured_idle_yield_us();
    if (idle_us)
      r["idle_yield_us"] = idle_us;
    else
      r["idle_yield_us"] = nullptr;
    r["decoder_fallback"] = default_decoder_fallback_;
    r["prefetch_fallback"] = default_prefetch_fallback_;
  }
  size_t decoder_owned_bytes() const {
    if (rom_jpeg_)
      return sizeof(RomJpeg);
    if (tjpgd_fast_)
      return sizeof(TjpgdFast);
    if (new_jpeg_)
      return sizeof(NewJpeg) + new_jpeg_->output_capacity() +
             new_jpeg_->warmup_cache_capacity();
    return 0;
  }
  bool warmup_eligible() const {
    const auto &t = renderer_.transform;
    return NewJpeg::first_block_warmup_available() &&
           first_block_warmup_selected_ && decoder_ == Decoder::NewJpeg &&
           new_jpeg_ && prefetch_.enabled() && renderer_.native_blocks &&
           t.rotation == 0 && t.width == renderer_.source_width &&
           t.height == renderer_.source_height && t.x >= 0 && t.y >= 0 &&
           t.x + t.width <= 320 && t.y + t.height <= 240;
  }
  void invalidate_warmup() {
    if (new_jpeg_ && new_jpeg_->discard_warmup())
      ++warmup_aborts_;
    warm_frame_ = {};
  }
  bool warmup_matches(uint32_t index, size_t length) const {
    const auto &a = warm_frame_.geometry;
    const auto &b = renderer_.transform;
    return warm_frame_.valid && warmup_eligible() &&
           warm_frame_.index == index && warm_frame_.generation == generation_ &&
           warm_frame_.revision == revision_ &&
           warm_frame_.payload == jpeg_payload_ && warm_frame_.length == length &&
           warm_frame_.big_endian == painter_.big_endian() && a.x == b.x &&
           a.y == b.y && a.width == b.width && a.height == b.height &&
           a.rotation == b.rotation && a.lock_aspect == b.lock_aspect;
  }
  void warmup_status(JsonObject r) {
    r["available"] = NewJpeg::first_block_warmup_available();
    r["selected"] = first_block_warmup_selected_;
    r["active"] = warmup_eligible();
    r["cached"] = warm_frame_.valid;
    r["blocks"] = new_jpeg_ ? new_jpeg_->warmup_blocks() : 1;
    r["cached_blocks"] = new_jpeg_ ? new_jpeg_->cached_blocks() : 0;
    r["cache_owned_bytes"] = new_jpeg_ ? new_jpeg_->warmup_cache_capacity() : 0;
    r["attempts"] = warmup_attempts_;
    r["hits"] = warmup_hits_;
    r["misses"] = warmup_misses_;
    r["aborts"] = warmup_aborts_;
    r["errors"] = warmup_errors_;
    r["work_us_nested"] = warmup_work_us_;
    r["timing_scope"] = "lookahead_work_nonadditive_credited_to_consuming_decode";
    r["presentation_completion"] = "synchronous_end_frame";
    r["codec_open_calls"] = new_jpeg_
                                ? new_jpeg_->handle_open_calls() - codec_open_baseline_
                                : 0;
  }
  void reset_benchmark() {
    benchmark_started_ = runtime_.now();
    benchmark_finished_ = benchmark_started_;
    benchmark_frames_ = benchmark_read_ = benchmark_decode_ = benchmark_draw_ =
        0;
    pipeline_profile_ = {};
    benchmark_telemetry_us_ = 0;
    benchmark_color_us_ = 0;
    benchmark_adapter_copy_us_ = 0;
    prefetch_requests_ = prefetch_hits_ = prefetch_misses_ = prefetch_errors_ = 0;
    prefetch_join_us_ = prefetch_background_us_ = benchmark_fetch_wait_us_ = 0;
    warmup_attempts_ = warmup_hits_ = warmup_misses_ = warmup_aborts_ =
        warmup_errors_ = warmup_work_us_ = 0;
    codec_open_baseline_ = new_jpeg_ ? new_jpeg_->handle_open_calls() : 0;
  }
  void prefetch_status(JsonObject r) {
    r["available"] = FramePrefetch::available();
    r["enabled"] = prefetch_.enabled();
    r["slot_bytes"] = prefetch_.capacity();
    r["worker_stack_bytes"] = prefetch_.enabled() ? FramePrefetch::StackBytes : 0;
    r["worker_stack_free_min_bytes"] =
        prefetch_.enabled() ? prefetch_stack_free_min_ : 0;
    r["requests"] = prefetch_requests_;
    r["hits"] = prefetch_hits_;
    r["misses"] = prefetch_misses_;
    r["errors"] = prefetch_errors_;
    r["join_wait_us"] = prefetch_join_us_;
    r["background_fetch_us"] = prefetch_background_us_;
    r["ownership"] = "joined_before_command_tick_storage_boundaries";
  }
  bool startup_record_pending() const {
#ifdef MVB_FIRST_FRAME_PRELOAD
    return startup_.prepared && !startup_.consumed &&
           startup_.generation == generation_ && prefetched_valid_ &&
           prefetched_.ok && prefetched_.index == 0 &&
           prefetched_.record == jpeg_bytes_;
#else
    return false;
#endif
  }
  void startup_status(JsonObject r, bool detailed, uint64_t playback_us = 0) {
#ifdef MVB_FIRST_FRAME_PRELOAD
    r["available"] = true;
    r["enabled"] = first_frame_preload_selected_;
    r["preparation_us"] = startup_.latency_us;
    r["preload_us"] = startup_.preload_us;
    r["first_record_consumed"] = startup_.consumed;
    r["elapsed_including_preparation_us"] = playback_us + startup_.latency_us;
    if (benchmark_frames_ && playback_us + startup_.latency_us)
      r["actual_fps_including_preparation"] =
          double(benchmark_frames_) * 1000000 /
          double(playback_us + startup_.latency_us);
    else
      r["actual_fps_including_preparation"] = nullptr;
    if (!detailed)
      return;
    r["attempted"] = startup_.attempted;
    r["verified"] = startup_.prepared;
    r["pending"] = startup_record_pending();
    r["trigger"] = startup_.trigger;
    r["error"] = startup_.error;
    r["frame_index"] = startup_.attempted ? 0 : -1;
    r["playhead_generation"] = startup_.generation;
    r["jpeg_bytes"] = startup_.jpeg_bytes;
    r["preparation_scope"] =
        "ready_or_restart_handler_before_media_anchor_excludes_prior_hash_index_and_UART_queue";
    auto stage = r["record_stage_us"].to<JsonObject>();
    stage["io_available"] = startup_.profile.io_available;
    stage["sd_read"] = startup_.profile.sd_read_us;
    stage["sd_seek"] = startup_.profile.sd_seek_us;
    stage["crc"] = startup_.profile.crc_us;
    stage["jpeg_validation"] = startup_.profile.validate_us;
    stage["container_prepare"] = startup_.profile.prepare_us;
    r["timing_scope"] = "startup_record_stages_nested_in_preload_not_playback_service";
#else
    (void)detailed;
    (void)playback_us;
    r["available"] = false;
    r["enabled"] = false;
#endif
  }
  void pipeline_status(JsonObject r) {
    r["frames"] = benchmark_frames_;
    r["io_available"] = pipeline_profile_.io_available;
    r["display_available"] = pipeline_profile_.display_available;
    r["timing_scope"] = "software_wall_time_us";
    auto averages = r["average_us"].to<JsonObject>();
    auto average = [&](const char *key, uint64_t value, bool available = true) {
      if (benchmark_frames_ && available)
        averages[key] = double(value) / benchmark_frames_;
      else
        averages[key] = nullptr;
    };
    average("sd_read", pipeline_profile_.sd_read_us,
            pipeline_profile_.io_available);
    average("sd_seek", pipeline_profile_.sd_seek_us,
            pipeline_profile_.io_available);
    average("crc", pipeline_profile_.crc_us);
    average("jpeg_validation", pipeline_profile_.validate_us);
    average("container_prepare", pipeline_profile_.prepare_us,
            pipeline_profile_.io_available);
    average("jpeg_open", pipeline_profile_.jpeg_open_us);
    average("jpeg_decode", pipeline_profile_.jpeg_decode_us);
    average("display", pipeline_profile_.display_us,
            pipeline_profile_.display_available);
    average("transform", pipeline_profile_.transform_us,
            pipeline_profile_.display_available);
    average("frame", pipeline_profile_.frame_us);
    average("frame_fetch_wait", benchmark_fetch_wait_us_);
    r["sd_fetch_overlapped"] = prefetch_.enabled();
    prefetch_status(r["prefetch"].to<JsonObject>());
    warmup_status(r["decoder_pipeline"].to<JsonObject>());
    // Copies and DMA waits are nested in display, not additional frame stages.
    average("display_copy_nested", pipeline_profile_.copy_us,
            pipeline_profile_.display_available);
    average("dma_wait_nested", pipeline_profile_.wait_us,
            pipeline_profile_.display_available);
    if (pipeline_profile_.display_available) {
      r["pixels"] = pipeline_profile_.pixels;
      r["address_windows"] = pipeline_profile_.windows;
      r["pixel_pushes"] = pipeline_profile_.pushes;
    } else {
      r["pixels"] = nullptr;
      r["address_windows"] = nullptr;
      r["pixel_pushes"] = nullptr;
    }
    r["telemetry_us"] = benchmark_telemetry_us_;
    r["color_conversion"] = decoder_ == Decoder::Rom
                                ? "rgb888_to_rgb565_nested_in_decode"
                                : "fused_with_jpeg_decode";
    average("color_conversion_nested", benchmark_color_us_,
            decoder_ == Decoder::Rom);
    average("decoder_strip_copy_nested", benchmark_adapter_copy_us_,
            decoder_ == Decoder::TjpgdFast || decoder_ == Decoder::NewJpeg);
    r["per_frame_application_allocations"] = 0;
    r["allocation_scope"] =
        "application_buffers_excludes_configuration_and_private_codec_opens";
    r["sd_timing_scope"] = "stdio_calls_including_driver_and_buffer_copies";
  }
  void benchmark_status(JsonObject r) {
    r["decoder"] = decoder_name();
    r["decoder_owned_bytes"] = decoder_owned_bytes();
    if (decoder_ == Decoder::NewJpeg)
      r["decoder_private_memory_bytes"] = nullptr;
    else
      r["decoder_private_memory_bytes"] = 0;
#ifdef MVB_FRAME_RECORD_READ
    r["frame_read_mode"] = "coalesced_record";
#else
    r["frame_read_mode"] = "separate_header_payload_padding";
#endif
    r["decoder_max_mcus"] = decoder_mcus_;
    r["decoder_max_mcus_applies"] = decoder_ == Decoder::Jpegdec;
    r["native_blocks"] = renderer_.native_blocks;
#ifdef MVB_JPEG_DMA_DIRECT_OUTPUT
    auto alignment = r["input_alignment"].to<JsonObject>();
    alignment["fixed_record_mod16"] = reinterpret_cast<uintptr_t>(jpeg_bytes_) & 15;
    alignment["current_record_mod16"] = reinterpret_cast<uintptr_t>(current_record_) & 15;
    alignment["current_payload_mod16"] = reinterpret_cast<uintptr_t>(jpeg_payload_) & 15;
    if (prefetch_.buffer())
      alignment["prefetch_record_mod16"] = reinterpret_cast<uintptr_t>(prefetch_.buffer()) & 15;
    else
      alignment["prefetch_record_mod16"] = nullptr;
#endif
    r["content_sha256"] = digest_;
    r["frames"] = benchmark_frames_;
    uint64_t elapsed =
        (timeline_.running() ? runtime_.now() : benchmark_finished_) -
        benchmark_started_;
    r["elapsed_us"] = elapsed;
    if (benchmark_frames_ && elapsed) {
      r["actual_fps"] = double(benchmark_frames_) * 1000000 / double(elapsed);
      r["average_read_us"] =
          double(benchmark_read_) / double(benchmark_frames_);
      r["average_decode_us"] =
          double(benchmark_decode_) / double(benchmark_frames_);
      r["average_draw_us"] =
          double(benchmark_draw_) / double(benchmark_frames_);
    } else {
      r["actual_fps"] = nullptr;
      r["average_read_us"] = nullptr;
      r["average_decode_us"] = nullptr;
      r["average_draw_us"] = nullptr;
    }
    r["dropped_frames"] = timeline_.dropped;
    r["rate_skipped_frames"] = timeline_.rate_skipped;
    r["target_fps"]["numerator"] = timeline_.target.numerator;
    r["target_fps"]["denominator"] = timeline_.target.denominator;
#ifdef MVB_FIRST_FRAME_PRELOAD
    startup_status(r["startup"].to<JsonObject>(), false, elapsed);
#endif
    prefetch_status(r["prefetch"].to<JsonObject>());
    warmup_status(r["decoder_pipeline"].to<JsonObject>());
  }

  static int draw(JPEGDRAW *tile) {
    auto *self = static_cast<Engine *>(tile->pUser);
    if (!self || !self->draw_enabled_)
      return 1;
    uint64_t start = self->runtime_.now();
    bool ok =
        self->renderer_.tile(tile->x, tile->y, tile->iWidth, tile->iWidthUsed,
                             tile->iHeight, tile->pPixels);
    self->draw_us_ += self->runtime_.now() - start;
    if (!ok)
      self->draw_failed_ = true;
    return ok ? 1 : 0;
  }
  void begin_render_frame() {
    render_started_ = true;
    const auto &t = renderer_.transform;
    if (renderer_.native_blocks && t.rotation == 0 &&
        t.width == renderer_.source_width &&
        t.height == renderer_.source_height)
      { auto region = clipped(t);
        region.h = std::max(0, std::min(region.h, renderer_.viewport_height - region.y));
        painter_.begin_image(region); }
    else
      painter_.begin_frame();
  }
  void finish_render_frame() {
    if (!render_started_)
      return;
    const uint64_t at = runtime_.now();
    if (!painter_.end_frame())
      draw_failed_ = true;
    draw_us_ += runtime_.now() - at;
    render_started_ = false;
  }
  template <class Backend>
  bool decode_backend(Backend *decoder, size_t length, bool draw_image,
                      bool defer_end = false, bool already_prepared = false,
                      uint64_t prior_open = 0, uint64_t prior_decode = 0) {
    uint64_t start = runtime_.now();
    if (!decoder ||
        (!already_prepared &&
         !decoder->prepare(jpeg_payload_, length, renderer_, runtime_,
                           draw_image, painter_.big_endian())))
      return false;
    frame_profile_.jpeg_open_us = runtime_.now() - start + prior_open;
    if (draw_image) {
      uint64_t at = runtime_.now();
      begin_render_frame();
      draw_us_ += runtime_.now() - at;
    }
    bool ok = decoder->decode();
    draw_us_ += decoder->draw_us();
    color_us_ = decoder->color_us();
    draw_failed_ = decoder->draw_failed();
    if (draw_image && !defer_end)
      finish_render_frame();
    decode_us_ = runtime_.now() - start - draw_us_ + prior_open + prior_decode;
    frame_profile_.jpeg_decode_us =
        decode_us_ >= frame_profile_.jpeg_open_us
            ? decode_us_ - frame_profile_.jpeg_open_us
            : 0;
    return ok && !draw_failed_;
  }
  bool decode_jpeg(size_t length, bool draw_image, bool defer_end = false,
                   bool already_prepared = false, uint64_t prior_open = 0,
                   uint64_t prior_decode = 0) {
    draw_enabled_ = draw_image;
    draw_failed_ = false;
    draw_us_ = color_us_ = adapter_copy_us_ = 0;
    render_started_ = false;
    if (decoder_ == Decoder::Rom)
      return decode_backend(rom_jpeg_.get(), length, draw_image);
    if (decoder_ == Decoder::TjpgdFast) {
      bool ok = decode_backend(tjpgd_fast_.get(), length, draw_image);
      if (tjpgd_fast_)
        adapter_copy_us_ = tjpgd_fast_->copy_us();
      return ok;
    }
    if (decoder_ == Decoder::NewJpeg) {
      bool ok = decode_backend(new_jpeg_.get(), length, draw_image, defer_end,
                               already_prepared, prior_open, prior_decode);
      if (new_jpeg_)
        adapter_copy_us_ = new_jpeg_->copy_us();
      return ok;
    }
    uint64_t start = runtime_.now();
    if (!jpeg_.openRAM(jpeg_payload_, int(length), draw))
      return false;
    jpeg_.setUserPointer(this);
    jpeg_.setPixelType(painter_.big_endian() ? RGB565_BIG_ENDIAN
                                             : RGB565_LITTLE_ENDIAN);
    jpeg_.setMaxOutputSize(int(decoder_mcus_));
    frame_profile_.jpeg_open_us = runtime_.now() - start;
    if (draw_image) {
      uint64_t at = runtime_.now();
      begin_render_frame();
      draw_us_ += runtime_.now() - at;
    }
    int ok = jpeg_.decode(0, 0, 0);
    if (draw_image)
      finish_render_frame();
    jpeg_.close();
    decode_us_ = runtime_.now() - start - draw_us_;
    frame_profile_.jpeg_decode_us =
        decode_us_ >= frame_profile_.jpeg_open_us
            ? decode_us_ - frame_profile_.jpeg_open_us
            : 0;
    // JPEGDEC treats callback cancellation as successful decoding. Track
    // display failures separately so partial or failed DMA presentations cannot
    // count.
    return ok != 0 && !draw_failed_;
  }
  void close_file() {
    // Every normal render already joins. This also safely handles cleanup if a
    // caller is added later: task deletion cannot cancel an active card read.
    invalidate_warmup();
    prefetch_.release();
    prefetched_valid_ = current_record_valid_ = false;
    current_record_ = jpeg_bytes_;
    current_record_capacity_ = sizeof(jpeg_bytes_);
    if (file_) {
      file_->close();
      file_ = nullptr;
    }
  }
  void reset_qualified_media_decoder() {
#ifdef MVB_QUALIFIED_DEFAULTS
    // New media, uploads and validation start from the universally supported
    // decoder. Automatic selection runs again after verified load metadata.
    first_block_warmup_selected_ = false;
    decoder_ = Decoder::Jpegdec;
    rom_jpeg_.reset();
    tjpgd_fast_.reset();
    new_jpeg_.reset();
    codec_open_baseline_ = 0;
#endif
  }
  void failure(const char *code) {
    timeline_.pause(runtime_.now());
    state_ = "error";
    error_ = code ? code : "io_error";
    work_ = Work::None;
    close_file();
    std::snprintf(blocked_, sizeof(blocked_), "%s", current_);
    has_frame_ = false;
    waiting_dirty_ = true;
    ++revision_;
    event_error(error_);
  }
  void upload_failure(const char *code) {
    upload_state_ = "error";
    upload_error_ = code;
    work_ = Work::None;
    finalizing_ = false;
    close_file();
    if (writer_) {
      writer_->close();
      writer_ = nullptr;
    }
    waiting_dirty_ = true;
    ++revision_;
    event_error(code);
  }
  void json_frame(uint8_t kind, uint32_t sequence, JsonDocument &doc,
                  bool cache = false, uint32_t request_crc = 0) {
    Frame f;
    f.kind = kind;
    f.session = session_;
    f.sequence = sequence;
    size_t length = measureJson(doc);
    if (length > MaxPayload) {
      if (kind != 2) {
        error_count_++;
        return;
      }
      doc.clear();
      doc["ok"] = false;
      doc["error"]["code"] = "response_too_large";
      doc["error"]["details"] = "Response exceeds the bounded protocol payload";
      length = measureJson(doc);
    }
    f.length = uint16_t(
        serializeJson(doc, reinterpret_cast<char *>(f.payload), MaxPayload));
    if (cache) {
      Receipt &r = receipts_[receipt_next_++ % 8];
      r.sequence = sequence;
      r.crc = request_crc;
      r.length = f.length <= 512 ? f.length : 0;
      if (r.length)
        std::memcpy(r.data, f.payload, r.length);
    }
    output_.send(f);
  }
  void reject(uint32_t sequence, const char *code,
              const char *details = "Invalid or unavailable command") {
    JsonDocument d;
    d["ok"] = false;
    d["error"]["code"] = code;
    d["error"]["details"] = details;
    json_frame(2, sequence, d, sequence && sequence == context_sequence_,
               context_crc_);
  }
  void event_error(const char *code) {
    JsonDocument d;
    d["event"] = "error";
    d["code"] = code;
    if (has_frame_)
      d["frame_index"] = current_frame_;
    else
      d["frame_index"] = nullptr;
    error_count_++;
    d["uptime_us"] = runtime_.now();
    json_frame(3, 0, d);
  }
  void status(JsonObject r) {
    r["queue_persistent"] = storage_.queue_store_available();
    r["queue_error"] = queue_error_;
    r["state"] = state_;
    // Order is sent separately so full hardware telemetry stays bounded.
    r["queue_revision"] = playlist_.revision;
    preparation_status(r["preparation"].to<JsonObject>());
    if (work_ == Work::LoadHash) {
      r["validation_bytes"] = work_offset_;
      r["validation_total_bytes"] = file_ ? file_->size() : 0;
    }
    r["connected"] = connected_;
    r["sd_mounted"] = storage_.mounted();
    r["state_revision"] = revision_;
    r["playhead_generation"] = generation_;
    if (has_frame_) {
      r["frame_index"] = current_frame_;
      r["frame_pts_us"] = video_.header.fps.pts(current_frame_);
    } else {
      r["frame_index"] = nullptr;
      r["frame_pts_us"] = nullptr;
    }
    if (digest_[0])
      r["content_sha256"] = digest_;
    else
      r["content_sha256"] = nullptr;
    r["position_us"] = timeline_.position(runtime_.now());
    r["target_fps"]["numerator"] = timeline_.target.numerator;
    r["target_fps"]["denominator"] = timeline_.target.denominator;
    if (video_.ready) {
      r["encoded_fps"]["numerator"] = video_.header.fps.numerator;
      r["encoded_fps"]["denominator"] = video_.header.fps.denominator;
      r["frame_count"] = video_.header.frames;
    }
    if (timeline_.running() && !fps_available_)
      r["actual_fps"] = nullptr;
    else
      r["actual_fps"] = timeline_.running() ? actual_fps_ : 0.0;
    r["actual_fps_source"] = "completed_frame_draws";
    r["dropped_frames"] = timeline_.dropped;
    r["rate_skipped_frames"] = timeline_.rate_skipped;
    r["late_frames"] = timeline_.late;
    auto t = r["transform"].to<JsonObject>();
    const auto &v = renderer_.transform;
    t["x"] = v.x;
    t["y"] = v.y;
    t["width"] = v.width;
    t["height"] = v.height;
    t["rotation_deg"] = v.rotation;
    t["lock_aspect"] = v.lock_aspect;
    r["read_us"] = read_us_;
    r["decode_us"] = decode_us_;
    r["draw_us"] = draw_us_;
    r["error_count"] = error_count_;
    r["sd_read_bytes"] = read_bytes_;
    if (read_us_)
      r["sd_read_bytes_per_second"] =
          double(read_bytes_) * 1000000 / double(read_us_);
    else
      r["sd_read_bytes_per_second"] = nullptr;
    if (error_)
      r["error"] = error_;
    else
      r["error"] = nullptr;
  }
  void upload_status(JsonObject r) {
    r["transfer_id"] = transfer_id_;
    r["state"] = upload_state_;
    r["accepted_offset"] = accepted_;
    r["checkpointed_offset"] = checkpointed_;
    r["verified_bytes"] = verified_bytes_;
    r["size"] = upload_size_;
    r["sha256"] = upload_digest_;
    r["chunk_bytes"] = 1024;
    preparation_status(r["preparation"].to<JsonObject>());
    if (upload_error_)
      r["error"] = upload_error_;
    else
      r["error"] = nullptr;
  }
  bool begin_load(const char *path) {
    close_file();
    reset_qualified_media_decoder();
    file_ = storage_.read_file(path);
    if (!file_) {
      failure("sd_open_failed");
      return false;
    }
    std::snprintf(current_, sizeof(current_), "%s", path);
    digest_[0] = 0;
    has_frame_ = false;
    error_ = nullptr;
    state_ = "loading";
    timeline_.stop();
    generation_++;
    revision_++;
    controls_visible_ = false;
    renderer_.viewport_height = 240;
    waiting_dirty_ = true;
    if (file_->size() > 2147483647 || !runtime_.hash_begin()) {
      failure("invalid_container");
      return false;
    }
    work_offset_ = 0;
    work_ = Work::LoadHash;
    return true;
  }
  void ready_playback() {
#ifdef MVB_FIRST_FRAME_PRELOAD
    const uint64_t preparation_started = runtime_.now();
#endif
    renderer_.source_width = video_.header.width;
    renderer_.source_height = video_.header.height;
    renderer_.transform = {int((320 - video_.header.width) / 2),
                           int((240 - video_.header.height) / 2),
                           int(video_.header.width),
                           int(video_.header.height),
                           0,
                           true};
    timeline_.encoded = video_.header.fps;
#ifdef MVB_QUALIFIED_DEFAULTS
    if (!target_fps_explicit_)
      timeline_.target = timeline_.encoded;
    if (!apply_qualified_video_defaults())
      return;
#endif
    timeline_.count = video_.header.frames;
    timeline_.stop();
#ifdef MVB_FIRST_FRAME_PRELOAD
    if (first_frame_preload_selected_) {
      painter_.clear({0, 0, 320, 240});
      if (!prepare_first_record("auto_ready", preparation_started))
        return;
    } else
      startup_ = {};
#endif
    if (load_autoplay_) timeline_.start(runtime_.now());
    reset_benchmark();
    state_ = load_autoplay_ ? "playing" : "paused";
    work_ = Work::None;
    playlist_.append(digest_);
    playlist_.position = playlist_.find(digest_);
    queue_initialized_ = true;
    if (!load_autoplay_) redraw_ = true;
#ifdef MVB_FIRST_FRAME_PRELOAD
    if (!first_frame_preload_selected_)
#endif
#ifdef MVB_QUALIFIED_FAST_DEFAULTS
      clear_difference(painter_, {0, 0, 320, 240}, clipped(renderer_.transform));
#else
      painter_.clear({0, 0, 320, 240});
#endif
    fps_start_ = runtime_.now();
    fps_count_ = 0;
    actual_fps_ = 0;
    fps_available_ = false;
    revision_++;
    if (!load_autoplay_) render_frame(0, false);
  }
  bool apply_qualified_video_defaults() {
    default_decoder_fallback_ = default_prefetch_fallback_ = nullptr;
    renderer_.native_blocks = true;
    decoder_ = Decoder::Jpegdec;
    first_block_warmup_selected_ = false;
    if (video_.header.width % 8 || video_.header.height % 8)
      default_decoder_fallback_ = "new_jpeg_requires_multiple_of_eight";
    else if (!NewJpeg::available())
      default_decoder_fallback_ = "new_jpeg_unavailable";
    else {
      std::unique_ptr<NewJpeg> candidate(new (std::nothrow) NewJpeg);
      if (!candidate)
        default_decoder_fallback_ = "new_jpeg_object_allocation_failed";
      else {
        size_t length = 0;
        if (!fetch_frame(0, length)) {
          failure(video_.error);
          return false;
        }
        if (!candidate->prepare(jpeg_payload_, length, renderer_, runtime_,
                                 false, painter_.big_endian()
#ifdef MVB_QUALIFIED_FAST_DEFAULTS
                                 , painter_.output_leases_available()
#endif
                                 ))
          default_decoder_fallback_ = "new_jpeg_prepare_failed";
        else {
          // Configuration preflight does not own compressed scratch during
          // later reads. Retain reusable storage, close partial codec state.
          candidate->discard_warmup();
          new_jpeg_ = std::move(candidate);
          decoder_ = Decoder::NewJpeg;
        }
      }
    }
    if (!FramePrefetch::available())
      default_prefetch_fallback_ = "prefetch_unavailable";
    else if (!prefetch_.configure(video_.header.max_jpeg +
                                   Video::RecordHeaderBytes + 3))
      default_prefetch_fallback_ = "prefetch_memory_or_worker_unavailable";
    // Allocation failure retains checked synchronous JPEGDEC/NewJPEG playback.
    // Warmup remains one block and never allocates its optional second cache.
    if (decoder_ == Decoder::NewJpeg && prefetch_.enabled() &&
        NewJpeg::first_block_warmup_available())
      first_block_warmup_selected_ = true;
    return true;
  }
  bool checkpoint(bool first = false) {
    checkpoint_error_ = "sd_flush_failed";
    if (!writer_ || !writer_->flush())
      return false;
    if (!first && accepted_ == checkpointed_)
      return true;
    uint8_t meta[96]{};
    std::memcpy(meta, "MVBUP1\0\0", 8);
    std::memcpy(meta + 8, upload_digest_, 64);
    put64(meta + 72, upload_size_);
    put32(meta + 80, accepted_);
    put32(meta + 84, prefix_crc_ ^ 0xffffffffu);
    put32(meta + 88, crc32(meta, 88));
    checkpoint_error_ = "sd_checkpoint_failed";
    if (!journal_[0] || !storage_.checkpoint_file(journal_, meta, 96, !first))
      return false;
    checkpointed_ = accepted_;
    return true;
  }
  bool begin_upload(JsonObjectConst cmd) {
    upload_error_ = nullptr;
    auto fail = [&](const char *code) {
      upload_error_ = code;
      return false;
    };
    const char *hash = cmd["sha256"].as<const char *>();
    if (!digest_valid(hash) || cmd["sha256"].as<JsonString>().size() != 64 ||
        !cmd["size"].is<uint64_t>() || cmd["size"].as<uint64_t>() < 136 ||
        cmd["size"].as<uint64_t>() > 2147483647 ||
        cmd["container_version"] != 1)
      return fail("invalid_parameter");
    if (playlist_.count == Playlist::Capacity && playlist_.find(hash) < 0)
      return fail("queue_full");
    if (std::strcmp(upload_state_, "none") &&
        std::strcmp(upload_state_, "cancelled") &&
        std::strcmp(upload_state_, "error") &&
        std::strcmp(upload_state_, "complete"))
      return fail("busy");
    if (!storage_.mounted())
      return fail("sd_unavailable");
    if (!storage_.mkdir_library())
      return fail("sd_directory_failed");
    timeline_.pause(runtime_.now());
    close_file();
    reset_qualified_media_decoder();
    state_ = "idle";
    has_frame_ = false;
    digest_[0] = 0;
    waiting_dirty_ = true;
    revision_++;
    controls_visible_ = false;
    renderer_.viewport_height = 240;
    // Recovery belongs to one identified digest. Never acquire ownership of a
    // foreign legacy journal when beginning a different video.
    upload_owned_ = false;
    std::snprintf(upload_digest_, 65, "%s", hash);
    upload_size_ = cmd["size"].as<uint64_t>();
    transfer_id_ = runtime_.session_nonce();
    if (!transfer_id_)
      transfer_id_ = 1;
    std::snprintf(partial_, sizeof(partial_), "/muse-video/.upload-%s.part",
                  hash);
    std::snprintf(journal_, sizeof(journal_), "/muse-video/.upload-%s.meta",
                  hash);
    upload_error_ = nullptr;
    verified_bytes_ = 0;
    accepted_ = checkpointed_ = 0;
    prefix_crc_ = 0xffffffffu;
    uint8_t b[96]{};
    bool recovery = storage_.exists(journal_);
    bool legacy = !recovery && storage_.exists("/muse-video/.upload.meta");
    if (recovery || legacy) {
      File *meta =
          storage_.read_file(legacy ? "/muse-video/.upload.meta" : journal_);
      if (!meta)
        return fail("sd_open_failed");
      bool valid = meta->size() >= 96 && meta->size() % 96 == 0 &&
                   meta->size() <= 3200000 &&
                   meta->seek(uint32_t(meta->size() - 96)) &&
                   exact(*meta, b, 96);
      meta->close();
      char recovery_digest[65]{};
      std::memcpy(recovery_digest, b + 8, 64);
      if (!valid || std::memcmp(b, "MVBUP1\0\0", 8) ||
          crc32(b, 88) != get32(b + 88) || get32(b + 92) != 0 ||
          !digest_valid(recovery_digest) || get64(b + 72) < 136 ||
          get64(b + 72) > 2147483647 || get32(b + 80) > get64(b + 72))
        return fail("upload_journal_invalid");
      if (legacy && std::strcmp(recovery_digest, hash)) {
        // A valid, identified legacy transfer may coexist with new uploads.
        // Its journal and partial remain unchanged and available to resume.
        recovery = false;
      } else if (std::strcmp(recovery_digest, hash) ||
                 get64(b + 72) != upload_size_)
        return fail("upload_conflict");
      else {
        recovery = true;
        if (legacy)
          std::snprintf(journal_, sizeof(journal_), "/muse-video/.upload.meta");
      }
    }
    char existing[96];
    std::snprintf(existing, sizeof(existing), "/muse-video/%s.mvb", hash);
    if (storage_.exists(existing)) {
      file_ = storage_.read_file(existing);
      if (!file_ || file_->size() != upload_size_ || !runtime_.hash_begin())
        return fail("digest_mismatch");
      // A matching SD file is verified and loaded, rather than retransmitted.
      // The existing journal was validated above before cleanup ownership.
      upload_owned_ = recovery;
      accepted_ = checkpointed_ = uint32_t(upload_size_);
      upload_state_ = "verifying"; work_offset_ = 0; work_ = Work::ExistingHash;
      return true;
    }
    if (recovery) {
      upload_owned_ = true;
      checkpointed_ = get32(b + 80);
      resume_expected_crc_ = get32(b + 84);
      file_ = storage_.read_file(partial_);
      if (!file_ || file_->size() < checkpointed_ ||
          file_->size() > upload_size_ || !file_->seek(0))
        return fail("partial_unavailable");
      uint64_t remaining = upload_size_ - file_->size();
      if (storage_.free_bytes() <
          remaining + 65536 +
              ((upload_size_ - checkpointed_ + 65535) / 65536 + 2) * 96)
        return fail("insufficient_space");
      upload_state_ = "resuming";
      work_offset_ = 0;
      work_ = Work::ResumeHash;
      return true;
    }
    if (storage_.exists(partial_))
      return fail("upload_conflict");
    if (storage_.free_bytes() <
        upload_size_ + 65536 + ((upload_size_ + 65535) / 65536 + 2) * 96)
      return fail("insufficient_space");
    writer_ = storage_.write_file(partial_, false);
    if (!writer_)
      return fail("sd_open_failed");
    if (!checkpoint(true))
      return fail(checkpoint_error_);
    upload_owned_ = true;
    upload_state_ = "receiving";
    work_ = Work::None;
    return true;
  }
  uint32_t resume_expected_crc_ = 0;
  void chunk(const Frame &f) {
    const uint32_t request_crc = crc32(f.payload, f.length);
    if (!f.sequence) {
      reject(f.sequence, "invalid_parameter", "Chunk sequence must be nonzero");
      return;
    }
    if (f.sequence == chunk_sequence_) {
      if (request_crc != chunk_crc_) {
        reject(f.sequence, "sequence_conflict", "Conflicting chunk retry");
        return;
      }
      Frame cached;
      cached.kind = 17;
      cached.session = session_;
      cached.sequence = f.sequence;
      cached.length = 16;
      std::memcpy(cached.payload, chunk_ack_, 16);
      output_.send(cached);
      return;
    }
    if (f.sequence <= high_sequence_) {
      reject(f.sequence, "stale_sequence", "Expired chunk sequence");
      return;
    }
    high_sequence_ = f.sequence;
    Frame ack;
    ack.kind = 17;
    ack.session = session_;
    ack.sequence = f.sequence;
    ack.length = 16;
    uint16_t result = 2;
    if (f.length >= 12 && get32(f.payload) == transfer_id_ &&
        get16(f.payload + 10) == 0 &&
        size_t(get16(f.payload + 8)) + 12 == f.length &&
        get16(f.payload + 8) > 0 && get16(f.payload + 8) <= 1024 &&
        !std::strcmp(upload_state_, "receiving")) {
      uint32_t offset = get32(f.payload + 4), n = get16(f.payload + 8);
      if (uint64_t(offset) + n <= upload_size_ && offset == accepted_ &&
          writer_) {
        if (writer_->write(f.payload + 12, n) == n) {
          prefix_crc_ = crc_update(prefix_crc_, f.payload + 12, n);
          accepted_ += n;
          result = 0;
          if (accepted_ - checkpointed_ >= 65536 && !checkpoint()) {
            result = 3;
            upload_failure(checkpoint_error_);
          }
        } else {
          result = 3;
          upload_failure("sd_write_failed");
        }
      } else if (uint64_t(offset) + n <= accepted_ && writer_) {
        uint8_t check[1024];
        // FATFS snapshots the size at open. Flush before opening a new reader.
        const char *error = nullptr;
        File *reader = nullptr;
        if (!writer_->flush())
          error = "sd_flush_failed";
        else {
          reader = storage_.read_file(partial_);
          if (!reader)
            error = "sd_open_failed";
          else if (!reader->seek(offset))
            error = "seek_failed";
          else if (!exact(*reader, check, n))
            error = "sd_read_failed";
        }
        if (reader)
          reader->close();
        if (error) {
          result = 3;
          upload_failure(error);
        } else
          result = !std::memcmp(check, f.payload + 12, n) ? 1 : 2;
      }
    }
    put32(ack.payload, transfer_id_);
    put32(ack.payload + 4, accepted_);
    put32(ack.payload + 8, checkpointed_);
    chunk_sequence_ = f.sequence;
    chunk_crc_ = request_crc;
    // Keep the exact receipt so an ACK lost after an SD write never repeats it.
    // Offset-based duplicate checking remains available with a new sequence.
    put16(ack.payload + 12, result);
    std::memcpy(chunk_ack_, ack.payload, 16);
    output_.send(ack);
  }
  void work_step() {
    // Hashing runs after close_file has joined prefetch and invalidated compressed
    // records. Reuse that owned slot instead of keeping 4 KiB on the loop stack
    // while publication calls the FAT queue writer. No playback buffer is added.
    uint8_t *buffer = jpeg_bytes_;
    constexpr size_t HashBlockBytes = 4096;
    static_assert(sizeof(jpeg_bytes_) >= HashBlockBytes, "Hash scratch capacity");
    if (work_ == Work::LoadHash || work_ == Work::UploadHash ||
        work_ == Work::ExistingHash) {
      uint64_t size = file_ ? file_->size() : 0;
      if (!file_) {
        if (work_ == Work::LoadHash)
          failure("sd_open_failed");
        else
          upload_failure("sd_open_failed");
        return;
      }
      if (work_offset_ < size) {
        size_t n =
            size_t(std::min(uint64_t(HashBlockBytes), size - work_offset_));
        if (!exact(*file_, buffer, n)) {
          if (work_ == Work::LoadHash)
            failure("sd_read_failed");
          else
            upload_failure("sd_read_failed");
          return;
        }
        if (!runtime_.hash_update(buffer, n)) {
          if (work_ == Work::LoadHash)
            failure("hash_failed");
          else
            upload_failure("hash_failed");
          return;
        }
        work_offset_ += n;
        if (work_ != Work::LoadHash)
          verified_bytes_ = work_offset_;
        return;
      }
      char hash[65];
      if (!runtime_.hash_finish(hash)) {
        if (work_ == Work::LoadHash)
          failure("hash_failed");
        else
          upload_failure("hash_failed");
        return;
      }
      if (work_ == Work::ExistingHash) {
        close_file();
        if (std::strcmp(hash, upload_digest_)) {
          upload_failure("digest_mismatch");
          return;
        }
        publish_upload(true);
        return;
      }
      if (work_ == Work::UploadHash &&
          (std::strcmp(hash, upload_digest_) || size != upload_size_)) {
        upload_failure("digest_mismatch");
        return;
      }
      if (work_ == Work::LoadHash) {
        char expected[65]{};
        if (std::strlen(current_) == 80 && !std::strncmp(current_, "/muse-video/", 12)) {
          std::memcpy(expected, current_ + 12, 64);
          if (digest_valid(expected) && std::strcmp(hash, expected)) {
            failure("digest_mismatch"); return;
          }
        }
        std::snprintf(digest_, 65, "%s", hash);
      }
      if (!video_.begin(file_)) {
        if (work_ == Work::LoadHash)
          failure(video_.error);
        else
          upload_failure(video_.error);
        return;
      }
      work_ = work_ == Work::LoadHash ? Work::LoadIndex : Work::UploadIndex;
      return;
    }
    if (work_ == Work::ResumeHash) {
      if (work_offset_ < checkpointed_) {
        size_t n = size_t(std::min(uint64_t(HashBlockBytes),
                                   uint64_t(checkpointed_) - work_offset_));
        if (!file_ || !exact(*file_, buffer, n)) {
          upload_failure("sd_read_failed");
          return;
        }
        prefix_crc_ = crc_update(prefix_crc_, buffer, n);
        work_offset_ += n;
        verified_bytes_ = work_offset_;
        return;
      }
      close_file();
      if ((prefix_crc_ ^ 0xffffffffu) != resume_expected_crc_) {
        upload_failure("crc_mismatch");
        return;
      }
      writer_ = storage_.write_file(partial_, true);
      if (!writer_) {
        upload_failure("sd_open_failed");
        return;
      }
      if (!writer_->seek(checkpointed_)) {
        upload_failure("seek_failed");
        return;
      }
      accepted_ = checkpointed_;
      upload_state_ = "receiving";
      work_ = Work::None;
      return;
    }
    if (work_ == Work::LoadIndex || work_ == Work::UploadIndex) {
      if (!video_.scan_step()) {
        if (work_ == Work::LoadIndex)
          failure(video_.error);
        else
          upload_failure(video_.error);
        return;
      }
      if (video_.ready) {
        if (work_ == Work::LoadIndex)
          ready_playback();
        else {
          verify_frame_ = 0;
          work_ = Work::UploadFrames;
        }
      }
      return;
    }
    if (work_ == Work::UploadFrames) {
      size_t length = 0;
      if (!fetch_frame(verify_frame_, length) || !decode_jpeg(length, false)) {
        upload_failure(video_.error ? video_.error : "unsupported_jpeg");
        return;
      }
      if (++verify_frame_ == video_.header.frames) {
        close_file();
        char final[96];
        std::snprintf(final, sizeof(final), "/muse-video/%s.mvb",
                      upload_digest_);
        if (storage_.exists(final)) {
          file_ = storage_.read_file(final);
          if (!file_) {
            upload_failure("sd_open_failed");
            return;
          }
          if (!runtime_.hash_begin()) {
            upload_failure("hash_failed");
            return;
          }
          work_offset_ = 0;
          work_ = Work::ExistingHash;
        } else
          publish_upload(false);
      }
    }
  }
  void publish_upload(bool existing) {
    char final[96];
    std::snprintf(final, sizeof(final), "/muse-video/%s.mvb", upload_digest_);
    if (!existing && !storage_.rename_new(partial_, final)) {
      upload_failure("sd_publish_failed");
      return;
    }
    if (!storage_.select(upload_digest_)) {
      upload_failure("selection_failed");
      return;
    }
    upload_state_ = "complete";
    upload_error_ = nullptr;
    verified_bytes_ = upload_size_;
    work_ = Work::None;
    finalizing_ = false;
    // Only app-owned journal is removed; unrelated media and old selections are
    // retained.
    if (upload_owned_ && existing && !storage_.remove_owned(partial_))
      upload_error_ = "checkpoint_cleanup_failed";
    if (upload_owned_ && !storage_.remove_owned(journal_)) {
      upload_error_ = "checkpoint_cleanup_failed";
    }
    playlist_.append(upload_digest_);
    playlist_.position = playlist_.find(upload_digest_);
    if (!save_queue(playlist_)) { failure("queue_save_failed"); return; }
    load_autoplay_ = true;
    begin_load(final);
  }
  bool fetch_frame(uint32_t index, size_t &length,
                   ProfileClock *clock = nullptr,
                   FrameProfile *profile = nullptr) {
    invalidate_warmup();
    prefetched_valid_ = current_record_valid_ = false;
    current_record_ = jpeg_bytes_;
    current_record_capacity_ = sizeof(jpeg_bytes_);
    return fetch_into(index, jpeg_bytes_, sizeof(jpeg_bytes_), length, clock,
                      profile);
  }
  bool fetch_into(uint32_t index, uint8_t *record, size_t capacity, size_t &length,
                  ProfileClock *clock, FrameProfile *profile) {
#ifdef MVB_FRAME_RECORD_READ
    jpeg_payload_ = record + Video::RecordHeaderBytes;
    return video_.frame_record(index, record, capacity, length,
                               clock, profile);
#else
    (void)capacity;
    jpeg_payload_ = record;
    return video_.frame(index, record, length, clock, profile);
#endif
  }
  void point_to_record(uint8_t *record) {
#ifdef MVB_FRAME_RECORD_READ
    jpeg_payload_ = record + Video::RecordHeaderBytes;
#else
    jpeg_payload_ = record;
#endif
  }
  bool prepare_first_record(const char *trigger, uint64_t preparation_started) {
#ifdef MVB_FIRST_FRAME_PRELOAD
    startup_ = {};
    if (!first_frame_preload_selected_)
      return true;
    startup_.attempted = true;
    startup_.generation = generation_;
    startup_.trigger = trigger;
    reset_benchmark(); // A failed fresh start must not inherit presented frames.
    // Normal render_frame joins its worker before returning. Still establish
    // that precondition explicitly before recycling either compressed slot.
    FramePrefetch::Result ignored;
    prefetch_.join(ignored);
    const uint64_t at = runtime_.now();
    const auto before = file_->io_profile();
    size_t length = 0;
    const bool ok = fetch_frame(0, length, &runtime_, &startup_.profile);
    const auto after = file_->io_profile();
    startup_.preload_us = runtime_.now() - at;
    startup_.latency_us = runtime_.now() - preparation_started;
    if (!ok) {
      if (before.available && after.available) {
        startup_.profile.io_available = true;
        startup_.profile.sd_read_us = after.read_us - before.read_us;
        startup_.profile.sd_seek_us = after.seek_us - before.seek_us;
      }
      startup_.error = video_.error;
      failure(video_.error);
      return false;
    }
    startup_.prepared = true;
    startup_.jpeg_bytes = length;
    prefetched_ = {};
    prefetched_.ok = true;
    prefetched_.index = 0;
    prefetched_.record = jpeg_bytes_;
    prefetched_.capacity = sizeof(jpeg_bytes_);
    prefetched_.length = length;
    prefetched_.wall_us = startup_.preload_us;
    prefetched_.profile = startup_.profile;
    prefetched_valid_ = true;
    startup_.latency_us = runtime_.now() - preparation_started;
#else
    (void)trigger;
    (void)preparation_started;
#endif
    return true;
  }
  bool render_frame(uint32_t index, bool counted) {
    size_t length = 0;
    frame_profile_ = {};
    uint64_t start = runtime_.now();
    bool retained = !counted && current_record_valid_ && retained_frame_ == index;
    if (retained) {
      length = current_jpeg_length_;
      read_us_ = 0;
      point_to_record(current_record_);
    } else if ((prefetch_.enabled() || startup_record_pending()) &&
               prefetched_valid_ && prefetched_.index == index) {
#ifdef MVB_FIRST_FRAME_PRELOAD
      const bool initial_record = startup_record_pending();
#endif
      prefetched_valid_ = false;
      if (!prefetched_.ok) {
        failure(prefetched_.error);
        return false;
      }
      current_record_ = prefetched_.record;
      current_record_capacity_ = prefetched_.capacity;
      length = prefetched_.length;
      read_us_ = prefetched_.wall_us;
      frame_profile_ = prefetched_.profile;
      point_to_record(current_record_);
#ifdef MVB_FIRST_FRAME_PRELOAD
      if (initial_record)
        startup_.consumed = true;
      else
#endif
        prefetch_hits_++;
    } else {
      if (prefetched_valid_)
        prefetch_misses_++;
      prefetched_valid_ = false;
      if (warm_frame_.valid)
        ++warmup_misses_;
      invalidate_warmup();
      // The previous current JPEG may be overwritten only as the next decode
      // starts. No worker or decoder retains it across this ownership change.
      if (!fetch_into(index, current_record_, current_record_capacity_, length,
                      &runtime_, &frame_profile_)) {
        failure(video_.error);
        return false;
      }
      read_us_ = runtime_.now() - start;
    }
    uint64_t fetch_wait = runtime_.now() - start;
    read_bytes_ = length;
    bool warmed = false;
    uint64_t prior_open = 0, prior_decode = 0;
    if (warm_frame_.valid) {
      if (counted && warmup_matches(index, length) &&
          new_jpeg_->resume_first_block(jpeg_payload_, length, renderer_,
                                        runtime_, painter_.big_endian())) {
        warmed = true;
        prior_open = warm_frame_.open_us;
        prior_decode = warm_frame_.decode_us;
        warm_frame_.valid = false;
        ++warmup_hits_;
      } else {
        ++warmup_misses_;
        invalidate_warmup();
      }
    }
    bool launched = false;
    if (counted && prefetch_.enabled() && timeline_.running()) {
      uint32_t next = 0;
      uint64_t expected_work = decode_us_ + draw_us_;
      if (!expected_work && timeline_.work_estimate > read_us_)
        expected_work = timeline_.work_estimate - read_us_;
      if (timeline_.next_for_prefetch(runtime_.now() + expected_work, next)) {
        uint8_t *other = current_record_ == jpeg_bytes_ ? prefetch_.buffer()
                                                       : jpeg_bytes_;
        size_t capacity = other == jpeg_bytes_ ? sizeof(jpeg_bytes_)
                                               : prefetch_.capacity();
#ifdef MVB_FRAME_RECORD_READ
        constexpr bool coalesced = true;
#else
        constexpr bool coalesced = false;
#endif
        launched = prefetch_.start(video_, next, other, capacity, coalesced);
        if (launched)
          prefetch_requests_++;
      }
    }
    // Delay only the final DMA join. Every block callback already consumed or
    // copied pixels, so the codec output can hold the next first block safely.
    // No current-frame completion is published until end_frame returns.
    const bool defer_end = counted && warmup_eligible() && launched;
    bool decoded = decode_jpeg(length, true, defer_end, warmed, prior_open,
                               prior_decode);
    if (launched) {
      uint64_t wait_start = runtime_.now();
      prefetched_valid_ = prefetch_.join(prefetched_);
      prefetch_join_us_ += runtime_.now() - wait_start;
      if (prefetched_valid_) {
        prefetch_background_us_ += prefetched_.wall_us;
        if (prefetched_.stack_free_bytes)
          prefetch_stack_free_min_ =
              std::min(prefetch_stack_free_min_, prefetched_.stack_free_bytes);
        if (!prefetched_.ok)
          prefetch_errors_++;
      }
    }
    bool future_decode_failed = false;
    if (defer_end && decoded && prefetched_valid_ && prefetched_.ok) {
      ++warmup_attempts_;
      const uint64_t warm_start = runtime_.now();
#ifdef MVB_FRAME_RECORD_READ
      const uint8_t *next_payload = prefetched_.record + Video::RecordHeaderBytes;
#else
      const uint8_t *next_payload = prefetched_.record;
#endif
      if (new_jpeg_->warm_first_block(next_payload, prefetched_.length,
                                      renderer_, runtime_,
                                      painter_.big_endian())) {
        warm_frame_.valid = true;
        warm_frame_.index = prefetched_.index;
        warm_frame_.generation = generation_;
        warm_frame_.revision = revision_;
        warm_frame_.payload = next_payload;
        warm_frame_.length = prefetched_.length;
        warm_frame_.geometry = renderer_.transform;
        warm_frame_.big_endian = painter_.big_endian();
        warm_frame_.open_us = new_jpeg_->first_block_open_us();
        warm_frame_.decode_us = new_jpeg_->first_block_decode_us();
      } else {
        ++warmup_errors_;
        future_decode_failed = true;
      }
      warmup_work_us_ += runtime_.now() - warm_start;
    }
    if (defer_end) {
      finish_render_frame();
      decoded = decoded && !draw_failed_;
    }
    if (!decoded) {
      failure(draw_failed_ ? "display_failed" : "unsupported_jpeg");
      return false;
    }
    current_record_valid_ = true;
    current_jpeg_length_ = length;
    retained_frame_ = index;
    current_frame_ = index;
    has_frame_ = true;
    auto display = painter_.display_profile();
    frame_profile_.display_available = display.available;
    frame_profile_.display_us = display.submit_us;
    frame_profile_.copy_us = display.copy_us;
    frame_profile_.wait_us = display.wait_us;
    frame_profile_.pixels = display.pixels;
    frame_profile_.windows = display.windows;
    frame_profile_.pushes = display.pushes;
    frame_profile_.transform_us =
        draw_us_ >= display.submit_us ? draw_us_ - display.submit_us : 0;
    frame_profile_.frame_us = runtime_.now() - start;
    if (counted) {
      timeline_.complete(index, start, runtime_.now());
      fps_count_++;
      benchmark_frames_++;
      benchmark_finished_ = runtime_.now();
      benchmark_read_ += read_us_;
      benchmark_decode_ += decode_us_;
      benchmark_draw_ += draw_us_;
      benchmark_fetch_wait_us_ += fetch_wait;
      benchmark_color_us_ += color_us_;
      benchmark_adapter_copy_us_ += adapter_copy_us_;
      pipeline_profile_.add(frame_profile_);
    }
    if (prefetched_valid_ && !prefetched_.ok) {
      // The current frame completed, but storage preparation of the next one
      // failed. Preserve the actual presentation count, then stop and publish
      // the checked worker error after the worker relinquishes ownership.
      failure(prefetched_.error);
      return false;
    }
    if (future_decode_failed) {
      failure("unsupported_jpeg");
      return false;
    }
    return true;
  }

public:
  Engine(Storage &s, Painter &p, Runtime &r, Output &o)
      : storage_(s), painter_(p), runtime_(r), output_(o), renderer_(p) {
    session_ = r.session_nonce();
    if (!session_)
      session_ = 1;
  }
  ~Engine() { close_file(); }
  void begin() {
#ifdef MVB_QUALIFIED_DEFAULTS
    // Configure after Engine/queue allocation, so DMA cannot consume memory
    // needed for essential startup. The serial task is created before loads.
    RenderOptions options;
    options.mode = RenderMode::Dma;
    options.big_endian = true;
    options.spi_hz = 40000000;
    options.command_spi_hz = 20000000;
#ifdef MVB_QUALIFIED_FAST_DEFAULTS
    options.zero_copy = NewJpeg::direct_output_available();
    if (!options.zero_copy)
      default_display_fallback_ = "direct_output_unavailable_using_copied_dma";
#endif
    if (!painter_.configure(options)) {
#ifdef MVB_QUALIFIED_FAST_DEFAULTS
      if (options.zero_copy) {
        options.zero_copy = false;
        if (painter_.configure(options))
          default_display_fallback_ = "direct_dma_unavailable_using_copied_dma";
        else {
          options.mode = RenderMode::Blocks;
          default_display_fallback_ = painter_.configure(options)
              ? "dma_unavailable_using_blocks" : "display_configuration_unavailable";
        }
      } else {
#endif
      options.zero_copy = false;
      options.mode = RenderMode::Blocks;
      default_display_fallback_ = painter_.configure(options)
                                      ? "dma_unavailable_using_blocks"
                                      : "display_configuration_unavailable";
#ifdef MVB_QUALIFIED_FAST_DEFAULTS
      }
#endif
    }
#endif
    storage_.mount();
    restore_queue();
    if (queue_generation_ && playlist_.count) {
      char path[96];
      std::snprintf(path, sizeof(path), "/muse-video/%s.mvb", playlist_.ids[playlist_.position]);
      if (storage_.exists(path)) begin_load(path);
      else {
        int next = -1;
        for (size_t i = 0; i < playlist_.count; ++i) {
          std::snprintf(path, sizeof(path), "/muse-video/%s.mvb", playlist_.ids[i]);
          if (storage_.exists(path)) { next = int(i); break; }
        }
        if (next >= 0) select_queue(next, true);
        else queue_error_ = "video_unavailable";
      }
    }
    painter_.waiting(storage_.mounted(), false, nullptr);
    last_poll_ = runtime_.now() - 2000000;
  }
  void command(const Frame &f) {
    context_sequence_ = 0;
    context_crc_ = 0;
    if (f.session != session_ && !(f.session == 0 && f.kind == 1)) {
      reject(f.sequence, "stale_session");
      return;
    }
    if (f.kind == 16) {
      last_contact_ = runtime_.now();
      connected_ = true;
      chunk(f);
      return;
    }
    if (f.kind != 1 || !utf8(f.payload, f.length)) {
      reject(f.sequence, "invalid_command");
      return;
    }
    command_json(f);
  }
  // JSON queue editing needs a larger stack frame. Binary upload chunks must
  // reach checkpoint/SD writes without carrying that unused JSON frame as well.
  __attribute__((noinline)) void command_json(const Frame &f) {
    JsonDocument cmd;
    auto parse = deserializeJson(cmd, f.payload, f.length,
                                 DeserializationOption::NestingLimit(4));
    const char *raw = cmd["method"].as<const char *>();
    if (parse || !cmd.is<JsonObject>() || !raw ||
        cmd["method"].as<JsonString>().size() != std::strlen(raw) ||
        std::strlen(raw) > 48 || cmd.size() > 16) {
      reject(f.sequence, "invalid_command");
      return;
    }
    char method[49];
    size_t n = std::strlen(raw);
    for (size_t i = 0; i < n; i++) {
      if (raw[i] > 127 || raw[i] < 32) {
        reject(f.sequence, "invalid_command");
        return;
      }
      method[i] = raw[i] >= 'A' && raw[i] <= 'Z' ? char(raw[i] + 32) : raw[i];
    }
    method[n] = 0;
    if (f.session == 0 && std::strcmp(method, "hello")) {
      reject(f.sequence, "stale_session");
      return;
    }
    if (!f.sequence) {
      reject(f.sequence, "invalid_parameter");
      return;
    }
    if (!std::strcmp(method, "hello")) {
      const char *nonce = cmd["client_nonce"].as<const char *>();
      if (!nonce || !nonce[0] || std::strlen(nonce) >= sizeof(client_nonce_) ||
          !cmd["protocol_min"].is<unsigned>() ||
          !cmd["protocol_max"].is<unsigned>() ||
          cmd["protocol_min"].as<unsigned>() > 1 ||
          cmd["protocol_max"].as<unsigned>() < 1) {
        reject(f.sequence, "unsupported_version");
        return;
      }
      if (std::strcmp(nonce, client_nonce_)) {
        std::snprintf(client_nonce_, sizeof(client_nonce_), "%s", nonce);
        uint32_t prior = session_;
        session_ = runtime_.session_nonce();
        if (!session_ || session_ == prior)
          session_ = prior == 0xffffffffu ? 1 : prior + 1;
        high_sequence_ = 0;
        chunk_sequence_ = 0;
        receipt_next_ = 0;
        for (auto &r : receipts_)
          r = Receipt{};
      }
    }
    uint32_t request_crc = crc32(f.payload, f.length);
    for (auto &receipt : receipts_)
      if (receipt.sequence == f.sequence && f.sequence) {
        if (receipt.crc != request_crc) {
          reject(f.sequence, "sequence_conflict");
          return;
        }
        if (receipt.length) {
          last_contact_ = runtime_.now();
          connected_ = true;
          Frame reply;
          reply.kind = 2;
          reply.session = session_;
          reply.sequence = f.sequence;
          reply.length = receipt.length;
          std::memcpy(reply.payload, receipt.data, receipt.length);
          output_.send(reply);
          return;
        }
      }
    bool cached = false;
    for (const auto &r : receipts_)
      if (r.sequence == f.sequence)
        cached = true;
    if (!cached && f.sequence <= high_sequence_) {
      reject(f.sequence, "stale_sequence");
      return;
    }
    high_sequence_ = std::max(high_sequence_, f.sequence);
    context_sequence_ = f.sequence;
    context_crc_ = request_crc;
    last_contact_ = runtime_.now();
    if (!connected_)
      waiting_dirty_ = true;
    connected_ = true;
    JsonDocument reply;
    reply["ok"] = true;
    auto result = reply["result"].to<JsonObject>();
    auto object = cmd.as<JsonObjectConst>();
    auto is = [&](const char *name) { return !std::strcmp(method, name); };
    // Queries and subscription traffic never recycle decoder/input storage.
    // All other commands abort a cached partial picture before any mutation,
    // including invalid requests and probes that borrow compressed scratch.
    if (!(is("hello") || is("get_status") || is("get_telemetry") ||
          is("get_info") || is("ping") || is("get_benchmark") ||
          is("get_pipeline_profile") || is("get_display_info") ||
          is("get_serial_diagnostics") || is("get_recovery_info") ||
          is("upload_status") || is("get_playback_defaults") ||
          is("get_startup_profile") || is("get_playback_state") ||
          is("get_loop_diagnostics") || is("set_loop_options") || is("subscribe")))
      invalidate_warmup();
    if (is("hello")) {
      if (!cmd["protocol_min"].is<unsigned>() ||
          !cmd["protocol_max"].is<unsigned>() ||
          cmd["protocol_min"].as<unsigned>() > 1 ||
          cmd["protocol_max"].as<unsigned>() < 1) {
        reject(f.sequence, "unsupported_version");
        return;
      }
      result["firmware_version"] = "0.4.1";
      result["protocol_major"] = 1;
      result["container_version"] = 1;
      result["configured_board"] = "ESP32-2432S028R-one-USB";
      result["configured_controller"] = "ILI9341";
      result["physical_verification"] = "unverified";
      result["width"] = 320;
      result["height"] = 240;
      auto caps = result["capabilities"].to<JsonObject>();
      caps["seek"] = true;
      caps["direct_sd_playback"] = true;
      caps["engine_allocation_bytes"] = sizeof(Engine);
      caps["index_cache_bytes"] = 64 * 24;
      caps["baud_rates"][0] = runtime_.configured_baud();
      caps["serial_upload"] = true;
      caps["playback_queue"] = true;
      caps["queue_capacity"] = Playlist::Capacity;
      caps["arbitrary_rotation"] = false;
      caps["max_jpeg_bytes"] = MaxJpeg;
      caps["max_file_bytes"] = 2147483647;
      caps["rotations_deg"][0] = 0;
      caps["rotations_deg"][1] = 90;
      caps["rotations_deg"][2] = 180;
      caps["rotations_deg"][3] = 270;
      caps["fps_hardware_qualified"] = nullptr;
      runtime_.telemetry(result["hardware"].to<JsonObject>());
    } else if (is("get_benchmark")) {
      benchmark_status(result);
    } else if (is("get_pipeline_profile")) {
      pipeline_status(result);
    } else if (is("get_display_info")) {
      runtime_.display_info(result);
    } else if (is("get_recovery_info")) {
      runtime_.recovery_info(result);
    } else if (is("get_playback_defaults")) {
      qualified_defaults_status(result);
    } else if (is("get_startup_profile")) {
      uint64_t elapsed =
          (timeline_.running() ? runtime_.now() : benchmark_finished_) -
          benchmark_started_;
      startup_status(result, true, elapsed);
    } else if (is("set_startup_options")) {
      if (!cmd["preload_first_frame"].is<bool>()) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      if (transfer_active() ||
          (work_ != Work::None && work_ != Work::LoadHash &&
           work_ != Work::LoadIndex)) {
        reject(f.sequence, "busy");
        return;
      }
#ifdef MVB_FIRST_FRAME_PRELOAD
      first_frame_preload_selected_ = cmd["preload_first_frame"].as<bool>();
      result["available"] = true;
      result["preload_first_frame"] = first_frame_preload_selected_;
#else
      if (cmd["preload_first_frame"].as<bool>()) {
        reject(f.sequence, "unsupported_configuration",
               "First-frame preparation requires its explicit research build");
        return;
      }
      result["available"] = false;
      result["preload_first_frame"] = false;
#endif
    } else if (is("get_loop_diagnostics")) {
      runtime_.loop_diagnostics(result);
    } else if (is("set_loop_options")) {
      if (!cmd["idle_yield_us"].is<uint32_t>()) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      const uint32_t interval = cmd["idle_yield_us"].as<uint32_t>();
      if (interval != 50000 && interval != 250000 && interval != 1000000) {
        reject(f.sequence, "invalid_parameter",
               "Loop idle interval: 50000, 250000 or 1000000 microseconds");
        return;
      }
      if (!runtime_.configure_loop_options(interval)) {
        reject(f.sequence, "unsupported_configuration",
               "Adaptive loop unavailable or interval exceeds watchdog bound");
        return;
      }
      runtime_.loop_diagnostics(result);
    } else if (is("set_sd_clock")) {
      if (transfer_active() || writer_ ||
          (work_ != Work::None && work_ != Work::LoadHash &&
           work_ != Work::LoadIndex)) {
        reject(f.sequence, "busy");
        return;
      }
      if (!cmd["hz"].is<uint32_t>()) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      uint32_t hz = cmd["hz"].as<uint32_t>();
      if (hz != 10000000 && hz != 20000000 && hz != 40000000) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      char path[96];
      std::snprintf(path, sizeof(path), "%s", current_);
      timeline_.pause(runtime_.now());
      close_file();
      work_ = Work::None;
      if (!storage_.configure_clock(hz)) {
        failure("sd_unavailable");
        reject(f.sequence, "sd_unavailable");
        return;
      }
      if (path[0])
        begin_load(path);
      result["requested_hz"] = hz;
      result["sd_mounted"] = storage_.mounted();
    } else if (is("probe_pipeline")) {
      if (transfer_active() || work_ != Work::None) {
        reject(f.sequence, "busy");
        return;
      }
      const char *stage = cmd["stage"].as<const char *>();
      if ((!cmd["repetitions"].isNull() &&
           !cmd["repetitions"].is<unsigned>()) ||
          !stage ||
          (std::strcmp(stage, "display") && std::strcmp(stage, "sd") &&
           std::strcmp(stage, "decode") &&
           std::strcmp(stage, "frame_integrity"))) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      unsigned repetitions =
          cmd["repetitions"].isNull() ? 1 : cmd["repetitions"].as<unsigned>();
      if (!repetitions || repetitions > 4) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      uint64_t paused_at = runtime_.now();
      if (timeline_.running())
        benchmark_finished_ = paused_at;
      timeline_.pause(paused_at);
      if (file_)
        state_ = "paused";
      result["microbenchmark_only"] = true;
      result["stage"] = stage;
      if (!std::strcmp(stage, "display")) {
        if (!runtime_.display_probe(result, repetitions)) {
          reject(f.sequence, "unsupported_configuration");
          return;
        }
        redraw_ = has_frame_;
      } else if (!file_ || !video_.ready) {
        reject(f.sequence, "no_video");
        return;
      } else if (!std::strcmp(stage, "frame_integrity")) {
        const auto &t = renderer_.transform;
        if (!has_frame_ || !renderer_.native_blocks || t.rotation != 0 ||
            t.width != renderer_.source_width ||
            t.height != renderer_.source_height || t.x < 0 || t.y < 0 ||
            t.x + t.width > 320 || t.y + t.height > 240) {
          reject(f.sequence, "unsupported_configuration",
                 "Integrity probe requires a visible native unrotated frame");
          return;
        }
        uint64_t expected = uint64_t(t.width) * uint64_t(t.height);
        IntegrityProfile total;
        total.available = true;
        uint64_t started = runtime_.now();
        for (unsigned pass = 0; pass < repetitions; pass++) {
          if (!painter_.begin_integrity_check()) {
            reject(f.sequence, "unsupported_configuration",
                   "Display readback comparison is unavailable");
            return;
          }
          bool decoded = render_frame(current_frame_, false);
          auto checked = painter_.end_integrity_check();
          // end_integrity_check runs even if render_frame failed and cleaned up
          // its file, so the display cannot remain accidentally in probe mode.
          if (!decoded || !checked.available ||
              checked.pixels_checked != expected ||
              checked.pixels_mismatched > checked.pixels_checked) {
            reject(f.sequence, "integrity_check_failed",
                   "Full frame decoding/readback did not complete");
            return;
          }
          total.pixels_checked += checked.pixels_checked;
          total.pixels_mismatched += checked.pixels_mismatched;
          total.read_us += checked.read_us;
          total.displayed_crc32 = checked.displayed_crc32;
        }
        result["available"] = total.available;
        result["frames_checked"] = repetitions;
        result["pixels_checked"] = total.pixels_checked;
        result["pixels_mismatched"] = total.pixels_mismatched;
        result["displayed_crc32"] = total.displayed_crc32;
        result["displayed_crc32_scope"] = "last_pass_RGB565_BE_row_major";
        result["expected_pixels"] = expected * repetitions;
        result["read_us"] = total.read_us;
        result["wall_us"] = runtime_.now() - started;
        result["complete"] = true;
        result["passed"] = total.pixels_mismatched == 0;
        auto region = result["region"].to<JsonObject>();
        region["x"] = t.x;
        region["y"] = t.y;
        region["width"] = t.width;
        region["height"] = t.height;
        result["scope"] = "uncounted_actual_TFT_readback";
      } else if (!std::strcmp(stage, "sd")) {
        // This probe borrows the primary compressed slot as scratch storage.
        prefetched_valid_ = current_record_valid_ = false;
        uint64_t bytes = std::min(uint64_t(153600), file_->size());
        auto before = file_->io_profile();
        uint64_t started = runtime_.now();
        for (unsigned pass = 0; pass < repetitions; pass++) {
          if (!file_->seek(0)) {
            reject(f.sequence, "seek_failed");
            return;
          }
          for (uint64_t offset = 0; offset < bytes;) {
            size_t n = size_t(std::min(uint64_t(16384), bytes - offset));
            if (!exact(*file_, jpeg_bytes_, n)) {
              reject(f.sequence, "sd_read_failed");
              return;
            }
            offset += n;
          }
        }
        auto after = file_->io_profile();
        result["bytes"] = bytes * repetitions;
        result["buffer_bytes"] = 16384;
        result["wall_us"] = runtime_.now() - started;
        result["io_available"] = after.available;
        result["read_us"] = after.read_us - before.read_us;
        result["seek_us"] = after.seek_us - before.seek_us;
        result["scope"] =
            "existing_file_sequential_read_not_raw_video_playback";
      } else if (!std::strcmp(stage, "decode")) {
        size_t length = 0;
        if (!fetch_frame(has_frame_ ? current_frame_ : 0, length)) {
          reject(f.sequence, video_.error);
          return;
        }
        uint64_t started = runtime_.now();
        for (unsigned pass = 0; pass < repetitions; pass++) {
          if (!decode_jpeg(length, false)) {
            reject(f.sequence, "unsupported_jpeg");
            return;
          }
        }
        result["frames_decoded"] = repetitions;
        result["jpeg_bytes"] = length;
        result["wall_us"] = runtime_.now() - started;
        result["scope"] = "repeated_frame_decode_including_color_no_SD_or_TFT";
      } else {
        reject(f.sequence, "invalid_parameter");
        return;
      }
    } else if (is("set_render_options")) {
      if (transfer_active() || work_ != Work::None) {
        reject(f.sequence, "busy");
        return;
      }
      const char *mode = cmd["mode"].as<const char *>();
      if (!mode || !cmd["big_endian"].is<bool>()) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      RenderOptions options;
      if (!std::strcmp(mode, "legacy"))
        options.mode = RenderMode::Legacy;
      else if (!std::strcmp(mode, "blocks"))
        options.mode = RenderMode::Blocks;
      else if (!std::strcmp(mode, "dma"))
        options.mode = RenderMode::Dma;
      else {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      options.big_endian = cmd["big_endian"].as<bool>();
      if (!cmd["zero_copy"].isNull()) {
        if (!cmd["zero_copy"].is<bool>()) {
          reject(f.sequence, "invalid_parameter");
          return;
        }
        options.zero_copy = cmd["zero_copy"].as<bool>();
      }
      if (options.zero_copy &&
          (!NewJpeg::direct_output_available() ||
           options.mode != RenderMode::Dma || !options.big_endian)) {
        reject(f.sequence, "unsupported_configuration",
               "Zero-copy output requires its research build, DMA and big-endian pixels");
        return;
      }
      if (!cmd["spi_hz"].isNull()) {
        if (!cmd["spi_hz"].is<uint32_t>()) {
          reject(f.sequence, "invalid_parameter");
          return;
        }
        options.spi_hz = cmd["spi_hz"].as<uint32_t>();
      }
      if (options.spi_hz != 20000000 && options.spi_hz != 40000000 &&
          options.spi_hz != 80000000) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      options.command_spi_hz = std::min(options.spi_hz, uint32_t(40000000));
      if (!cmd["command_spi_hz"].isNull()) {
        if (!cmd["command_spi_hz"].is<uint32_t>()) {
          reject(f.sequence, "invalid_parameter");
          return;
        }
        options.command_spi_hz = cmd["command_spi_hz"].as<uint32_t>();
      }
      if (options.command_spi_hz != 10000000 &&
          options.command_spi_hz != 20000000 &&
          options.command_spi_hz != 40000000 &&
          options.command_spi_hz != 80000000) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      if (options.mode == RenderMode::Legacy &&
          (options.spi_hz != 20000000 || options.command_spi_hz != 20000000)) {
        reject(f.sequence, "unsupported_configuration",
               "Legacy mode requires 20 MHz command and pixel clocks");
        return;
      }
      if (!cmd["verify_every_frames"].isNull()) {
        if (!cmd["verify_every_frames"].is<uint32_t>() ||
            cmd["verify_every_frames"].as<uint32_t>() > 300) {
          reject(f.sequence, "invalid_parameter");
          return;
        }
        options.verify_every_frames = cmd["verify_every_frames"].as<uint32_t>();
      }
      if (!painter_.configure(options)) {
        reject(f.sequence, "unsupported_configuration",
               "Display mode unavailable or insufficient DMA memory");
        return;
      }
      result["mode"] = mode;
      result["big_endian"] = options.big_endian;
      result["zero_copy"] = options.zero_copy;
      result["requested_spi_hz"] = options.spi_hz;
      result["requested_command_spi_hz"] = options.command_spi_hz;
      result["verify_every_frames"] = options.verify_every_frames;
    } else if (is("get_serial_diagnostics")) {
      runtime_.serial_diagnostics(result);
      result["engine_bytes"] = sizeof(*this);
    } else if (is("set_prefetch_options")) {
      if (!cmd["enabled"].is<bool>()) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      if (transfer_active() || work_ != Work::None || !file_ || !video_.ready) {
        reject(f.sequence, "busy",
               "Prefetch options require loaded video and idle storage");
        return;
      }
      if (cmd["enabled"].as<bool>()) {
        if (!FramePrefetch::available()) {
          reject(f.sequence, "unsupported_configuration");
          return;
        }
        size_t capacity = video_.header.max_jpeg + Video::RecordHeaderBytes + 3;
        if (!prefetch_.configure(capacity)) {
          reject(f.sequence, "insufficient_memory",
                 "Synchronous playback remains available");
          return;
        }
      } else {
        prefetch_.release();
        current_record_ = jpeg_bytes_;
        current_record_capacity_ = sizeof(jpeg_bytes_);
        current_record_valid_ = false;
      }
      prefetched_valid_ = false;
      prefetch_stack_free_min_ = FramePrefetch::StackBytes;
      reset_benchmark();
      prefetch_status(result);
    } else if (is("set_decoder_pipeline")) {
      if (!cmd["warmup"].is<bool>() ||
          (!cmd["blocks"].isNull() && !cmd["blocks"].is<unsigned>())) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      if (transfer_active() || work_ != Work::None) {
        reject(f.sequence, "busy");
        return;
      }
      const unsigned blocks = cmd["blocks"].isNull()
                                  ? 1 : cmd["blocks"].as<unsigned>();
      if (blocks != 1 && blocks != 2) {
        reject(f.sequence, "invalid_parameter",
               "Decoder warmup blocks: 1 or 2");
        return;
      }
      if (blocks == 2 && !NewJpeg::two_block_warmup_available()) {
        reject(f.sequence, "unsupported_configuration",
               "Two-block warmup requires its explicit research build guard");
        return;
      }
      if (cmd["warmup"].as<bool>() &&
          (!NewJpeg::first_block_warmup_available() ||
           decoder_ != Decoder::NewJpeg || !new_jpeg_ || !prefetch_.enabled())) {
        reject(f.sequence, "unsupported_configuration",
               "Warmup requires compiled NewJPEG and enabled compressed prefetch");
        return;
      }
      if (new_jpeg_ &&
          !new_jpeg_->configure_warmup_blocks(cmd["warmup"].as<bool>() ? blocks : 1)) {
        reject(f.sequence, "insufficient_memory",
               "Two-block warmup cache allocation failed; existing selection remains");
        return;
      }
      first_block_warmup_selected_ = cmd["warmup"].as<bool>();
      reset_benchmark();
      warmup_status(result);
    } else if (is("set_decoder_options")) {
      if (transfer_active() || work_ != Work::None || !file_ || !video_.ready) {
        reject(f.sequence, "busy",
               "Decoder options require loaded video and idle storage");
        return;
      }
      if (!cmd["max_mcus"].is<unsigned>() ||
          cmd["max_mcus"].as<unsigned>() < 1 ||
          cmd["max_mcus"].as<unsigned>() > 32 ||
          !cmd["native_blocks"].is<bool>()) {
        reject(f.sequence, "invalid_parameter",
               "max_mcus: 1..32; native_blocks: Boolean");
        return;
      }
      const char *decoder = cmd["decoder"].isNull()
                                ? decoder_name()
                                : cmd["decoder"].as<const char *>();
      if (!decoder) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      Decoder selected;
      if (!std::strcmp(decoder, "jpegdec"))
        selected = Decoder::Jpegdec;
      else if (!std::strcmp(decoder, "rom"))
        selected = Decoder::Rom;
      else if (!std::strcmp(decoder, "tjpgd_fast"))
        selected = Decoder::TjpgdFast;
      else if (!std::strcmp(decoder, "new_jpeg"))
        selected = Decoder::NewJpeg;
      else {
        reject(f.sequence, "invalid_parameter",
               "decoder: jpegdec, rom, tjpgd_fast or new_jpeg");
        return;
      }
      if ((selected == Decoder::Rom && !RomJpeg::available()) ||
          (selected == Decoder::TjpgdFast && !TjpgdFast::available()) ||
          (selected == Decoder::NewJpeg && !NewJpeg::available())) {
        reject(f.sequence, "unsupported_configuration",
               "Requested decoder is unavailable in this target/build");
        return;
      }
      if (selected == Decoder::NewJpeg &&
          (video_.header.width % 8 || video_.header.height % 8)) {
        reject(f.sequence, "unsupported_configuration",
               "new_jpeg block output requires dimensions divisible by eight");
        return;
      }
      if (selected == Decoder::Rom && !rom_jpeg_) {
        rom_jpeg_.reset(new (std::nothrow) RomJpeg);
        if (!rom_jpeg_) {
          reject(f.sequence, "insufficient_memory",
                 "Reusable ROM workspace and RGB565 strip allocation failed");
          return;
        }
      }
      if (selected == Decoder::TjpgdFast && !tjpgd_fast_) {
        tjpgd_fast_.reset(new (std::nothrow) TjpgdFast);
        if (!tjpgd_fast_) {
          reject(f.sequence, "insufficient_memory",
                 "Reusable fast decoder allocation failed");
          return;
        }
      }
      if (selected == Decoder::NewJpeg) {
        std::unique_ptr<NewJpeg> candidate;
        NewJpeg *next = new_jpeg_.get();
        if (!next) {
          candidate.reset(new (std::nothrow) NewJpeg);
          next = candidate.get();
        }
        if (!next) {
          reject(f.sequence, "insufficient_memory",
                 "Block decoder allocation failed");
          return;
        }
        size_t length = 0;
        if (!fetch_frame(has_frame_ ? current_frame_ : 0, length)) {
          reject(f.sequence, video_.error);
          return;
        }
        if (!next->prepare(jpeg_payload_, length, renderer_, runtime_, false,
                           painter_.big_endian()
#ifdef MVB_QUALIFIED_FAST_DEFAULTS
                           , painter_.output_leases_available()
#endif
                           )) {
          reject(f.sequence, "unsupported_configuration",
                 "Block decoder preparation failed or insufficient workspace");
          return;
        }
        if (candidate)
          new_jpeg_ = std::move(candidate);
      }
      if (selected != Decoder::Rom)
        rom_jpeg_.reset();
      if (selected != Decoder::TjpgdFast)
        tjpgd_fast_.reset();
      if (selected != Decoder::NewJpeg)
        new_jpeg_.reset();
      decoder_ = selected;
      prefetched_valid_ = false;
      decoder_mcus_ = cmd["max_mcus"].as<unsigned>();
      renderer_.native_blocks = cmd["native_blocks"].as<bool>();
      reset_benchmark();
      result["decoder"] = decoder_name();
      result["decoder_workspace_bytes"] = decoder_owned_bytes();
      result["decoder_private_memory_included"] = selected != Decoder::NewJpeg;
      result["max_mcus"] = decoder_mcus_;
      result["max_mcus_applies"] = selected == Decoder::Jpegdec;
      result["native_blocks"] = renderer_.native_blocks;
    } else if (is("get_status") || is("get_telemetry") || is("get_info") ||
               is("ping")) {
      status(result);
      if (!is("get_status"))
        runtime_.telemetry(result["hardware"].to<JsonObject>());
    } else if (is("subscribe")) {
      if (!cmd["telemetry_interval_ms"].is<unsigned>() ||
          cmd["telemetry_interval_ms"].as<unsigned>() < 500 ||
          cmd["telemetry_interval_ms"].as<unsigned>() > 10000) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      telemetry_ms_ = cmd["telemetry_interval_ms"].as<unsigned>();
      result["telemetry_interval_ms"] = telemetry_ms_;
    }
#ifdef MVB_TEST_TOUCH
    else if (is("test_touch")) {
      if (!cmd["x"].is<int>() || !cmd["y"].is<int>()) {
        reject(f.sequence, "invalid_parameter"); return;
      }
      touch(cmd["x"].as<int>(), cmd["y"].as<int>());
      result["state_revision"] = revision_;
    }
#endif
    else if (is("get_playback_state")) {
      playback_snapshot(result);
    } else if (is("set_loop_mode")) {
      const char *mode = cmd["mode"].as<const char *>();
      if (!mode || (std::strcmp(mode, "play_once") && std::strcmp(mode, "loop_current") &&
                    std::strcmp(mode, "loop_queue"))) {
        reject(f.sequence, "invalid_parameter"); return;
      }
      Playlist candidate = playlist_;
      candidate.mode = !std::strcmp(mode, "loop_current") ? LoopMode::Current
                       : !std::strcmp(mode, "loop_queue") ? LoopMode::Queue : LoopMode::Once;
      if (!save_queue(candidate)) { reject(f.sequence, "queue_save_failed"); return; }
      playlist_ = candidate;
      ++playlist_.revision; ++revision_;
      controls_dirty_ = controls_visible_;
      result["state_revision"] = revision_;
    } else if (is("set_queue")) {
      if (transfer_active() || work_ != Work::None) {
        reject(f.sequence, "busy"); return;
      }
      if (!cmd["expected_queue_revision"].is<uint32_t>() ||
          cmd["expected_queue_revision"].as<uint32_t>() != playlist_.revision) {
        reject(f.sequence, "queue_conflict", "Refresh the device queue before editing"); return;
      }
      auto ids = cmd["ids"].as<JsonArrayConst>();
      if (!cmd["ids"].is<JsonArrayConst>() || ids.size() > Playlist::Capacity) {
        reject(f.sequence, "invalid_parameter"); return;
      }
      Playlist candidate;
      candidate.mode = playlist_.mode;
      for (JsonVariantConst value : ids) {
        const char *id = value.as<const char *>();
        char path[96];
        if (!digest_valid(id) || value.as<JsonString>().size() != 64 || candidate.find(id) >= 0) {
          reject(f.sequence, "invalid_parameter"); return;
        }
        std::snprintf(path, sizeof(path), "/muse-video/%s.mvb", id);
        if (!storage_.exists(path)) { reject(f.sequence, "video_unavailable"); return; }
        candidate.append(id);
      }
      char active[65]{};
      if (playlist_.position >= 0) std::memcpy(active, playlist_.ids[playlist_.position], 65);
      bool autoplay = timeline_.running();
      int previous_position = playlist_.position;
      candidate.position = candidate.find(active);
      if (candidate.position < 0 && candidate.count) candidate.position = std::min(std::max(0, previous_position), int(candidate.count) - 1);
      candidate.revision = playlist_.revision + 1;
      if (!save_queue(candidate)) { reject(f.sequence, "queue_save_failed"); return; }
      bool removed_active = candidate.find(active) < 0;
      playlist_ = candidate;
      queue_initialized_ = true;
      if (removed_active) {
        if (playlist_.count) {
          if (!select_queue(std::min(std::max(0, previous_position), int(playlist_.count)-1), autoplay, false))
            failure("video_unavailable");
        } else {
          close_file(); timeline_.stop(); state_ = "idle"; digest_[0] = 0;
          has_frame_ = false; ++generation_; waiting_dirty_ = true;
          controls_visible_ = false; renderer_.viewport_height = 240;
        }
      }
      ++revision_; controls_dirty_ = controls_visible_;
      result["queue_revision"] = playlist_.revision;
      result["state_revision"] = revision_;
    } else if (is("next_video") || is("previous_video") || (is("select_video") && !cmd["video_id"].isNull())) {
      if (transfer_active() || work_ != Work::None) { reject(f.sequence, "busy"); return; }
      bool ok = false;
      if (is("select_video")) {
        const char *id = cmd["video_id"].as<const char *>();
        ok = digest_valid(id) && select_queue(playlist_.find(id), true);
      } else ok = adjacent_video(is("next_video") ? 1 : -1, timeline_.running());
      if (!ok) { reject(f.sequence, "video_unavailable"); return; }
      result["state_revision"] = revision_;
    } else if (is("begin_upload")) {
      // A long startup hash must not prevent the owner's USB upload. Reading
      // validation can be stopped without changing the existing SD file.
      if (work_ == Work::LoadHash || work_ == Work::LoadIndex) {
        const char *hash = cmd["sha256"].as<const char *>();
        if (!digest_valid(hash) ||
            cmd["sha256"].as<JsonString>().size() != 64 ||
            !cmd["size"].is<uint64_t>() || cmd["size"].as<uint64_t>() < 136 ||
            cmd["size"].as<uint64_t>() > 2147483647 ||
            cmd["container_version"] != 1) {
          reject(f.sequence, "invalid_parameter");
          return;
        }
        close_file();
        work_ = Work::None;
      }
      if (transfer_active()) {
        const char *requested = cmd["sha256"].as<const char *>();
        if (!digest_valid(requested) ||
            cmd["sha256"].as<JsonString>().size() != 64 ||
            !cmd["size"].is<uint64_t>() ||
            std::strcmp(requested, upload_digest_) ||
            cmd["size"] != upload_size_ || cmd["container_version"] != 1) {
          reject(f.sequence, "busy",
                 "A different USB transfer owns the SD card");
          return;
        }
        if (std::strcmp(upload_state_, "receiving")) {
          // A reconnect may follow an interrupted verification/resume. Keep
          // its bounded work and identity rather than restarting or cancelling.
          upload_status(result);
          json_frame(2, f.sequence, reply, true, request_crc);
          return;
        }
        // A fresh client can reconnect before the inactivity timeout. Safely
        // checkpoint the same file, close the old writer and validate its
        // prefix. Heartbeats must not keep an orphan writer permanently busy.
        if (!checkpoint()) {
          upload_failure(checkpoint_error_);
          reject(f.sequence, checkpoint_error_);
          return;
        }
        writer_->close();
        writer_ = nullptr;
        upload_state_ = "cancelled";
      }
      if (work_ != Work::None || writer_ ||
          !std::strcmp(upload_state_, "receiving")) {
        reject(f.sequence, "busy");
        return;
      }
      if (!begin_upload(object)) {
        if (writer_) {
          writer_->close();
          writer_ = nullptr;
        }
        close_file();
        work_ = Work::None;
        upload_state_ = "error";
        reject(f.sequence, upload_error_ ? upload_error_ : "upload_rejected",
               "Transfer start failed; existing videos and recovery files "
               "retained");
        return;
      }
      upload_status(result);
    } else if (is("upload_status")) {
      if (!cmd["transfer_id"].isNull() && cmd["transfer_id"] != transfer_id_) {
        reject(f.sequence, "invalid_parameter", "Wrong transfer ID");
        return;
      }
      upload_status(result);
    } else if (is("cancel_upload")) {
      if (!transfer_id_ || cmd["transfer_id"] != transfer_id_ ||
          !std::strcmp(upload_state_, "complete") ||
          !std::strcmp(upload_state_, "none")) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      if (writer_) {
        if (!checkpoint()) {
          upload_failure(checkpoint_error_);
          reject(f.sequence, checkpoint_error_);
          return;
        }
        writer_->close();
        writer_ = nullptr;
      }
      close_file();
      work_ = Work::None;
      upload_state_ = "cancelled";
      finalizing_ = false;
      upload_status(result);
    } else if (is("finish_upload")) {
      if (cmd["transfer_id"] != transfer_id_ ||
          std::strcmp(upload_state_, "receiving") ||
          accepted_ != upload_size_) {
        reject(f.sequence, "incomplete_transfer");
        return;
      }
      if (!checkpoint()) {
        upload_failure(checkpoint_error_);
        reject(f.sequence, checkpoint_error_);
        return;
      }
      writer_->close();
      writer_ = nullptr;
      file_ = storage_.read_file(partial_);
      if (!file_) {
        upload_failure("sd_open_failed");
        reject(f.sequence, "sd_open_failed");
        return;
      }
      if (!runtime_.hash_begin()) {
        upload_failure("hash_failed");
        reject(f.sequence, "hash_failed");
        return;
      }
      upload_state_ = "verifying";
      finalizing_ = true;
      work_offset_ = 0;
      work_ = Work::UploadHash;
      upload_status(result);
    } else if (is("discard_upload")) {
      if (!transfer_id_ || !upload_owned_ || !digest_valid(upload_digest_) ||
          !partial_[0] || !journal_[0] || cmd["transfer_id"] != transfer_id_ ||
          !cmd["confirmed"].is<bool>() || !cmd["confirmed"].as<bool>() ||
          transfer_active() || finalizing_) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      bool ok =
          storage_.remove_owned(partial_) && storage_.remove_owned(journal_);
      if (!ok) {
        reject(f.sequence, "io_error");
        return;
      }
      upload_state_ = "none";
      upload_owned_ = false;
      upload_status(result);
    } else if (is("select_video")) {
      if (transfer_active()) {
        reject(f.sequence, "busy", "SD card is owned by the USB transfer");
        return;
      }
      const char *hash = cmd["sha256"].as<const char *>();
      if (!digest_valid(hash) || cmd["sha256"].as<JsonString>().size() != 64 ||
          (work_ != Work::None && work_ != Work::LoadHash &&
           work_ != Work::LoadIndex)) {
        reject(f.sequence, "invalid_parameter");
        return;
      }
      if (playlist_.count == Playlist::Capacity && playlist_.find(hash) < 0) {
        reject(f.sequence, "queue_full"); return;
      }
      if (work_ == Work::LoadHash || work_ == Work::LoadIndex) {
        close_file();
        work_ = Work::None;
      }
      char path[96];
      std::snprintf(path, sizeof(path), "/muse-video/%s.mvb", hash);
      if (!begin_load(path)) {
        reject(f.sequence, "invalid_container");
        return;
      }
      result["state"] = "loading";
    } else if (is("play") || is("resume") || is("pause") || is("stop") ||
               is("restart") || is("seek") || is("set_position") ||
               is("set_size") || is("set_rotation") || is("set_scale") ||
               is("set_transform") || is("set_fps") || is("set_target_fps")) {
      if (!video_.ready || work_ != Work::None || !file_) {
        reject(f.sequence, "no_video");
        return;
      }
      uint64_t now = runtime_.now();
      if (is("pause") || is("stop") || is("play") || is("resume") || is("restart")) {
        if (!playback_action(method)) {
          reject(f.sequence, error_ ? error_ : "no_video");
          return;
        }
      } else if (is("seek")) {
        if (!cmd["position_us"].is<uint64_t>()) {
          reject(f.sequence, "invalid_parameter");
          return;
        }
        uint32_t index = video_.header.fps.at_or_after(
            cmd["position_us"].as<uint64_t>(), video_.header.frames);
        timeline_.seek(index, now);
        prefetched_valid_ = current_record_valid_ = false;
        generation_++;
        if (!timeline_.running())
          state_ = "paused";
        if (!render_frame(index, false)) {
          reject(f.sequence, "seek_failed");
          return;
        }
      } else if (is("set_fps") || is("set_target_fps")) {
        if (!cmd["numerator"].is<unsigned>() ||
            !cmd["denominator"].is<unsigned>()) {
          reject(f.sequence, "invalid_parameter");
          return;
        }
        Rate rate{cmd["numerator"].as<unsigned>(),
                  cmd["denominator"].as<unsigned>()};
        if (!rate.valid()) {
          reject(f.sequence, "invalid_parameter");
          return;
        }
        timeline_.target = rate;
        target_fps_explicit_ = true;
        prefetched_valid_ = false;
      } else {
        Transform next = renderer_.transform;
        auto integer = [&](const char *key, int &field) -> bool {
          if (!cmd[key].is<int>())
            return false;
          field = cmd[key].as<int>();
          return true;
        };
        bool valid = true;
        if (is("set_position") || is("set_transform"))
          valid = integer("x", next.x) && integer("y", next.y);
        if (is("set_size") || is("set_transform")) {
          valid = valid && integer("width", next.width) &&
                  integer("height", next.height);
          if (cmd["lock_aspect"].is<bool>())
            next.lock_aspect = cmd["lock_aspect"].as<bool>();
        }
        if (is("set_rotation") || is("set_transform")) {
          int rotation = 0;
          valid = valid && integer("rotation_deg", rotation) &&
                  (rotation == 0 || rotation == 90 || rotation == 180 ||
                   rotation == 270);
          if (!valid) {
            reject(f.sequence, "invalid_parameter");
            return;
          }
          if (is("set_rotation") && next.lock_aspect &&
              ((rotation - next.rotation + 360) % 180)) {
            int center_x = next.x + next.width / 2,
                center_y = next.y + next.height / 2;
            std::swap(next.width, next.height);
            next.x = center_x - next.width / 2;
            next.y = center_y - next.height / 2;
          }
          next.rotation = rotation;
        }
        if (is("set_scale")) {
          if (!cmd["scale"].is<double>())
            valid = false;
          else {
            double scale = cmd["scale"].as<double>();
            if (!std::isfinite(scale) || scale < 0.01 || scale > 8)
              valid = false;
            else {
              bool turn = next.rotation % 180;
              next.width = int(std::round(
                  (turn ? renderer_.source_height : renderer_.source_width) *
                  scale));
              next.height = int(std::round(
                  (turn ? renderer_.source_width : renderer_.source_height) *
                  scale));
            }
          }
        }
        if (!valid || !next.valid()) {
          reject(f.sequence, "invalid_parameter");
          return;
        }
        if (next.lock_aspect) {
          int rw = next.rotation % 180 ? renderer_.source_height
                                       : renderer_.source_width;
          int rh = next.rotation % 180 ? renderer_.source_width
                                       : renderer_.source_height;
          if (std::abs(next.height -
                       int(std::round(double(next.width) * rh / rw))) > 1) {
            reject(f.sequence, "invalid_parameter",
                   "Aspect-locked rectangle is inconsistent");
            return;
          }
        }
        clear_difference(painter_, clipped(renderer_.transform), clipped(next));
        renderer_.transform = next;
        prefetched_valid_ = false;
        redraw_ = has_frame_;
      }
      revision_++;
      controls_dirty_ = controls_visible_;
      result["state_revision"] = revision_;
      result["playhead_generation"] = generation_;
      result["state"] = state_;
    } else if (is("set_locale")) {
      reject(f.sequence, "unsupported_command",
             "Phase 2 device waiting screen is explicitly English");
      return;
    } else {
      reject(f.sequence, "unsupported_command");
      return;
    }
    json_frame(2, f.sequence, reply, true, request_crc);
  }
  // Called on a debounced physical press by the adapter, on the engine thread.
  void touch(int x, int y) {
    if (x < 0 || x >= 320 || y < 0 || y >= 240 || !file_ ||
        work_ != Work::None || transfer_active()) return;
    controls_until_ = runtime_.now() + 4000000;
    if (!controls_visible_) {
      invalidate_warmup();
      controls_visible_ = controls_dirty_ = true;
      renderer_.viewport_height = 176;
      return;
    }
    if (y < 184 || y >= 232) return;
    bool changed = false;
    if (x >= 8 && x < 104) changed = adjacent_video(-1, timeline_.running());
    else if (x >= 112 && x < 208)
      changed = playback_action(timeline_.running() ? "pause" : "play");
    else if (x >= 216 && x < 312) changed = adjacent_video(1, timeline_.running());
    if (changed) { ++revision_; controls_dirty_ = controls_visible_; }
  }
  void tick() {
    uint64_t now = runtime_.now();
    // A 3-attempt host ACK exchange can take nine seconds. Do not expire the
    // SD owner midway through retries or a slow FAT checkpoint.
    if (connected_ && now - last_contact_ > (transfer_active() ? 30000000ULL : 6000000ULL)) {
      connected_ = false;
      if (!std::strcmp(upload_state_, "receiving") ||
          !std::strcmp(upload_state_, "resuming") ||
          !std::strcmp(upload_state_, "verifying")) {
        if (writer_ && !checkpoint())
          upload_failure(checkpoint_error_);
        if (writer_) {
          writer_->close();
          writer_ = nullptr;
        }
        close_file();
        work_ = Work::None;
        finalizing_ = false;
        if (std::strcmp(upload_state_, "error"))
          upload_state_ = "cancelled";
      }
      waiting_dirty_ = true;
    }
    if (work_ != Work::None)
      work_step();
    if (now - last_poll_ >= 2000000 && work_ == Work::None && !writer_ &&
        (std::strcmp(upload_state_, "resuming"))) {
      last_poll_ = now;
      if (!storage_.mounted())
        storage_.mount();
      if (file_ && current_[0] && !storage_.exists(current_)) {
        failure("video_unavailable");
        blocked_[0] = 0;
      }
      if (!file_ && !queue_initialized_ && (std::strcmp(upload_state_, "receiving")) &&
          (std::strcmp(upload_state_, "verifying"))) {
        char path[96];
        if (storage_.mounted() &&
            storage_.first_video(path, sizeof(path), blocked_))
          begin_load(path);
      }
    }
    now = runtime_.now(); // Loading may have started the media clock after tick
                          // entry.
    if (work_ == Work::None && timeline_.running() && file_) {
      uint32_t index = 0;
      if (timeline_.due(now, index))
        render_frame(index, true);
      if (timeline_.ended(runtime_.now())) {
        benchmark_finished_ = runtime_.now();
        timeline_.pause(runtime_.now());
        state_ = "ended";
        ++revision_;
        if (playlist_.mode == LoopMode::Current ||
            (playlist_.mode == LoopMode::Queue && playlist_.count == 1)) {
          if (playback_action("restart")) ++revision_;
        } else if (adjacent_video(1, true)) {
          // Play Once advances without wrapping; Loop Queue wraps at the end.
        }
        controls_dirty_ = controls_visible_;
      }
    }
    if (redraw_ && work_ == Work::None && file_ && has_frame_) {
      redraw_ = false;
      render_frame(current_frame_, false);
    }
    now = runtime_.now();
    if (timeline_.running() && now - fps_start_ >= 2000000) {
      actual_fps_ = double(fps_count_) * 1000000 / double(now - fps_start_);
      fps_start_ = now;
      fps_count_ = 0;
      fps_available_ = true;
    }
    if (controls_visible_ && now >= controls_until_) {
      invalidate_warmup();
      controls_visible_ = false; renderer_.viewport_height = 240;
      painter_.clear({0, 176, 320, 64});
      if (has_frame_ && !timeline_.running()) redraw_ = true;
    }
    if (controls_visible_ && controls_dirty_ && work_ == Work::None) {
      painter_.controls(timeline_.running(), adjacent_position(-1) >= 0,
                        adjacent_position(1) >= 0);
      controls_dirty_ = false;
    }
    const char *stage = preparation_stage();
    const int percent = preparation_percent();
    if (now - last_preparation_draw_ >= 250000 &&
        (waiting_dirty_ || stage != drawn_stage_ || percent != drawn_percent_)) {
      last_preparation_draw_ = now;
      drawn_stage_ = stage; drawn_percent_ = percent;
      if (!std::strcmp(stage, "waiting") || !std::strcmp(stage, "error"))
        painter_.waiting(storage_.mounted(), connected_, error_ ? error_ : upload_error_);
      else if (std::strcmp(stage, "ready")) painter_.preparation(stage, percent);
      waiting_dirty_ = false;
      if (connected_) {
        JsonDocument doc; doc["event"] = "preparation";
        preparation_status(doc["preparation"].to<JsonObject>());
        doc["state_revision"] = revision_;
        json_frame(3, 0, doc);
      }
    }
    if (connected_ && (sent_revision_ != revision_ || sent_queue_revision_ != playlist_.revision) &&
        now - last_state_event_ >= 100000) {
      last_state_event_ = now;
      bool order_changed = sent_queue_revision_ != playlist_.revision;
      sent_revision_ = revision_; sent_queue_revision_ = playlist_.revision;
      JsonDocument doc; doc["event"] = "playback_state";
      playback_snapshot(doc["playback"].to<JsonObject>(), order_changed);
      json_frame(3, 0, doc);
    }
    if (connected_ && !transfer_active() &&
        now - last_telemetry_ >= uint64_t(telemetry_ms_) * 1000) {
      uint64_t started = runtime_.now();
      last_telemetry_ = now;
      JsonDocument doc;
      doc["event"] = "telemetry";
      status(doc["playback"].to<JsonObject>());
      runtime_.telemetry(doc["hardware"].to<JsonObject>());
      doc["uptime_us"] = now;
      json_frame(3, 0, doc);
      benchmark_telemetry_us_ += runtime_.now() - started;
    }
    if (connected_ && !transfer_active() && has_frame_ &&
        now - last_frame_event_ >= uint64_t(frame_ms_) * 1000) {
      last_frame_event_ = now;
      JsonDocument doc;
      doc["event"] = "frame";
      doc["frame_index"] = current_frame_;
      doc["frame_pts_us"] = video_.header.fps.pts(current_frame_);
      doc["content_sha256"] = digest_;
      doc["state_revision"] = revision_;
      doc["playhead_generation"] = generation_;
      doc["uptime_us"] = now;
      json_frame(3, 0, doc);
    }
  }
};
} // namespace mvb
