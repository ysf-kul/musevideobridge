"""Version-checked local JPEGDEC portability fixes; upstream Apache-2.0 notices remain."""
import hashlib
from pathlib import Path

MARKER = "// Muse Video Bridge JPEGDEC 1.8.5 portability corrections\n"
HEADER_MARKER = "// Muse Video Bridge JPEGDEC 1.8.5 optional decoder tile buffer\n"
INL_ORIGINAL_SHA256 = "cca8f55bd3ab80d52e7e52790a334134eda903ecbcfe933b2a3dc87f670f588a"
INL_PATCHED_SHA256 = "4900f6e91cf8c348da3a7e451be31b4a2201739199e7766d768c16b6a035de98"
HEADER_ORIGINAL_SHA256 = "8275205e4c5dfc63549ea86a7fed0011baebd2007de920ae29b646909105389f"
HEADER_PATCHED_SHA256 = "09193a59f977ffcbdfa803b4156903d0aeacfb056134bf5d3fa41d73a775a752"
INL_EDITS = {
    "if ((cc >> (iBitNum-5)) == 0x1f)": ("if (iBitNum >= 5 && (cc >> (iBitNum-5)) == 0x1f)", 2),
    "if ((cc >> (iBitNum-6)) == 0x3f)": ("if (iBitNum >= 6 && (cc >> (iBitNum-6)) == 0x3f)", 1),
    "memcpy(pPage->ucFileBuf,": ("memmove(pPage->ucFileBuf,", 2),
    "((-1) << pJPEG->cApproxBitsLow)": ("(-int(uint32_t(1) << pJPEG->cApproxBitsLow))", 1),
}
HEADER_OLD = "#define MAX_BUFFERED_PIXELS 2048"
HEADER_NEW = """#ifndef MVB_JPEG_MAX_BUFFERED_PIXELS
#define MVB_JPEG_MAX_BUFFERED_PIXELS 2048
#endif
#if MVB_JPEG_MAX_BUFFERED_PIXELS != 2048 && MVB_JPEG_MAX_BUFFERED_PIXELS != 5120
#error "Muse JPEG decoder buffer must be 2048 or 5120 pixels"
#endif
#define MAX_BUFFERED_PIXELS MVB_JPEG_MAX_BUFFERED_PIXELS"""


def checked_source(path, marker, original_sha256, patched_sha256):
    raw = path.read_bytes()
    source = raw.decode("utf-8")
    patched = source.startswith(marker)
    expected = patched_sha256 if patched else original_sha256
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RuntimeError(f"JPEGDEC {path.name} source changed; review required")
    return source, not patched


def patch(directory):
    path = Path(directory) / "src/jpeg.inl"
    header = Path(directory) / "src/JPEGDEC.h"
    # Validate both original/patched inputs before modifying either file.
    source, patch_inl = checked_source(path, MARKER, INL_ORIGINAL_SHA256, INL_PATCHED_SHA256)
    declarations, patch_header = checked_source(
        header, HEADER_MARKER, HEADER_ORIGINAL_SHA256, HEADER_PATCHED_SHA256)
    if patch_inl:
        for old, (new, count) in INL_EDITS.items():
            if source.count(old) != count:
                raise RuntimeError("JPEGDEC patch pattern mismatch")
            source = source.replace(old, new)
        source = MARKER + source
    if patch_header:
        if declarations.count(HEADER_OLD) != 1:
            raise RuntimeError("JPEGDEC header patch pattern mismatch")
        declarations = HEADER_MARKER + declarations.replace(HEADER_OLD, HEADER_NEW)
    if (hashlib.sha256(source.encode()).hexdigest() != INL_PATCHED_SHA256 or
            hashlib.sha256(declarations.encode()).hexdigest() != HEADER_PATCHED_SHA256):
        raise RuntimeError("JPEGDEC generated patch changed; review required")
    if patch_inl:
        path.write_text(source, newline="\n")
    if patch_header:
        header.write_text(declarations, newline="\n")


if "Import" in globals():
    Import("env")
    patch(Path(env.subst("$PROJECT_LIBDEPS_DIR")) / env.subst("$PIOENV") / "JPEGDEC")
