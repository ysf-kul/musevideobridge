use crate::{jpeg, Cancellation, Error, Result};
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};

pub const MAX_FILE: u64 = 2_147_483_647;
pub const HEADER_SIZE: u64 = 96;
const INDEX_SIZE: u64 = 24;
pub(crate) fn u16le(b: &[u8], o: usize) -> u16 {
    u16::from_le_bytes(b[o..o + 2].try_into().unwrap())
}
pub(crate) fn u32le(b: &[u8], o: usize) -> u32 {
    u32::from_le_bytes(b[o..o + 4].try_into().unwrap())
}
pub(crate) fn u64le(b: &[u8], o: usize) -> u64 {
    u64::from_le_bytes(b[o..o + 8].try_into().unwrap())
}
fn put16(b: &mut [u8], o: usize, n: u16) {
    b[o..o + 2].copy_from_slice(&n.to_le_bytes());
}
fn put32(b: &mut [u8], o: usize, n: u32) {
    b[o..o + 4].copy_from_slice(&n.to_le_bytes());
}
fn put64(b: &mut [u8], o: usize, n: u64) {
    b[o..o + 8].copy_from_slice(&n.to_le_bytes());
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub struct Rate {
    pub numerator: u32,
    pub denominator: u32,
}
impl Rate {
    pub fn new(numerator: u32, denominator: u32) -> Result<Self> {
        if numerator == 0
            || numerator > 30000
            || denominator == 0
            || denominator > 1001
            || numerator < denominator
            || u64::from(numerator) > u64::from(denominator) * 30
        {
            return Err(Error::new(
                "invalid_parameter",
                "FPS must be rational 1..30",
            ));
        }
        Ok(Self {
            numerator,
            denominator,
        })
    }
    pub fn pts(self, i: u64) -> u64 {
        (u128::from(i) * 1_000_000 * u128::from(self.denominator)
            / u128::from(self.numerator.max(1)))
        .min(u128::from(u64::MAX)) as u64
    }
    pub fn index_at_or_after(self, us: u64, count: u32) -> u32 {
        let denominator = 1_000_000 * u64::from(self.denominator);
        let index =
            (u128::from(us) * u128::from(self.numerator)).div_ceil(u128::from(denominator.max(1)));
        index.min(u128::from(count.saturating_sub(1))) as u32
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Header {
    pub width: u16,
    pub height: u16,
    pub fps: Rate,
    pub frames: u32,
    pub duration_us: u64,
    pub index_offset: u64,
    pub file_bytes: u64,
    pub max_jpeg_bytes: u32,
    pub index_crc: u32,
}
impl Header {
    pub fn encode(&self) -> [u8; 96] {
        let mut b = [0; 96];
        b[..8].copy_from_slice(b"MVBJPG1\0");
        put16(&mut b, 8, 1);
        put16(&mut b, 10, 96);
        put16(&mut b, 16, self.width);
        put16(&mut b, 18, self.height);
        put32(&mut b, 20, self.fps.numerator);
        put32(&mut b, 24, self.fps.denominator);
        put32(&mut b, 28, self.frames);
        put64(&mut b, 32, self.duration_us);
        put64(&mut b, 40, 96);
        put64(&mut b, 48, self.index_offset);
        put64(&mut b, 56, self.file_bytes);
        put32(&mut b, 64, self.max_jpeg_bytes);
        put16(&mut b, 68, 24);
        put16(&mut b, 70, 16);
        put32(&mut b, 72, self.index_crc);
        let crc = crc32fast::hash(&b);
        put32(&mut b, 76, crc);
        b
    }
    pub fn decode(mut b: [u8; 96], actual_size: u64) -> Result<Self> {
        let bad = || Error::new("invalid_container", "Header bounds/version/integrity");
        let crc = u32le(&b, 76);
        put32(&mut b, 76, 0);
        if &b[..8] != b"MVBJPG1\0"
            || u16le(&b, 8) != 1
            || u16le(&b, 10) != 96
            || u32le(&b, 12) != 0
            || u64le(&b, 40) != 96
            || u16le(&b, 68) != 24
            || u16le(&b, 70) != 16
            || b[80..].iter().any(|x| *x != 0)
            || crc32fast::hash(&b) != crc
        {
            return Err(bad());
        }
        let h = Self {
            width: u16le(&b, 16),
            height: u16le(&b, 18),
            fps: Rate::new(u32le(&b, 20), u32le(&b, 24))?,
            frames: u32le(&b, 28),
            duration_us: u64le(&b, 32),
            index_offset: u64le(&b, 48),
            file_bytes: u64le(&b, 56),
            max_jpeg_bytes: u32le(&b, 64),
            index_crc: u32le(&b, 72),
        };
        if h.width == 0
            || h.width > 320
            || h.height == 0
            || h.height > 240
            || h.frames == 0
            || h.frames > 1_000_000
            || h.max_jpeg_bytes == 0
            || h.max_jpeg_bytes > jpeg::MAX_JPEG as u32
            || actual_size > MAX_FILE
            || h.file_bytes != actual_size
            || h.duration_us != h.fps.pts(u64::from(h.frames))
            || h.index_offset < 96
            || h.index_offset > actual_size
            || h.index_offset + u64::from(h.frames) * INDEX_SIZE != actual_size
        {
            return Err(bad());
        }
        Ok(h)
    }
}
#[derive(Debug, Clone, Copy)]
pub struct Entry {
    pub offset: u64,
    pub pts_us: u64,
    pub bytes: u32,
    pub crc: u32,
}
impl Entry {
    fn encode(self) -> [u8; 24] {
        let mut b = [0; 24];
        put64(&mut b, 0, self.offset);
        put64(&mut b, 8, self.pts_us);
        put32(&mut b, 16, self.bytes);
        put32(&mut b, 20, self.crc);
        b
    }
    fn decode(b: &[u8; 24]) -> Self {
        Self {
            offset: u64le(b, 0),
            pts_us: u64le(b, 8),
            bytes: u32le(b, 16),
            crc: u32le(b, 20),
        }
    }
    fn next(self) -> u64 {
        (self.offset + 16 + u64::from(self.bytes) + 3) & !3
    }
}

pub struct Video<R: Read + Seek> {
    pub header: Header,
    reader: R,
}
impl<R: Read + Seek> Video<R> {
    pub fn open(mut reader: R, cancel: &Cancellation) -> Result<Self> {
        let size = reader.seek(SeekFrom::End(0))?;
        reader.seek(SeekFrom::Start(0))?;
        let mut b = [0; 96];
        reader.read_exact(&mut b)?;
        let header = Header::decode(b, size)?;
        reader.seek(SeekFrom::Start(header.index_offset))?;
        let mut hash = crc32fast::Hasher::new();
        let mut expected = 96;
        for i in 0..header.frames {
            cancel.check()?;
            let mut b = [0; 24];
            reader.read_exact(&mut b)?;
            hash.update(&b);
            let e = Entry::decode(&b);
            if e.offset != expected
                || e.bytes == 0
                || e.bytes > header.max_jpeg_bytes
                || e.pts_us != header.fps.pts(u64::from(i))
                || e.next() > header.index_offset
            {
                return Err(Error::new("invalid_container", "Index bounds/timing"));
            }
            expected = e.next();
        }
        if expected != header.index_offset || hash.finalize() != header.index_crc {
            return Err(Error::new("crc_mismatch", "Index"));
        }
        Ok(Self { header, reader })
    }
    pub fn entry(&mut self, i: u32) -> Result<Entry> {
        if i >= self.header.frames {
            return Err(Error::new("invalid_parameter", "Frame out of range"));
        }
        self.reader.seek(SeekFrom::Start(
            self.header.index_offset + u64::from(i) * 24,
        ))?;
        let mut b = [0; 24];
        self.reader.read_exact(&mut b)?;
        let e = Entry::decode(&b);
        if e.bytes == 0
            || e.bytes > self.header.max_jpeg_bytes
            || e.offset < 96
            || e.offset >= self.header.index_offset
            || !e.offset.is_multiple_of(4)
            || e.pts_us != self.header.fps.pts(u64::from(i))
            || e.next() > self.header.index_offset
        {
            return Err(Error::new(
                "invalid_container",
                "Index changed/out of bounds",
            ));
        }
        Ok(e)
    }
    pub fn frame(&mut self, i: u32) -> Result<Vec<u8>> {
        let e = self.entry(i)?;
        self.reader.seek(SeekFrom::Start(e.offset))?;
        self.read_record(i, e)
    }
    fn read_record(&mut self, i: u32, e: Entry) -> Result<Vec<u8>> {
        let mut b = [0; 16];
        self.reader.read_exact(&mut b)?;
        if u32le(&b, 0) != e.bytes
            || u32le(&b, 4) != i
            || u32le(&b, 8) != e.crc
            || u32le(&b, 12) != 0
        {
            return Err(Error::new("invalid_container", "Frame header"));
        }
        let mut data = vec![0; e.bytes as usize];
        self.reader.read_exact(&mut data)?;
        if crc32fast::hash(&data) != e.crc {
            return Err(Error::new("crc_mismatch", format!("Frame {i}")));
        }
        let dims = jpeg::validate(&data)?;
        if dims.width != self.header.width || dims.height != self.header.height {
            return Err(Error::new("invalid_container", "JPEG dimensions"));
        }
        let padding = (4 - data.len() % 4) % 4;
        let mut p = [0; 3];
        self.reader.read_exact(&mut p[..padding])?;
        if p[..padding].iter().any(|x| *x != 0) {
            return Err(Error::new("invalid_container", "Nonzero padding"));
        }
        Ok(data)
    }
    pub fn verify(&mut self, cancel: &Cancellation) -> Result<()> {
        for i in 0..self.header.frames {
            cancel.check()?;
            self.frame(i)?;
        }
        Ok(())
    }
}

pub struct Writer {
    file: File,
    index: File,
    header: Header,
}
impl Writer {
    pub fn new(mut file: File, index: File, fps: Rate) -> Result<Self> {
        Rate::new(fps.numerator, fps.denominator)?;
        file.write_all(&[0; 96])?;
        Ok(Self {
            file,
            index,
            header: Header {
                width: 0,
                height: 0,
                fps,
                frames: 0,
                duration_us: 0,
                index_offset: 0,
                file_bytes: 0,
                max_jpeg_bytes: 0,
                index_crc: 0,
            },
        })
    }
    pub fn push(&mut self, data: &[u8]) -> Result<()> {
        let d = jpeg::validate(data)?;
        if self.header.frames == 0 {
            self.header.width = d.width;
            self.header.height = d.height;
        }
        if d.width != self.header.width
            || d.height != self.header.height
            || self.header.frames >= 1_000_000
        {
            return Err(Error::new(
                "invalid_container",
                "Changing dimensions/too many frames",
            ));
        }
        let e = Entry {
            offset: self.file.stream_position()?,
            pts_us: self.header.fps.pts(u64::from(self.header.frames)),
            bytes: data.len() as u32,
            crc: crc32fast::hash(data),
        };
        if e.next() + u64::from(self.header.frames + 1) * 24 > MAX_FILE {
            return Err(Error::new("file_too_large", MAX_FILE));
        }
        let mut b = [0; 16];
        put32(&mut b, 0, e.bytes);
        put32(&mut b, 4, self.header.frames);
        put32(&mut b, 8, e.crc);
        self.file.write_all(&b)?;
        self.file.write_all(data)?;
        self.file.write_all(&[0; 3][..(4 - data.len() % 4) % 4])?;
        self.index.write_all(&e.encode())?;
        self.header.frames += 1;
        self.header.max_jpeg_bytes = self.header.max_jpeg_bytes.max(e.bytes);
        Ok(())
    }
    pub fn finish(mut self, cancel: &Cancellation) -> Result<Header> {
        if self.header.frames == 0 {
            return Err(Error::new("conversion_failed", "No output frames"));
        }
        self.header.index_offset = self.file.stream_position()?;
        self.index.seek(SeekFrom::Start(0))?;
        let mut hash = crc32fast::Hasher::new();
        let mut buf = [0; 8192];
        loop {
            cancel.check()?;
            let n = self.index.read(&mut buf)?;
            if n == 0 {
                break;
            }
            hash.update(&buf[..n]);
            self.file.write_all(&buf[..n])?;
        }
        self.header.index_crc = hash.finalize();
        self.header.file_bytes = self.file.stream_position()?;
        self.header.duration_us = self.header.fps.pts(u64::from(self.header.frames));
        self.file.seek(SeekFrom::Start(0))?;
        self.file.write_all(&self.header.encode())?;
        self.file.sync_all()?;
        Ok(self.header)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rational_timestamps_do_not_drift() {
        let r = Rate::new(15, 1).unwrap();
        assert_eq!(r.pts(54_000), 3_600_000_000);
        assert_eq!(r.index_at_or_after(66_666, 100), 1);
        assert_eq!(r.index_at_or_after(u64::MAX, 100), 99);
        assert!(Rate::new(1, 0).is_err());
        assert!(Rate::new(31, 1).is_err());
    }
    #[test]
    fn malformed_headers_never_allocate() {
        for n in [0, 95, 96, u64::MAX] {
            assert!(Header::decode([0; 96], n).is_err());
        }
        let h = Header {
            width: 320,
            height: 240,
            fps: Rate::new(10, 1).unwrap(),
            frames: 1,
            duration_us: 100000,
            index_offset: 112,
            file_bytes: 136,
            max_jpeg_bytes: 1,
            index_crc: 0,
        };
        assert!(Header::decode(h.encode(), 136).is_ok());
        let mut b = h.encode();
        b[48] = 255;
        assert!(Header::decode(b, 136).is_err());
    }
}
