#pragma once
#include <cstdint>
namespace mvb::theme {
constexpr uint16_t rgb565(uint32_t rgb) {
  return uint16_t(((rgb >> 19) & 31) << 11 | ((rgb >> 10) & 63) << 5 |
                  ((rgb >> 3) & 31));
}
// Existing Muse Downloader dark Material 3 roles, reduced to the TFT palette.
constexpr uint16_t surface = rgb565(0x1a221d), on_surface = rgb565(0xe3e9e0),
                   primary = rgb565(0x90d5c4);
constexpr int margin = 16, title_y = 22, message_y = 67, instructions_y = 108,
              sd_y = 163, connection_y = 184, error_y = 209;
} // namespace mvb::theme
