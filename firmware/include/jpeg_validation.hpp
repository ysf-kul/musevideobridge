#pragma once
#include <cstddef>
#include <cstdint>

namespace mvb {
// Production restricts the profile to baseline 4:2:0 tables/components.
// Explicit physical research builds may also accept standard 4:2:2/4:4:4;
// this is not a public version-1 format compatibility expansion.
// Validate selectors, canonical Huffman bounds and entropy marker framing
// before entering the third-party decoder, which assumes structurally valid
// tables.
inline bool supported_luma_sampling(uint8_t sampling) {
#ifdef MVB_RESEARCH_JPEG_CHROMA
  return sampling == 0x22 || sampling == 0x21 || sampling == 0x11;
#else
  return sampling == 0x22;
#endif
}
inline bool baseline_jpeg(const uint8_t *b, size_t length, uint16_t &w,
                          uint16_t &h) {
  if (length < 4 || length > 49152 || b[0] != 255 || b[1] != 0xd8)
    return false;
  size_t at = 2;
  bool sof = false;
  uint8_t quant = 0, huffman = 0, required_quant = 0;
  while (at + 4 <= length) {
    if (b[at++] != 255)
      return false;
    while (at < length && b[at] == 255)
      at++;
    if (at + 3 > length)
      return false;
    uint8_t mark = b[at++];
    size_t n = (size_t(b[at]) << 8) | b[at + 1];
    if (n < 2 || n > length - at)
      return false;
    const uint8_t *p = b + at + 2;
    size_t size = n - 2;
    if (mark == 0xc0) {
      if (sof || size != 15 || p[0] != 8 || p[5] != 3 || p[6] != 1 ||
          !supported_luma_sampling(p[7]) || p[8] > 1 || p[9] != 2 ||
          p[10] != 0x11 || p[11] > 1 || p[12] != 3 || p[13] != 0x11 ||
          p[14] > 1)
        return false;
      h = uint16_t((uint16_t(p[1]) << 8) | p[2]);
      w = uint16_t((uint16_t(p[3]) << 8) | p[4]);
      required_quant = uint8_t((1 << p[8]) | (1 << p[11]) | (1 << p[14]));
      sof = w && w <= 320 && h && h <= 240;
      if (!sof)
        return false;
    } else if (mark == 0xdb) {
      size_t k = 0;
      while (k < size) {
        uint8_t id = p[k++];
        if (id > 1 || size - k < 64)
          return false;
        for (size_t j = 0; j < 64; j++)
          if (!p[k + j])
            return false;
        k += 64;
        quant |= uint8_t(1 << id);
      }
    } else if (mark == 0xc4) {
      size_t k = 0;
      while (k < size) {
        if (size - k < 17)
          return false;
        uint8_t id = p[k++];
        if (id != 0 && id != 1 && id != 0x10 && id != 0x11)
          return false;
        size_t symbols = 0;
        int available = 1;
        for (size_t j = 0; j < 16; j++) {
          available = 2 * available - p[k + j];
          if (available < 0)
            return false;
          symbols += p[k + j];
        }
        k += 16;
        if (!symbols || symbols > 256 || symbols > size - k || available == 0)
          return false;
        for (size_t j = 0; j < symbols; j++) {
          uint8_t v = p[k + j];
          if (id < 16
                  ? v > 11
                  : ((v & 15) > 10 || ((v & 15) == 0 && v != 0 && v != 0xf0)))
            return false;
        }
        k += symbols;
        huffman |= uint8_t(1 << ((id & 1) + ((id & 16) ? 2 : 0)));
      }
    } else if (mark == 0xda) {
      if (!sof || (quant & required_quant) != required_quant || huffman != 15 ||
          size != 10 || p[0] != 3 || p[1] != 1 || p[2] != 0 || p[3] != 2 ||
          p[4] != 0x11 || p[5] != 3 || p[6] != 0x11 || p[7] != 0 ||
          p[8] != 63 || p[9] != 0)
        return false;
      at += n;
      bool entropy = false;
      while (at < length) {
        uint8_t v = b[at++];
        if (v != 255) {
          entropy = true;
          continue;
        }
        while (at < length && b[at] == 255)
          at++;
        if (at >= length)
          return false;
        v = b[at++];
        if (v == 0) {
          entropy = true;
          continue;
        }
        if (v >= 0xd0 && v <= 0xd7)
          continue;
        if (v == 0xd9)
          return entropy && at == length;
        return false;
      }
      return false;
    } else if (mark == 0xdd) {
      if (size != 2)
        return false;
    } else if (!(mark >= 0xe0 && mark <= 0xef) && mark != 0xfe)
      return false;
    at += n;
  }
  return false;
}
} // namespace mvb
