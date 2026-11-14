# Indexed JPEG container proposal

Status: **draft for experiments, not a finalized interoperable format**. Extension
proposal `.mvb`, magic `MVBJPG1\0`. This is independent of the older project's `.mvj`
and MVB1 serial protocol; no compatibility is claimed.

## AVI versus custom container

Both carry the same independently decodable baseline JPEG payloads. Decoder CPU cost
does not improve merely because the file extension changes.

| Consideration | Standard MJPEG AVI | Custom indexed JPEG proposal |
| --- | --- | --- |
| Creation / tools | FFmpeg writes it directly; general players can inspect it | Requires a small writer/parser and a preview adapter |
| Sequential reads | `movi` RIFF chunks, padding, other chunk/list types | One bounded record after another |
| Seeking | `idx1` or OpenDML indices; offsets and timebase require careful handling | Fixed-size absolute offset/PTS entries; page-cache arithmetic |
| Memory | Can stream with a small parser; whole AVI index is not mandatory | Can stream with a small parser; index never fully loaded |
| Error checks | RIFF/index validation; no mandatory per-frame payload checksum | Header/index/frame CRC plus transfer SHA-256 |
| Long files | OpenDML and multiple RIFF regions add complexity | Conservative 2 GiB file cap; fixed-width checked offsets |
| Storage size | Low overhead relative to JPEG data | Comparable; metadata/index can make long files slightly larger |
| Risk | Mature desktop tooling, more embedded parser cases | Simpler embedded scope, new-format testing burden |

The 12 host samples measured AVI size and **modeled** this draft's size for identical
JPEG packets. On four-second clips, modeled files were roughly 4.6–5.0 kB smaller
due to AVI's fixed headers; this is not a general size advantage. This draft uses
about 40–43 overhead bytes/frame, versus a simple AVI's roughly 24 bytes/frame plus
fixed headers. On long videos it may be larger. No SD latency benchmark was run.

Recommend trialing this custom format because of explicit bounds, integrity and seek
semantics, with **MJPEG AVI as a real benchmark/fallback candidate**. Finalize only
after both parsers read the same JPEGs on the actual card/display and are compared
for sequential throughput, seek latency, heap, correctness and sustained FPS. Thousands
of SD JPEG files add directory/FAT/open overhead and complicate export; they are not
the default. Their disadvantage is reasoned here, not a measured benchmark claim.

## Draft v1 layout

All fields are **little-endian**, except the standard JPEG bitstream. All reserved
fields/padding are zero. Header and index field decoding is explicit; never map raw
bytes to an unpacked C++ struct. Use checked 64-bit arithmetic even though v1 files
are limited to **2,147,483,647 bytes** and firmware seek APIs may take 32-bit offsets.

```text
96-byte header
N × [16-byte frame header, JPEG payload, 0–3 zero bytes to 4-byte alignment]
N × 24-byte index entry
```

The index is at the end so conversion can stream. The first data record starts at
offset 96. Startup seeks to the index for validation then to data; steady playback
reads records sequentially. SD sectors are larger than four bytes, but aligning every
small frame to 512 bytes wastes storage; use buffered sector reads instead.

### Header — 96 bytes

| Offset | Type | Field / validation |
| ---: | --- | --- |
| 0 | byte[8] | Magic: ASCII `MVBJPG1` followed by NUL |
| 8 | u16 | Version = 1 |
| 10 | u16 | Header bytes = 96 |
| 12 | u32 | Flags = 0; v1 is CFR, baseline JPEG, no audio |
| 16 | u16 | Encoded width, 1…320 |
| 18 | u16 | Encoded height, 1…240 |
| 20 | u32 | FPS numerator, 1…30000 |
| 24 | u32 | FPS denominator, 1…1001; rational rate 1…30 FPS |
| 28 | u32 | Frame count, 1…1,000,000 and constrained by file bounds |
| 32 | u64 | Duration µs = floor(N × 1,000,000 × fps_den / fps_num) |
| 40 | u64 | Data offset = 96 |
| 48 | u64 | Index offset = immediately after last padded frame record |
| 56 | u64 | Exact file size; must match filesystem size |
| 64 | u32 | Maximum JPEG bytes across records, ≤49,152 for initial profile |
| 68 | u16 | Index entry bytes = 24 |
| 70 | u16 | Frame header bytes = 16 |
| 72 | u32 | CRC32 of entire index table |
| 76 | u32 | CRC32 of 96-byte header with this field zeroed |
| 80 | byte[16] | Reserved, all zero |

