use crate::{Error, Result};
use std::io::Read;

pub const MAX_JPEG: usize = 49_152;
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Dimensions {
    pub width: u16,
    pub height: u16,
}

fn byte<R: Read>(r: &mut R, output: &mut Vec<u8>) -> Result<u8> {
    if output.len() >= MAX_JPEG {
        return Err(Error::new("frame_too_large", MAX_JPEG));
    }
    let mut b = [0];
    r.read_exact(&mut b)?;
    output.push(b[0]);
    Ok(b[0])
}

/// Segment-aware baseline JPEG stream framing, including stuffed entropy/restarts.
pub fn next_frame<R: Read>(r: &mut R) -> Result<Option<Vec<u8>>> {
    let mut start = [0];
    loop {
        match r.read(&mut start) {
            Ok(0) => return Ok(None),
            Ok(_) => break,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e.into()),
        }
    }
    let mut out = vec![start[0]];
    if start[0] != 255 || byte(r, &mut out)? != 0xd8 {
        return Err(Error::new("unsupported_jpeg", "Missing SOI"));
    }
    loop {
        if byte(r, &mut out)? != 255 {
            return Err(Error::new("unsupported_jpeg", "Bad marker boundary"));
        }
        let mut marker = byte(r, &mut out)?;
        while marker == 255 {
            marker = byte(r, &mut out)?;
        }
        if marker == 0xd9 {
            return Err(Error::new("unsupported_jpeg", "No scan"));
        }
        if marker == 0 || marker == 0xd8 || (0xd0..=0xd7).contains(&marker) {
            return Err(Error::new("unsupported_jpeg", "Unexpected marker"));
        }
        let hi = byte(r, &mut out)?;
        let lo = byte(r, &mut out)?;
        let length = u16::from_be_bytes([hi, lo]) as usize;
        if length < 2 || out.len() + length - 2 > MAX_JPEG {
            return Err(Error::new("frame_too_large", "Invalid/oversized segment"));
        }
        for _ in 2..length {
            byte(r, &mut out)?;
        }
        if marker == 0xda {
            break;
        }
    }
    loop {
        if byte(r, &mut out)? != 255 {
            continue;
        }
        let mut marker = byte(r, &mut out)?;
        while marker == 255 {
            marker = byte(r, &mut out)?;
        }
        match marker {
            0 | 0xd0..=0xd7 => {}
            0xd9 => {
                validate(&out)?;
                return Ok(Some(out));
            }
            _ => return Err(Error::new("unsupported_jpeg", "Multiple/nonbaseline scans")),
        }
    }
}

pub fn validate(data: &[u8]) -> Result<Dimensions> {
    let bad = || {
        Error::new(
            "unsupported_jpeg",
            if cfg!(feature = "research-jpeg-chroma") {
                "Requires complete 8-bit baseline YCbCr research JPEG with standard sampling"
            } else {
                "Requires complete 8-bit baseline YCbCr 4:2:0 JPEG"
            },
        )
    };
    if data.len() > MAX_JPEG {
        return Err(Error::new("frame_too_large", data.len()));
    }
    if !data.starts_with(&[255, 0xd8]) || !data.ends_with(&[255, 0xd9]) {
        return Err(bad());
    }
    let mut i = 2;
    let mut dimensions = None;
    let mut quant = 0u8;
    let mut required_quant = 0u8;
    let mut huffman = 0u8;
    while i < data.len() - 2 {
        if data[i] != 255 {
            return Err(bad());
        }
        while i < data.len() && data[i] == 255 {
            i += 1;
        }
        let marker = *data.get(i).ok_or_else(bad)?;
        i += 1;
        let len = data.get(i..i + 2).ok_or_else(bad)?;
        let len = u16::from_be_bytes([len[0], len[1]]) as usize;
        if len < 2 {
            return Err(bad());
        }
        let end = i.checked_add(len).ok_or_else(bad)?;
        let segment = data.get(i + 2..end).ok_or_else(bad)?;
        match marker {
            0xc0 => {
                if dimensions.is_some()
                    || segment.len() != 15
                    || segment[0] != 8
                    || segment[5] != 3
                    || segment[6] != 1
                    || !supported_luma_sampling(segment[7])
                    || segment[8] > 1
                    || segment[9] != 2
                    || segment[10] != 0x11
                    || segment[11] > 1
                    || segment[12] != 3
                    || segment[13] != 0x11
                    || segment[14] > 1
                {
                    return Err(bad());
                }
                let d = Dimensions {
                    width: u16::from_be_bytes([segment[3], segment[4]]),
                    height: u16::from_be_bytes([segment[1], segment[2]]),
                };
                if d.width == 0 || d.width > 320 || d.height == 0 || d.height > 240 {
                    return Err(bad());
                }
                required_quant = (1 << segment[8]) | (1 << segment[11]) | (1 << segment[14]);
                dimensions = Some(d);
            }
            0xc4 => {
                let mut k = 0;
                while k < segment.len() {
                    if segment.len() - k < 17 {
                        return Err(bad());
                    }
                    let id = segment[k];
                    k += 1;
                    if ![0, 1, 0x10, 0x11].contains(&id) {
                        return Err(bad());
                    }
                    let mut available = 1i32;
                    let mut symbols = 0usize;
                    for n in &segment[k..k + 16] {
                        available = 2 * available - i32::from(*n);
                        symbols += usize::from(*n);
                        if available < 0 {
                            return Err(bad());
                        }
                    }
                    k += 16;
                    if symbols == 0
                        || symbols > 256
                        || symbols > segment.len() - k
                        || available == 0
                    {
                        return Err(bad());
                    }
                    for v in &segment[k..k + symbols] {
                        if if id < 16 {
                            *v > 11
                        } else {
                            (v & 15) > 10 || ((v & 15) == 0 && *v != 0 && *v != 0xf0)
                        } {
                            return Err(bad());
                        }
                    }
                    k += symbols;
                    huffman |= 1 << ((id & 1) + if id & 16 != 0 { 2 } else { 0 });
                }
            }
            0xdb => {
                let mut k = 0;
                while k < segment.len() {
                    let id = segment[k];
                    k += 1;
                    if id > 1 || segment.len() - k < 64 || segment[k..k + 64].contains(&0) {
                        return Err(bad());
                    }
                    k += 64;
                    quant |= 1 << id;
                }
            }
            0xda => {
                if quant & required_quant != required_quant
                    || huffman != 15
                    || segment.len() != 10
                    || segment[0] != 3
                    || segment[..] != [3, 1, 0, 2, 0x11, 3, 0x11, 0, 63, 0]
                {
                    return Err(bad());
                }
                let mut at = end;
                let mut entropy = false;
                while at < data.len() {
                    let mut v = data[at];
                    at += 1;
                    if v != 255 {
                        entropy = true;
                        continue;
                    }
                    while at < data.len() && data[at] == 255 {
                        at += 1;
                    }
                    v = *data.get(at).ok_or_else(bad)?;
                    at += 1;
                    match v {
                        0 => entropy = true,
                        0xd0..=0xd7 => {}
                        0xd9 if entropy && at == data.len() => return dimensions.ok_or_else(bad),
                        _ => return Err(bad()),
                    }
                }
                return Err(bad());
            }
            0xdd if segment.len() == 2 => {}
            0xe0..=0xef | 0xfe => {}
            _ => return Err(bad()),
        }
        i = end;
    }
    Err(bad())
}

