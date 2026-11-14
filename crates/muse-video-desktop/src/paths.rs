//! Portable packages opt in through a checked marker beside the executable.
use muse_video_core::{Error, Result};
use serde::Deserialize;
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Portable {
    schema_version: u32,
}
pub fn directories(exe: &Path, config: PathBuf, cache: PathBuf) -> Result<(PathBuf, PathBuf)> {
    let parent = exe
        .parent()
        .ok_or_else(|| Error::new("unsafe_path", "Executable directory"))?;
    let marker = parent.join("portable-mode.json");
    if !marker.exists() {
        return Ok((config, cache));
    }
    if fs::symlink_metadata(&marker)?.file_type().is_symlink() {
        return Err(Error::new("unsafe_path", "Portable marker symlink"));
    }
    let mut b = Vec::new();
    fs::File::open(marker)?.take(257).read_to_end(&mut b)?;
    if b.len() > 256 {
        return Err(Error::new("invalid_parameter", "Portable marker too large"));
    }
    let marker: Portable = serde_json::from_slice(&b)?;
    if marker.schema_version != 1 {
        return Err(Error::new("invalid_parameter", "Portable marker version"));
    }
    let data = parent.join("Data");
    if data.exists() && (!data.is_dir() || fs::symlink_metadata(&data)?.file_type().is_symlink()) {
        return Err(Error::new("unsafe_path", "Portable Data directory"));
    }
    Ok((data.join("config"), data.join("cache")))
}
pub fn tool(exe: &Path, selected: &str) -> PathBuf {
    if ["ffmpeg", "ffprobe"].contains(&selected) {
        if let Some(parent) = exe.parent() {
            let bundled =
                parent
                    .join("tools")
                    .join(format!("{}{}", selected, std::env::consts::EXE_SUFFIX));
            if bundled.is_file() {
                return bundled;
            }
        }
    }
    selected.into()
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn portable_settings_follow_the_executable_and_reject_bad_marker() {
        let root = tempfile::tempdir().unwrap();
        let exe = root.path().join("bridge.exe");
        let config = PathBuf::from("native-config");
        let cache = PathBuf::from("native-cache");
        assert_eq!(
            directories(&exe, config.clone(), cache.clone()).unwrap(),
            (config.clone(), cache.clone())
        );
        fs::write(
            root.path().join("portable-mode.json"),
            b"{\"schema_version\":1}",
        )
        .unwrap();
        assert_eq!(
            directories(&exe, config.clone(), cache.clone()).unwrap(),
            (
                root.path().join("Data/config"),
                root.path().join("Data/cache")
            )
        );
        fs::write(
            root.path().join("portable-mode.json"),
            b"{\"schema_version\":2}",
        )
        .unwrap();
        assert!(directories(&exe, config, cache).is_err());
    }
    #[test]
    fn bundled_tools_move_with_package_and_explicit_choices_are_preserved() {
        let root = tempfile::tempdir().unwrap();
        let exe = root.path().join("bridge.exe");
        fs::create_dir(root.path().join("tools")).unwrap();
        let ff = root
            .path()
            .join("tools")
            .join(format!("ffmpeg{}", std::env::consts::EXE_SUFFIX));
        fs::write(&ff, b"fixture").unwrap();
        assert_eq!(tool(&exe, "ffmpeg"), ff);
        assert_eq!(
            tool(&exe, "/explicit/ffmpeg"),
            PathBuf::from("/explicit/ffmpeg")
        );
    }
}
