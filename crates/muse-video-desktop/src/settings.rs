use muse_video_core::{media::DEFAULT_ENCODING_FPS, Error, Result};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{Read, Write},
    path::Path,
};
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub version: u32,
    pub language: String,
    pub theme: String,
    pub qscale: u8,
    pub fps: u32,
    pub crop: bool,
    pub last_port: String,
    pub ffmpeg: String,
    pub ffprobe: String,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            version: 1,
            language: "tr".into(),
            theme: "system".into(),
            qscale: 8,
            fps: DEFAULT_ENCODING_FPS,
            crop: false,
            last_port: String::new(),
            ffmpeg: "ffmpeg".into(),
            ffprobe: "ffprobe".into(),
        }
    }
}
impl Settings {
    pub fn validate(&self) -> Result<()> {
        if self.version != 1
            || !["tr", "en"].contains(&self.language.as_str())
            || !["system", "light", "dark"].contains(&self.theme.as_str())
            || !(2..=31).contains(&self.qscale)
            || !(1..=30).contains(&self.fps)
            || self.last_port.len() > 512
            || self.ffmpeg.is_empty()
            || self.ffprobe.is_empty()
            || self.ffmpeg.len() > 4096
            || self.ffprobe.len() > 4096
        {
            return Err(Error::new("invalid_parameter", "Settings validation"));
        }
        Ok(())
    }
    pub fn load(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let mut b = Vec::new();
        fs::File::open(path)?.take(16385).read_to_end(&mut b)?;
        if b.len() > 16384 {
            return Err(Error::new(
                "invalid_parameter",
                "Settings too large; file retained",
            ));
        }
        let s: Self = serde_json::from_slice(&b)?;
        s.validate()?;
        Ok(s)
    }
    pub fn save(&self, path: &Path) -> Result<()> {
        self.validate()?;
        let parent = path
            .parent()
            .ok_or_else(|| Error::new("unsafe_path", "Settings parent"))?;
        fs::create_dir_all(parent)?;
        // Preserve invalid or future-version settings before replacing them.
        if path.exists() && Self::load(path).is_err() {
            let backup = parent.join(format!(
                "settings-unrecognized-{}.json",
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_nanos()
            ));
            let mut input = fs::File::open(path)?;
            let mut output = fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(backup)?;
            std::io::copy(&mut input, &mut output)?;
            output.sync_all()?;
        }
        let temp = parent.join(format!(
            ".settings-{}.part",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        let mut f = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)?;
        f.write_all(&serde_json::to_vec_pretty(self)?)?;
        f.sync_all()?;
        drop(f);
        fs::rename(temp, path)?;
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn first_launch_and_persistence() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("settings.json");
        assert_eq!(Settings::load(&p).unwrap().language, "tr");
        assert_eq!(Settings::load(&p).unwrap().fps, 25);
        let s = Settings {
            language: "en".into(),
            theme: "dark".into(),
            fps: 10,
            ..Settings::default()
        };
        s.save(&p).unwrap();
        let original = fs::read(&p).unwrap();
        let loaded = Settings::load(&p).unwrap();
        assert_eq!(loaded.language, "en");
        assert_eq!(loaded.theme, "dark");
        assert_eq!(loaded.fps, 10);
        assert_eq!(fs::read(&p).unwrap(), original);
        fs::write(&p, b"bad").unwrap();
        assert!(Settings::load(&p).is_err());
        assert_eq!(fs::read(p).unwrap(), b"bad");
    }
}
