#pragma once
#include "playlist.hpp"
#include "wire.hpp"
namespace mvb {
// Two alternating, CRC-protected records. An interrupted write leaves the
// preceding generation usable. This format is shared with mounted-card export.
constexpr size_t QueueRecordBytes = 808;
inline bool decode_queue(const uint8_t *b, Playlist &out, uint32_t &generation) {
  if (std::memcmp(b, "MVBQUE1\0", 8) || get32(b + 804) != crc32(b, 804) ||
      !get32(b + 8) || get32(b + 12) > Playlist::Capacity || get32(b + 20) > 2)
    return false;
  Playlist candidate;
  const uint32_t count = get32(b + 12), position = get32(b + 16);
  if ((count && position >= count) || (!count && position != UINT32_MAX)) return false;
  for (uint32_t i = 0; i < count; ++i) {
    const char *id = reinterpret_cast<const char *>(b + 24 + i * 65);
    if (id[64]) return false;
    for (size_t n = 0; n < 64; ++n)
      if (!(id[n] >= '0' && id[n] <= '9') && !(id[n] >= 'a' && id[n] <= 'f')) return false;
    if (candidate.find(id) >= 0) return false;
    candidate.append(id);
  }
  for (size_t i = 24 + count * 65; i < 804; ++i) if (b[i]) return false;
  candidate.position = count ? int(position) : -1;
  candidate.mode = get32(b + 20) == 1 ? LoopMode::Current : get32(b + 20) == 2 ? LoopMode::Queue : LoopMode::Once;
  generation = get32(b + 8); out = candidate;
  return true;
}
inline void encode_queue(uint8_t *b, const Playlist &queue, uint32_t generation) {
  std::memset(b, 0, QueueRecordBytes); std::memcpy(b, "MVBQUE1\0", 8);
  put32(b + 8, generation); put32(b + 12, uint32_t(queue.count));
  put32(b + 16, queue.count ? uint32_t(queue.position < 0 ? 0 : queue.position) : UINT32_MAX);
  put32(b + 20, queue.mode == LoopMode::Current ? 1 : queue.mode == LoopMode::Queue ? 2 : 0);
  for (size_t i = 0; i < queue.count; ++i) std::memcpy(b + 24 + i * 65, queue.ids[i], 65);
  put32(b + 804, crc32(b, 804));
}
} // namespace mvb
