# AppImage runtime attribution, corresponding source and rebuilding

The Muse AppImage uses the unmodified x86_64 AppImage type-2 runtime from commit
`fedfad51acc2860b2916a1eef32ba4a500ff31ef`, SHA-256
`617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46`.
The matching `AppImage-runtime-sources.tar.gz` asset is supplied on the same Muse
release: https://github.com/ysf-kul/musevideobridge/releases/tag/v0.3.0 .

The runtime's own code is MIT licensed. It statically incorporates libfuse 3.15.0
library code (LGPL 2.1), squashfuse 0.5.2 (BSD 2-Clause), musl 1.2.5-r11 (MIT and
retained component notices), zstd 1.5.6-r2 (BSD 3-Clause selected from dual terms),
zlib 1.3.2-r1 (Zlib), and mimalloc 2.1.7-r0 (MIT). GCC 14.2.0 compiler support
notices include the GCC Runtime Library Exception. All original license texts
accompany the AppImage in `licenses/appimage-runtime/` and the source asset.
libfuse's utilities use GPL 2; the bundled runtime links its LGPL library code.
The source asset retains the full original libfuse archive, including those
utilities and their notices. It does not distribute the GPL utilities as binaries.

The source asset contains exact original runtime/library source archives, the
upstream runtime's libfuse `mount.c` patch, all applicable Alpine source patches and
APKBUILD recipes, provenance and checksums. musl, zstd, zlib and mimalloc archives
and patches were verified against the Alpine recipes pinned at commit
`8f01bd5af18e4f66b7df105bdd7cab10de2ee1fa`. The package versions match the
[upstream x86_64 build](https://github.com/AppImage/type2-runtime/actions/runs/39320578745/job/118293253809).
The builder used Alpine 3.21 image digest
`sha256:ce64758a109eb420d874a118f87920e625e12d3634e03b4a5573fd9f6e5d3507`,
clang 19.1.4, GCC 14.2.0 and the configuration preserved in the upstream scripts.
The source and rebuild materials were checked; no local runtime rebuild or
bit-identical reproduction is claimed.

## Rebuild the runtime

Extract the source asset and verify `sha256sum -c SHA256SUMS.txt`. Extract
`archives/type2-runtime.tar.gz` into a working folder. Its `BUILD.md`,
`scripts/docker/Dockerfile`, `scripts/common/install-dependencies.sh`,
`scripts/build-runtime.sh` and runtime Makefile contain the full original build
instructions, compiler/linker flags, and libfuse patch. The runtime links libraries
statically and enables `-static-pie` and section garbage collection.

For an unchanged rebuild, set the source version to the upstream release value,
then run the isolated Docker build from that source checkout:

```sh
printf '%s\n' 'https://github.com/AppImage/type2-runtime/commit/fedfad5' \
  > src/runtime/version
mkdir build-output
cd build-output
env ARCH=x86_64 ../scripts/docker/build-with-docker.sh
```

The resulting `runtime-x86_64` and debug file are written into `build-output`.
Use the pinned Alpine image digest and library package versions in `provenance.json`
when matching the original environment. The upstream Dockerfile's `alpine:3.21`
tag and package repository are mutable; later builds can differ even with identical
runtime source. Compiler/build tools are general-purpose tools installed in the
build container, not required at application runtime.

If a listed Alpine binary package is unavailable, its complete source and patched
APKBUILD are supplied in `archives/` and `alpine-recipes/`. Alpine's `abuild`
toolchain can rebuild these packages from those local inputs. The original
source/patch SHA-512 checksums are preserved in every APKBUILD.

## Relink with a modified libfuse

You may modify and rebuild libfuse and relink the runtime. No signing key or Muse
service is required. Use an isolated Alpine build container with the dependencies
listed in the original Dockerfile. Extract `archives/fuse-3.15.0.tar.xz`, apply
`patches/libfuse/mount.c.diff` from the extracted runtime checkout, then make your
changes to the library source. Build/install your static library in that container:

```sh
patch -p1 < /path/to/type2-runtime/patches/libfuse/mount.c.diff
meson setup build --prefix=/usr --default-library=static
ninja -C build install
```

Build/install squashfuse from the supplied archive using its `autogen.sh`,
`configure LDFLAGS=-static`, `make` and `make install` steps as shown in the
runtime's `install-dependencies.sh`; its headers must also be installed into
`/usr/local/include/squashfuse`. Keep the supplied musl/zstd/zlib/mimalloc sources
and Alpine build recipes for replacing any of those libraries. Run
`scripts/build-runtime.sh` from the runtime checkout **after** installing the
modified libfuse. That relinks the complete MIT runtime source with your modified
library. It also performs the stripping, debug-link and AppImage magic-byte steps.

## Use a replacement runtime with the Muse payload

The application payload can remain intact while replacing the runtime. Obtain the
original payload offset with `./Muse-Video-Bridge-0.3.0-Linux-x86_64.AppImage
--appimage-offset`, then copy every byte after that offset to `payload.squashfs`.
Concatenate your replacement `runtime-x86_64` and that payload:

```sh
cat /path/to/rebuilt/runtime-x86_64 payload.squashfs > Muse-Video-Bridge-relinked.AppImage
chmod +x Muse-Video-Bridge-relinked.AppImage
./Muse-Video-Bridge-relinked.AppImage --appimage-extract-and-run
```

Alternatively, extract the original AppImage with `--appimage-extract`, rebuild its
SquashFS payload with `mksquashfs`, and prepend your replacement runtime. The Muse
application's source and corresponding dependency sources are separately supplied
on the same GitHub release. The runtime-source asset contains the full source of
the work using the statically linked libfuse library, so replacing that library is
possible without proprietary object files or credentials.

To regenerate this source asset, download the inputs listed in
`packaging/collect_appimage_runtime_sources.py`, verify their pinned hashes and run:

```sh
python3 packaging/collect_appimage_runtime_sources.py \
  --inputs /path/to/verified-downloads --output /path/to/new-output
```

GTK, WebKitGTK, libudev and FFmpeg remain system dependencies of the compact Muse
AppImage and are not copied into its runtime or bundled under these notices.