CRC32 means CRC-32/ISO-HDLC: reflected polynomial `0xEDB88320`, initial/final XOR
`0xFFFFFFFF`, check value for ASCII `123456789` = `0xCBF43926`. CRC detects accidental
corruption, not hostile tampering. Full-container SHA-256 is recorded externally to
identify media and verify copies; no circular self-hash is embedded in the file.

### Frame record — 16-byte header plus JPEG and padding

| Relative offset | Type | Field |
| ---: | --- | --- |
| 0 | u32 | JPEG payload length |
| 4 | u32 | Zero-based frame index |
| 8 | u32 | CRC32 of exactly the JPEG bytes |
| 12 | u32 | Flags = 0 |
| 16 | byte[length] | Complete independent baseline JPEG, SOI through EOI |
| after payload | byte[0…3] | Zero padding to align next record to four bytes |

Each frame supplies its own quantization/Huffman tables. No dependency on the prior
frame's tables or decoder state. Width/height/precision/subsampling match the header
and profile. Reject progressive, arithmetic, CMYK, unsupported scans, oversized
segments and dimensions before allocating/drawing. JPEG payload view reads/seeks
are confined to this record; JPEGDEC cannot run into the next frame or the index.

### Index entry — 24 bytes

| Relative offset | Type | Field |
| ---: | --- | --- |
| 0 | u64 | Absolute offset of the corresponding frame header |
| 8 | u64 | PTS µs = floor(i × 1,000,000 × fps_den / fps_num) |
| 16 | u32 | JPEG length, equal to frame record |
| 20 | u32 | JPEG CRC32, equal to frame record |

Seek target index can be calculated from the CFR rate using a checked integer ceil,
then its entry is read directly. A 64-entry cache is **1,536 bytes**. Sequential
playback does not fetch an index entry for every frame. An hour at 15 FPS has 54,000
entries (~1.24 MiB on SD), which must never become a full in-memory table.

## Validation, CRC and timing behavior

Desktop validation checks the entire index/record sequence and decodes all frames
before publication. Firmware validates header, file-size arithmetic and the index
CRC/bounds in small pages when opening, then validates each record/JPEG in playback.
Index validation is measurable startup work with an explicit loading state. Recheck
length/CRC against the referenced record on seek; reject unsupported version/flags.

A streaming decoder can calculate payload CRC as it reads, but full corruption can
only be known after the bytes are consumed. Partial image output may already have
occurred. To guarantee integrity **before drawing**, use a bounded full compressed
frame buffer or a two-pass CRC/read (costs SD bandwidth), and measure that mode.
JPEGDEC may seek or reread within its bounded frame view: CRC must cover each
payload byte exactly once in file order, not blindly every decoder callback. A
separate sequential verification pass is the simplest baseline when rereads occur.
The default must never claim pre-draw verification if it only checks CRC afterwards.
Failure stops playback, clears the affected region and emits a precise frame error.

PTS stores media time; the runtime target FPS caps sampling of that timeline. At
15 FPS, use exact rational computation rather than repeatedly adding 66,667 µs,
which accumulates drift. Last frame duration comes from total duration minus its PTS.
Seeking, pause/resume and loop rules are defined in [ARCHITECTURE.md](ARCHITECTURE.md).

## Format finalization gates

- Same payload set in valid AVI and draft custom files; compare actual SD performance.
- Validate truncated header/index/payload, CRC mismatch, malicious lengths, duplicate/
  backward offsets, bad timing, seek boundaries and large-file arithmetic natively.
- Verify firmware/JPEGDEC against FFmpeg-produced tables and sampling on actual hardware.
- Compare cached-index startup/seek at 1 minute, 10 minutes and near the size limit.
- Confirm the file-size/seek bound of the pinned storage API and measured heap budget.
- Freeze a v1 specification and commit golden binary fixtures only after these gates;
  draft artifacts must not become an undocumented compatibility promise.
