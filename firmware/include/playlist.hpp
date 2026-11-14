#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace mvb {
enum class LoopMode { Once, Current, Queue };
inline const char *loop_name(LoopMode mode) {
  return mode == LoopMode::Current ? "loop_current"
         : mode == LoopMode::Queue ? "loop_queue" : "play_once";
}
// Device-owned, bounded session queue. IDs are the verified container SHA-256.
// Editing order never deletes media. Queue revision is separate from playhead.
struct Playlist {
  static constexpr size_t Capacity = 12;
  char ids[Capacity][65]{};
  size_t count = 0;
  int position = -1;
  uint32_t revision = 0;
  LoopMode mode = LoopMode::Once;
  int find(const char *id) const {
    for (size_t i = 0; i < count; ++i)
      if (!std::strcmp(ids[i], id)) return int(i);
    return -1;
  }
  bool append(const char *id) {
    if (find(id) >= 0) return true;
    if (count == Capacity) return false;
    std::memcpy(ids[count++], id, 65);
    ++revision;
    return true;
  }
  int adjacent(int direction) const {
    if (!count || position < 0) return -1;
    int next = position + direction;
    if (next >= 0 && next < int(count)) return next;
    if (mode == LoopMode::Queue)
      return direction > 0 ? 0 : int(count) - 1;
    return -1;
  }
  int after_end() const {
    return mode == LoopMode::Current ? position : adjacent(1);
  }
};
} // namespace mvb
