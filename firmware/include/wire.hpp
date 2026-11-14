#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#if defined(ARDUINO_ARCH_ESP32) && defined(MVB_USE_ROM_CRC)
#include <esp_rom_crc.h>
#endif

namespace mvb {
constexpr size_t MaxPayload = 2048, MaxWire = 2078, MaxJpeg = 49152;
inline uint16_t get16(const uint8_t *p) {
  return uint16_t(p[0]) | (uint16_t(p[1]) << 8);
}
inline uint32_t get32(const uint8_t *p) {
  return uint32_t(get16(p)) | (uint32_t(get16(p + 2)) << 16);
}
inline uint64_t get64(const uint8_t *p) {
  return uint64_t(get32(p)) | (uint64_t(get32(p + 4)) << 32);
}
inline void put16(uint8_t *p, uint16_t n) {
  p[0] = uint8_t(n);
  p[1] = uint8_t(n >> 8);
}
inline void put32(uint8_t *p, uint32_t n) {
  put16(p, uint16_t(n));
  put16(p + 2, uint16_t(n >> 16));
}
inline void put64(uint8_t *p, uint64_t n) {
  put32(p, uint32_t(n));
  put32(p + 4, uint32_t(n >> 32));
}
inline uint32_t crc_update(uint32_t crc, const uint8_t *p, size_t n) {
#if defined(ARDUINO_ARCH_ESP32) && defined(MVB_USE_ROM_CRC)
  // ROM uses complemented input/output state. Keep the existing unfinalized
  // state contract so chunked journals, wire frames and JPEG CRCs stay
  // identical.
  return ~esp_rom_crc32_le(~crc, p, uint32_t(n));
#else
  while (n--) {
    crc ^= *p++;
    for (int i = 0; i < 8; i++)
      crc = (crc >> 1) ^ (0xedb88320u & uint32_t(-int32_t(crc & 1)));
  }
  return crc;
#endif
}
inline uint32_t crc32(const uint8_t *p, size_t n) {
  return crc_update(0xffffffffu, p, n) ^ 0xffffffffu;
}
inline bool crc_contract_self_test() {
  const uint8_t sample[] = "123456789";
  if (crc32(sample, 0) != 0 || crc32(sample, 9) != 0xcbf43926u)
    return false;
  for (size_t split = 0; split <= 9; split++) {
    uint32_t state = crc_update(0xffffffffu, sample, split);
    if ((crc_update(state, sample + split, 9 - split) ^ 0xffffffffu) !=
        0xcbf43926u)
      return false;
  }
  return true;
}
struct Frame {
  uint8_t kind = 1;
  uint32_t session = 0, sequence = 0;
  uint16_t length = 0;
  uint8_t payload[MaxPayload]{};
};
inline size_t encode(const Frame &f, uint8_t *out) {
  if (f.length > MaxPayload)
    return 0;
  uint8_t raw[MaxPayload + 20]{};
  raw[0] = 1;
  raw[1] = f.kind;
  put32(raw + 4, f.session);
  put32(raw + 8, f.sequence);
  put16(raw + 12, f.length);
  std::memcpy(raw + 16, f.payload, f.length);
  size_t n = size_t(f.length) + 20;
  put32(raw + n - 4, crc32(raw, n - 4));
  size_t at = 1, code_at = 0;
  uint8_t code = 1;
  for (size_t i = 0; i < n; i++) {
    if (raw[i] == 0) {
      out[code_at] = code;
      code_at = at++;
      code = 1;
    } else {
      out[at++] = raw[i];
      if (++code == 255) {
        out[code_at] = code;
        code_at = at++;
        code = 1;
      }
    }
  }
  out[code_at] = code;
  out[at++] = 0;
  return at;
}
inline bool decode(const uint8_t *encoded, size_t length, Frame &frame) {
  if (length == 0 || length >= MaxWire)
    return false;
  uint8_t raw[MaxPayload + 20]{};
  size_t at = 0, i = 0;
  while (i < length) {
    size_t code = encoded[i++];
    if (code == 0 || i + code - 1 > length || at + code - 1 > sizeof(raw))
      return false;
    for (size_t j = 1; j < code; j++) {
      if (encoded[i] == 0)
        return false;
      raw[at++] = encoded[i++];
    }
    if (code < 255 && i < length) {
      if (at == sizeof(raw))
        return false;
      raw[at++] = 0;
    }
  }
  if (at < 20 || raw[0] != 1 || get16(raw + 2) != 0 || get16(raw + 14) != 0 ||
      size_t(get16(raw + 12)) + 20 != at ||
      crc32(raw, at - 4) != get32(raw + at - 4) ||
      !(raw[1] == 1 || raw[1] == 2 || raw[1] == 3 || raw[1] == 16 ||
        raw[1] == 17))
    return false;
  frame.kind = raw[1];
  frame.session = get32(raw + 4);
  frame.sequence = get32(raw + 8);
  frame.length = get16(raw + 12);
  std::memcpy(frame.payload, raw + 16, frame.length);
  return true;
}
class WireParser {
  uint8_t data_[MaxWire]{};
  size_t count_ = 0;
  bool discard_ = false;
  uint64_t started_ = 0;

public:
  uint32_t invalid_frames = 0;
  bool feed(uint8_t b, uint64_t now, Frame &out) {
    if (count_ && now - started_ > 2000000) {
      count_ = 0;
      discard_ = false;
      invalid_frames++;
    }
    if (b == 0) {
      bool ok = !discard_ && decode(data_, count_, out);
      if (!ok && (count_ || discard_))
        invalid_frames++;
      count_ = 0;
      discard_ = false;
      return ok;
    }
    if (!count_)
      started_ = now;
    if (count_ + 1 >= MaxWire)
      discard_ = true;
    else if (!discard_)
      data_[count_++] = b;
    return false;
  }
};
} // namespace mvb
