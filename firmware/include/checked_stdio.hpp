#pragma once
#include <cstdio>
#if defined(_WIN32)
#include <io.h>
#else
#include <unistd.h>
#endif

namespace mvb {
// Check both the stdio buffer and underlying filesystem synchronization.
// Arduino's File.flush() exposes neither operation's return status.
inline bool sync_file(FILE *file) {
  if (!file || std::ferror(file) || std::fflush(file) != 0)
    return false;
#if defined(_WIN32)
  return ::_commit(::_fileno(file)) == 0;
#else
  return ::fsync(::fileno(file)) == 0;
#endif
}
} // namespace mvb
