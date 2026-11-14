#!/usr/bin/env python3
"""Build native firmware tests/simulator; no serial/device/SD-card operations."""
import argparse
import importlib.util
import pathlib
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parents[1]
    dependency = root / "firmware/.pio/libdeps/cyd_ili9341"
    if not (dependency / "JPEGDEC/src/JPEGDEC.cpp").is_file():
        raise SystemExit("Install pinned firmware dependencies with PlatformIO first.")
    spec = importlib.util.spec_from_file_location("jpeg_patch", root / "firmware/patch_jpegdec.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.patch(dependency / "JPEGDEC")
    output = root / ".verification/native"
    output.mkdir(parents=True, exist_ok=True)
    flags = ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-g", "-O1"]
    if args.sanitize:
        flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    common = ["clang++", *flags, "-I", str(root / "firmware/include")]
    subprocess.run([*common, str(root / "firmware/native/core_tests.cpp"), "-o", str(output / "core-tests")], check=True)
    subprocess.run([str(output / "core-tests")], check=True)
    subprocess.run([
        *common, "-DMVB_TEST_TOUCH", "-D__LINUX__", "-DNO_SIMD", "-include", str(root / "firmware/include/jpeg_portable.hpp"),
        "-I", str(dependency / "ArduinoJson/src"), "-I", str(dependency / "JPEGDEC/src"),
        str(root / "firmware/native/simulator.cpp"), str(dependency / "JPEGDEC/src/JPEGDEC.cpp"),
        "-lcrypto", "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-sign-compare",
        "-o", str(output / "mvb-simulator"),
    ], check=True)
    subprocess.run([
        *common, "-D__LINUX__", "-DNO_SIMD", "-include", str(root / "firmware/include/jpeg_portable.hpp"),
        "-I", str(dependency / "ArduinoJson/src"), "-I", str(dependency / "JPEGDEC/src"),
        str(root / "firmware/native/profile_tests.cpp"), str(dependency / "JPEGDEC/src/JPEGDEC.cpp"),
        "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-sign-compare",
        "-o", str(output / "profile-tests"),
    ], check=True)
    print(output / "mvb-simulator")


if __name__ == "__main__":
    main()
