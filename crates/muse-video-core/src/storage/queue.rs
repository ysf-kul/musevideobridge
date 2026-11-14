//! Same bounded record as firmware/include/queue_record.hpp.
use super::{no_symlink, unique, valid_digest};
use crate::{Cancellation, Error, Result};
use std::{fs, io::Write, path::Path};
pub const RECORD_BYTES: usize = 808;
#[derive(Clone, Debug, Default)]
pub struct Queue {
    pub generation: u32,
    pub ids: Vec<String>,
    pub position: Option<usize>,
    pub mode: u32,
}
fn u32_at(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(b[at..at + 4].try_into().unwrap())
}
pub fn decode(b: &[u8]) -> Option<Queue> {
    if b.len() != RECORD_BYTES
        || &b[..8] != b"MVBQUE1\0"
        || u32_at(b, 804) != crc32fast::hash(&b[..804])
    {
        return None;
    }
    let generation = u32_at(b, 8);
    let count = u32_at(b, 12) as usize;
    let position = u32_at(b, 16);
    let mode = u32_at(b, 20);
    if generation == 0
        || count > 12
        || mode > 2
        || (count == 0 && position != u32::MAX)
        || (count > 0 && position as usize >= count)
    {
        return None;
    }
    let mut ids = Vec::new();
    for i in 0..count {
        let at = 24 + i * 65;
        let id = std::str::from_utf8(&b[at..at + 64]).ok()?.to_owned();
        if b[at + 64] != 0 || !valid_digest(&id) || ids.contains(&id) {
            return None;
        }
        ids.push(id);
    }
    if b[24 + count * 65..804].iter().any(|v| *v != 0) {
        return None;
    }
    Some(Queue {
        generation,
        ids,
        position: (count > 0).then_some(position as usize),
        mode,
    })
}
pub fn encode(queue: &Queue) -> Result<[u8; RECORD_BYTES]> {
    if queue.generation == 0
        || queue.ids.len() > 12
        || queue.mode > 2
        || queue.ids.iter().any(|id| !valid_digest(id))
        || queue
            .ids
            .iter()
            .enumerate()
            .any(|(i, id)| queue.ids[..i].contains(id))
        || (!queue.ids.is_empty() && queue.position.is_none_or(|p| p >= queue.ids.len()))
        || (queue.ids.is_empty() && queue.position.is_some())
    {
        return Err(Error::new("invalid_parameter", "Invalid persisted queue"));
    }
    let mut b = [0; RECORD_BYTES];
    b[..8].copy_from_slice(b"MVBQUE1\0");
    for (at, value) in [
        (8, queue.generation),
        (12, queue.ids.len() as u32),
        (16, queue.position.map_or(u32::MAX, |v| v as u32)),
        (20, queue.mode),
    ] {
        b[at..at + 4].copy_from_slice(&value.to_le_bytes());
    }
    for (i, id) in queue.ids.iter().enumerate() {
        b[24 + i * 65..24 + i * 65 + 64].copy_from_slice(id.as_bytes());
    }
    let crc = crc32fast::hash(&b[..804]);
    b[804..].copy_from_slice(&crc.to_le_bytes());
    Ok(b)
}
pub fn load(library: &Path) -> Result<Queue> {
    let mut queue = Queue::default();
    for slot in 0..2 {
        let path = library.join(format!("queue.{slot}.bin"));
        no_symlink(&path)?;
        if path.exists() && fs::metadata(&path)?.len() == RECORD_BYTES as u64 {
            if let Some(candidate) = decode(&fs::read(&path)?) {
                if candidate.generation > queue.generation {
                    queue = candidate;
                }
            }
        }
    }
    Ok(queue)
}
pub fn save(library: &Path, queue: &mut Queue, cancel: &Cancellation) -> Result<()> {
    let generation = queue
        .generation
        .checked_add(1)
        .ok_or_else(|| Error::new("queue_save_failed", "Queue generation exhausted"))?;
    let mut next = queue.clone();
    next.generation = generation;
    let bytes = encode(&next)?;
    let path = library.join(format!("queue.{}.bin", generation % 2));
    no_symlink(&path)?;
    if path.exists()
        && (fs::metadata(&path)?.len() != RECORD_BYTES as u64
            || decode(&fs::read(&path)?).is_none())
    {
        super::rename_new(
            &path,
            &library.join(format!("queue.unrecognized-{}.bin", unique())),
        )?;
    }
    let temp = library.join(format!(".queue-{}.part", unique()));
    let mut f = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temp)?;
    f.write_all(&bytes)?;
    f.sync_all()?;
    drop(f);
    cancel.check()?;
    fs::rename(temp, path)?;
    *queue = next;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn record_bounds_crc_and_interrupted_generation() {
        let root = tempfile::tempdir().unwrap();
        let mut queue = Queue {
            ids: vec!["a".repeat(64), "b".repeat(64)],
            position: Some(1),
            mode: 2,
            ..Queue::default()
        };
        save(root.path(), &mut queue, &Cancellation::default()).unwrap();
        assert_eq!(queue.generation, 1);
        let bytes = encode(&queue).unwrap();
        let restored = decode(&bytes).unwrap();
        assert_eq!(restored.ids, queue.ids);
        assert_eq!(restored.position, Some(1));
        assert_eq!(restored.mode, 2);
        let mut corrupt = bytes;
        corrupt[24] ^= 1;
        assert!(decode(&corrupt).is_none());
        fs::write(root.path().join("queue.0.bin"), b"interrupted write").unwrap();
        assert_eq!(load(root.path()).unwrap().generation, 1);
        queue.ids.reverse();
        save(root.path(), &mut queue, &Cancellation::default()).unwrap();
        assert_eq!(load(root.path()).unwrap().ids, queue.ids);
        assert!(fs::read_dir(root.path()).unwrap().any(|p| p
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with("queue.unrecognized-")));
        queue.ids.push(queue.ids[0].clone());
        assert!(encode(&queue).is_err());
    }
    #[test]
    fn deliberate_empty_queue_is_valid() {
        let queue = Queue {
            generation: 1,
            ..Queue::default()
        };
        let restored = decode(&encode(&queue).unwrap()).unwrap();
        assert!(restored.ids.is_empty());
        assert_eq!(restored.position, None);
    }
}