fn supported_luma_sampling(sampling: u8) -> bool {
    sampling == 0x22 || (cfg!(feature = "research-jpeg-chroma") && matches!(sampling, 0x11 | 0x21))
}

#[cfg(test)]
mod research_chroma_tests {
    use super::*;

    const SAMPLES: [&[u8]; 3] = [
        include_bytes!("../../../tests/fixtures/tjpgd_fast/edge-2x2.jpg"),
        include_bytes!("../../../tests/fixtures/tjpgd_fast/edge-2x1.jpg"),
        include_bytes!("../../../tests/fixtures/tjpgd_fast/edge-1x1.jpg"),
    ];

    fn marker(data: &[u8], expected: u8) -> usize {
        let mut at = 2;
        while data[at + 1] != expected {
            at += 2 + usize::from(u16::from_be_bytes([data[at + 2], data[at + 3]]));
        }
        at
    }

    #[test]
    fn standard_chroma_requires_explicit_research_feature() {
        for (index, sample) in SAMPLES.iter().enumerate() {
            let expected = index == 0 || cfg!(feature = "research-jpeg-chroma");
            assert_eq!(validate(sample).is_ok(), expected);
            assert_eq!(next_frame(&mut &sample[..]).is_ok(), expected);
        }
        // FFmpeg's equal 1x2 components are not standard 4:4:4 for these codecs.
        let unsupported =
            include_bytes!("../../../tests/fixtures/tjpgd_fast/unsupported-ffmpeg-444.jpg");
        assert!(validate(unsupported).is_err());
    }

    #[test]
    fn research_sampling_keeps_component_table_and_entropy_guards() {
        for sample in SAMPLES {
            let sof = marker(sample, 0xc0);
            let dqt = marker(sample, 0xdb);
            let dht = marker(sample, 0xc4);
            let sos = marker(sample, 0xda);
            for (at, value) in [
                (sof + 10, 4),    // Component ID.
                (sof + 11, 0x12), // Unsupported luminance sampling.
                (sof + 12, 2),    // Quantization selector.
                (sof + 14, 0x21), // Chroma must remain 1x1.
                (sof + 17, 0x12),
                (dqt + 4, 0x10), // Only 8-bit quantization tables.
                (dqt + 5, 0),
                (dht + 4, 2),    // Only existing Huffman selectors.
                (dht + 5, 255),  // Oversubscribed canonical codes.
                (sos + 6, 0x10), // Scan selector consistency.
                (sos + 12, 62),  // Complete baseline scan.
            ] {
                let mut damaged = sample.to_vec();
                damaged[at] = value;
                assert!(validate(&damaged).is_err(), "accepted mutation at {at}");
            }
            let mut damaged = sample.to_vec();
            damaged.splice(damaged.len() - 2..damaged.len() - 2, [255, 0xc0]);
            assert!(validate(&damaged).is_err());
            assert!(validate(&sample[..sample.len() - 1]).is_err());
        }
    }
}
