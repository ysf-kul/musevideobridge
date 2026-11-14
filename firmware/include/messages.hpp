#pragma once
namespace mvb {
namespace messages {
enum class Key {
  Title,
  Waiting,
  Instructions,
  SdReady,
  SdMissing,
  Connected,
  Disconnected,
  Error
};
// The explicit Phase 2 requirement fixes the device waiting-screen locale to
// English. All screen copy is keyed here; desktop catalogs retain Turkish as
// their default.
inline const char *english(Key key) {
  switch (key) {
  case Key::Title:
    return "Muse Video Bridge";
  case Key::Waiting:
    return "Waiting for video...";
  case Key::Instructions:
    return "Insert SD card or send a video from your computer.";
  case Key::SdReady:
    return "SD card: ready";
  case Key::SdMissing:
    return "SD card: unavailable";
  case Key::Connected:
    return "Computer: connected";
  case Key::Disconnected:
    return "Computer: disconnected";
  default:
    return "Playback error";
  }
}
} // namespace messages
} // namespace mvb
