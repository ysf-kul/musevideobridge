use crate::{
    container::{u16le, u32le},
    Error, Result,
};
pub const MAX_PAYLOAD: usize = 2048;
pub const MAX_WIRE: usize = 2078;
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    pub kind: u8,
    pub session: u32,
    pub sequence: u32,
    pub payload: Vec<u8>,
}
impl Frame {
    pub fn json(kind: u8, session: u32, sequence: u32, value: &serde_json::Value) -> Result<Self> {
        let payload = serde_json::to_vec(value)?;
        if payload.len() > MAX_PAYLOAD {
            return Err(Error::new("invalid_parameter", "Payload too large"));
        }
        Ok(Self {
            kind,
            session,
            sequence,
            payload,
        })
    }
    pub fn encode(&self) -> Result<Vec<u8>> {
        if self.payload.len() > MAX_PAYLOAD || ![1, 2, 3, 16, 17].contains(&self.kind) {
            return Err(Error::new("invalid_frame", "Kind/length"));
        }
        let mut data = vec![0; 16];
        data[0] = 1;
        data[1] = self.kind;
        data[4..8].copy_from_slice(&self.session.to_le_bytes());
        data[8..12].copy_from_slice(&self.sequence.to_le_bytes());
        data[12..14].copy_from_slice(&(self.payload.len() as u16).to_le_bytes());
        data.extend_from_slice(&self.payload);
        let crc = crc32fast::hash(&data);
        data.extend_from_slice(&crc.to_le_bytes());
        let mut out = vec![0];
        let mut code_pos = 0;
        let mut code = 1u8;
        for b in data {
            if b == 0 {
                out[code_pos] = code;
                code_pos = out.len();
                out.push(0);
                code = 1;
            } else {
                out.push(b);
                code += 1;
                if code == 255 {
                    out[code_pos] = code;
                    code_pos = out.len();
                    out.push(0);
                    code = 1;
                }
            }
        }
        out[code_pos] = code;
        out.push(0);
        Ok(out)
    }
    pub fn decode(encoded: &[u8]) -> Result<Self> {
        let bad = || Error::new("invalid_frame", "COBS/version/CRC/length");
        if encoded.is_empty() || encoded.len() >= MAX_WIRE {
            return Err(bad());
        }
        let mut raw = Vec::with_capacity(2068);
        let mut i = 0;
        while i < encoded.len() {
            let code = encoded[i] as usize;
            if code == 0 || i + code > encoded.len() {
                return Err(bad());
            }
            if encoded[i + 1..i + code].contains(&0) {
                return Err(bad());
            }
            raw.extend_from_slice(&encoded[i + 1..i + code]);
            i += code;
            if code < 255 && i < encoded.len() {
                raw.push(0);
            }
            if raw.len() > 2068 {
                return Err(bad());
            }
        }
        if raw.len() < 20
            || raw[0] != 1
            || ![1, 2, 3, 16, 17].contains(&raw[1])
            || u16le(&raw, 2) != 0
            || u16le(&raw, 14) != 0
            || usize::from(u16le(&raw, 12)) + 20 != raw.len()
            || crc32fast::hash(&raw[..raw.len() - 4]) != u32le(&raw, raw.len() - 4)
        {
            return Err(bad());
        }
        Ok(Self {
            kind: raw[1],
            session: u32le(&raw, 4),
            sequence: u32le(&raw, 8),
            payload: raw[16..raw.len() - 4].to_vec(),
        })
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn roundtrip_payload_extremes_and_corruption() {
        for n in [0, 1, 253, 254, 255, 1024, 2048] {
            for fill in [0, 1, 255] {
                let f = Frame {
                    kind: 16,
                    session: 123,
                    sequence: 456,
                    payload: vec![fill; n],
                };
                let wire = f.encode().unwrap();
                assert!(wire.len() <= MAX_WIRE);
                assert_eq!(Frame::decode(&wire[..wire.len() - 1]).unwrap(), f);
                let mut bad = wire.clone();
                bad[1] ^= 7;
                assert!(Frame::decode(&bad[..bad.len() - 1]).is_err());
            }
        }
        for n in 0..100 {
            assert!(Frame::decode(&vec![255; n]).is_err());
        }
    }
}
