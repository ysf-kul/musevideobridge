//! Local prepared-media registrations; paths are relative to this app's cache.
use crate::Media;
use muse_video_core::{container::Video, storage, Cancellation, Error, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    fs,
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Library {
    version: u32,
    entries: Vec<Entry>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    media: Media,
    path: PathBuf,
}

pub fn load(cache: &Path) -> Result<(Vec<Media>, HashMap<String, PathBuf>)> {
    let index = cache.join("prepared.json");
    if !index.exists() {
        return Ok((Vec::new(), HashMap::new()));
    }
    let mut bytes = Vec::new();
    fs::File::open(index)?
        .take(2_000_001)
        .read_to_end(&mut bytes)?;
    if bytes.len() > 2_000_000 {
        return Err(Error::new(
            "invalid_container",
            "Prepared library is too large; file retained",
        ));
    }
    let library: Library = serde_json::from_slice(&bytes)?;
    if library.version != 1 || library.entries.len() > 128 {
        return Err(Error::new(
            "invalid_container",
            "Unrecognized prepared library; file retained",
        ));
    }
    let root = fs::canonicalize(cache)?;
    let mut media = Vec::new();
    let mut paths = HashMap::new();
    for entry in library.entries {
        if entry
            .path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
        {
            return Err(Error::new(
                "unsafe_path",
                "Prepared path must remain inside the cache",
            ));
        }
        let Some(artifact) = &entry.media.artifact else {
            continue;
        };
        if !storage::valid_digest(&artifact.sha256) {
            return Err(Error::new("invalid_container", "Invalid prepared identity"));
        }
        let Ok(path) = fs::canonicalize(root.join(entry.path)) else {
            continue;
        };
        if !path.starts_with(&root) {
            return Err(Error::new("unsafe_path", "Prepared path escaped cache"));
        }
        // Recheck the real header/index without hashing every large cache file at startup.
        // USB/export perform full verification before transferring; preview checks identity.
        let Ok(video) = Video::open(fs::File::open(&path)?, &Cancellation::default()) else {
            continue;
        };
        if video.header.file_bytes != artifact.header.file_bytes
            || video.header.frames != artifact.header.frames
        {
            continue;
        }
        paths.insert(artifact.sha256.clone(), path);
        media.push(entry.media);
    }
    Ok((media, paths))
}

pub fn save(cache: &Path, media: &[Media], paths: &HashMap<String, PathBuf>) -> Result<()> {
    if media.len() > 128 {
        return Err(Error::new(
            "invalid_container",
            "Prepared library limit is 128 videos",
        ));
    }
    let index = cache.join("prepared.json");
    if index.exists() && load(cache).is_err() {
        let backup = cache.join(format!(
            "prepared-unrecognized-{}.json",
            crate::device::now_ms()
        ));
        let mut out = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(backup)?;
        std::io::copy(&mut fs::File::open(&index)?, &mut out)?;
        out.sync_all()?;
    }
    let entries = media
        .iter()
        .filter_map(|m| {
            let path = paths
                .get(&m.artifact.as_ref()?.sha256)?
                .strip_prefix(cache)
                .ok()?
                .to_path_buf();
            Some(Entry {
                media: m.clone(),
                path,
            })
        })
        .collect();
    let temp = cache.join(format!(".prepared-{}.part", crate::device::now_ms()));
    let mut file = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temp)?;
    file.write_all(&serde_json::to_vec(&Library {
        version: 1,
        entries,
    })?)?;
    file.sync_all()?;
    drop(file);
    fs::rename(temp, index)?;
    Ok(())
}
