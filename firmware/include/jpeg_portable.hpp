#pragma once
#ifdef __cplusplus
#include <cstdint>
#include <cstring>
template <class T>
#if defined(ARDUINO_ARCH_ESP32)
__attribute__((always_inline))
#endif
inline T mvb_word(const void *p) {
#if defined(ARDUINO_ARCH_ESP32)
  // Xtensa keeps even constant-size memcpy calls for unaligned word reads.
  // Assemble bytes safely; the fixed-size loop becomes inline byte loads.
  const auto *bytes = static_cast<const uint8_t *>(p);
  T n = 0;
  for (unsigned i = 0; i < sizeof(T); ++i)
    n |= T(bytes[i]) << (8 * i);
  return n;
#else
  T n;
  std::memcpy(&n, p, sizeof(n));
  return n;
#endif
}
#define INTELSHORT(p) mvb_word<uint16_t>(p)
#define INTELLONG(p) mvb_word<uint32_t>(p)
#if defined(ARDUINO_ARCH_ESP32)
template <class T>
__attribute__((always_inline)) inline T mvb_big_word(const void *p) {
  const auto *bytes = static_cast<const uint8_t *>(p);
  T n = 0;
  for (unsigned i = 0; i < sizeof(T); ++i)
    n = (n << 8) | bytes[i];
  return n;
}
#define MOTOSHORT(p) mvb_big_word<uint16_t>(p)
#define MOTOLONG(p) mvb_big_word<uint32_t>(p)
#else
#define MOTOSHORT(p) __builtin_bswap16(mvb_word<uint16_t>(p))
#if defined(__x86_64__) || defined(__aarch64__)
#define MOTOLONG(p) __builtin_bswap64(mvb_word<uint64_t>(p))
#else
#define MOTOLONG(p) __builtin_bswap32(mvb_word<uint32_t>(p))
#endif
#endif
#endif
