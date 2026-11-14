use crate::{
    container::{Video, MAX_FILE},
    media::{progress, ProgressSink},
    Cancellation, Error, Result,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
#[cfg(target_os = "linux")]
use std::time::Duration;
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Debug, Clone, Serialize)]
pub struct Destination {
    pub filesystem: String,
    pub total_bytes: u64,
    pub available_bytes: u64,
    pub fat32_compatible: bool,
    pub verification: String,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub schema_version: u32,
    pub sha256: String,
    pub filename: String,
}
pub fn valid_digest(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn digest_file(path: &Path, cancel: &Cancellation) -> Result<String> {
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut buf = [0; 8192];
    loop {
        cancel.check()?;
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hash.update(&buf[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
fn no_symlink(path: &Path) -> Result<()> {
    if fs::symlink_metadata(path)
        .map(|m| m.file_type().is_symlink())
        .unwrap_or(false)
    {
        return Err(Error::new("unsafe_path", "Symlink destination rejected"));
    }
    Ok(())
}
pub(crate) fn unique() -> String {
    format!(
        "{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    )
}

#[cfg(target_os = "linux")]
pub fn inspect(path: &Path) -> Result<Destination> {
    use std::{ffi::CString, os::unix::ffi::OsStrExt};
    let path = fs::canonicalize(path)?;
    if !path.is_dir() {
        return Err(Error::new(
            "invalid_parameter",
            "Select existing mounted directory",
        ));
    }
    let c = CString::new(path.as_os_str().as_bytes())
        .map_err(|_| Error::new("unsafe_path", "NUL path"))?;
    let mut stat = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    // SAFETY: CString is terminated; stat points to appropriately aligned writable storage.
    if unsafe { libc::statvfs(c.as_ptr(), stat.as_mut_ptr()) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let stat = unsafe { stat.assume_init() };
    let mut filesystem = "unknown".to_owned();
    let mut longest = 0;
    for line in fs::read_to_string("/proc/self/mountinfo")?.lines() {
        if let Some((left, right)) = line.split_once(" - ") {
            if let (Some(mount), Some(kind)) = (
                left.split_whitespace().nth(4),
                right.split_whitespace().next(),
            ) {
                let mount = mount
                    .replace("\\040", " ")
                    .replace("\\011", "\t")
                    .replace("\\134", "\\");
                if path.starts_with(&mount) && mount.len() > longest {
                    longest = mount.len();
                    filesystem = kind.into();
                }
            }
        }
    }
    let mut confirmed = false;
    if filesystem == "vfat" {
        if let Ok(data) = crate::media::capture(
            std::process::Command::new("lsblk").args([
                "--json",
                "--output",
                "FSTYPE,FSVER,MOUNTPOINTS",
            ]),
            &Cancellation::default(),
            Duration::from_secs(5),
        ) {
            if let Ok(value) = serde_json::from_slice::<serde_json::Value>(&data) {
                fn match_volume(v: &serde_json::Value, p: &Path) -> bool {
                    let current = v["fstype"] == "vfat"
                        && v["fsver"] == "FAT32"
                        && v["mountpoints"].as_array().is_some_and(|a| {
                            a.iter()
                                .any(|m| m.as_str().is_some_and(|s| p.starts_with(s)))
                        });
                    current
                        || v["children"]
                            .as_array()
                            .is_some_and(|a| a.iter().any(|x| match_volume(x, p)))
                }
                confirmed = value["blockdevices"]
                    .as_array()
                    .is_some_and(|a| a.iter().any(|x| match_volume(x, &path)));
            }
        }
    }
    Ok(Destination {
        filesystem,
        total_bytes: stat.f_blocks.saturating_mul(stat.f_frsize),
        available_bytes: stat.f_bavail.saturating_mul(stat.f_frsize),
        fat32_compatible: confirmed,
        verification: if confirmed {
            "mounted-volume-FAT32"
        } else {
            "filesystem-not-confirmed-FAT32"
        }
        .into(),
    })
}
#[cfg(windows)]
pub fn inspect(path: &Path) -> Result<Destination> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        GetDiskFreeSpaceExW, GetVolumeInformationW, GetVolumePathNameW,
    };
    let path = fs::canonicalize(path)?;
    if !path.is_dir() {
        return Err(Error::new("invalid_parameter", "Directory required"));
    }
    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut volume = [0u16; 32768];
    let mut available = 0;
    let mut total = 0;
    let mut free = 0;
    let mut name = [0u16; 32];
    // SAFETY: All input strings are NUL terminated and output buffers/integers are writable.
    unsafe {
        if GetDiskFreeSpaceExW(wide.as_ptr(), &mut available, &mut total, &mut free) == 0
            || GetVolumePathNameW(wide.as_ptr(), volume.as_mut_ptr(), volume.len() as u32) == 0
            || GetVolumeInformationW(
                volume.as_ptr(),
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                name.as_mut_ptr(),
                name.len() as u32,
            ) == 0
        {
            return Err(std::io::Error::last_os_error().into());
        }
    }
    let fs =
        String::from_utf16_lossy(&name[..name.iter().position(|x| *x == 0).unwrap_or(name.len())]);
    Ok(Destination {
        fat32_compatible: fs.eq_ignore_ascii_case("FAT32"),
        filesystem: fs,
        total_bytes: total,
        available_bytes: available,
        verification: "Windows-volume-query".into(),
    })
}
#[cfg(not(any(target_os = "linux", windows)))]
pub fn inspect(_: &Path) -> Result<Destination> {
    Err(Error::new("unsupported_platform", "Windows/Linux only"))
}

pub fn capacity_check(required: u64, available: u64) -> Result<()> {
    if required > MAX_FILE {
        return Err(Error::new(
            "file_too_large",
            "2 GiB profile; FAT32 itself allows 4 GiB minus one",
        ));
    }
    if required.checked_add(65_536).is_none_or(|n| n > available) {
        return Err(Error::new(
            "card_full",
            "Insufficient free space including metadata margin",
        ));
    }
    Ok(())
}
pub(crate) fn rename_new(from: &Path, to: &Path) -> Result<()> {
    no_symlink(to)?;
    #[cfg(target_os = "linux")]
    {
        use std::{ffi::CString, os::unix::ffi::OsStrExt};
        let a = CString::new(from.as_os_str().as_bytes())
            .map_err(|_| Error::new("unsafe_path", "NUL"))?;
        let b = CString::new(to.as_os_str().as_bytes())
            .map_err(|_| Error::new("unsafe_path", "NUL"))?;
        // SAFETY: valid C strings, no-follow replacement semantics are enforced by the kernel.
        if unsafe {
            libc::renameat2(
                libc::AT_FDCWD,
                a.as_ptr(),
                libc::AT_FDCWD,
                b.as_ptr(),
                libc::RENAME_NOREPLACE,
            )
        } != 0
        {
            return Err(std::io::Error::last_os_error().into());
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        let a: Vec<u16> = from.as_os_str().encode_wide().chain(Some(0)).collect();
        let b: Vec<u16> = to.as_os_str().encode_wide().chain(Some(0)).collect();
        // SAFETY: valid NUL-terminated strings; flags 0 forbids replacing a destination.
        if unsafe {
            windows_sys::Win32::Storage::FileSystem::MoveFileExW(a.as_ptr(), b.as_ptr(), 0)
        } == 0
        {
            return Err(std::io::Error::last_os_error().into());
        }
    }
    #[cfg(not(any(target_os = "linux", windows)))]
    return Err(Error::new(
        "unsupported_platform",
        "No-clobber publication unavailable",
    ));
    Ok(())
}

pub fn export(
    source: &Path,
    destination: &Path,
    allow_test_directory: bool,
    cancel: &Cancellation,
    sink: ProgressSink,
) -> Result<Selection> {
    cancel.check()?;
    no_symlink(destination)?;
    let root = fs::canonicalize(destination)?;
    let info = inspect(&root)?;
    if !info.fat32_compatible && !allow_test_directory {
        return Err(Error::new(
            "unsupported_filesystem",
            format!("{}; FAT32 must be confirmed", info.filesystem),
        ));
    }
    let mut video = Video::open(File::open(source)?, cancel)?;
    video.verify(cancel)?;
    let size = video.header.file_bytes;
    capacity_check(size, info.available_bytes)?;
    let digest = digest_file(source, cancel)?;
    let library = root.join("muse-video");
    no_symlink(&library)?;
    match fs::create_dir(&library) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists && library.is_dir() => {}
        Err(e) => return Err(e.into()),
    }
    if !fs::canonicalize(&library)?.starts_with(&root) {
        return Err(Error::new("unsafe_path", "Destination escaped mount"));
    }
    let filename = format!("{digest}.mvb");
    let final_path = library.join(&filename);
    no_symlink(&final_path)?;
    if final_path.exists() {
        if digest_file(&final_path, cancel)? != digest {
            return Err(Error::new("digest_mismatch", "Existing media is retained"));
        }
    } else {
        let staging = library.join(format!(".{}.part", unique()));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&staging)?;
        let mut input = File::open(source)?;
        let mut buf = [0; 8192];
        let mut written = 0;
        loop {
            cancel.check()?;
            let n = input.read(&mut buf)?;
            if n == 0 {
                break;
            }
            file.write_all(&buf[..n])?;
            written += n as u64;
            progress(&sink, "copying", written, Some(size));
        }
        file.sync_all()?;
        drop(file);
        if written != size || digest_file(&staging, cancel)? != digest {
            return Err(Error::new(
                "digest_mismatch",
                "Copied media changed/truncated",
            ));
        }
        Video::open(File::open(&staging)?, cancel)?.verify(cancel)?;
        cancel.check()?;
        rename_new(&staging, &final_path)?;
    }
    let selection = Selection {
        schema_version: 1,
        sha256: digest,
        filename,
    };
    let active = library.join("active.json");
    no_symlink(&active)?;
    if active.exists() {
        let mut bytes = Vec::new();
        File::open(&active)?.take(513).read_to_end(&mut bytes)?;
        if bytes.len() > 512 {
            return Err(Error::new(
                "unsafe_path",
                "Unrecognized active.json retained",
            ));
        }
        let old: Selection = serde_json::from_slice(&bytes)
            .map_err(|_| Error::new("unsafe_path", "Unrecognized active.json retained"))?;
        if old.schema_version != 1
            || !valid_digest(&old.sha256)
            || old.filename != format!("{}.mvb", old.sha256)
        {
            return Err(Error::new(
                "unsafe_path",
                "Invalid prior selection retained",
            ));
        }
        let mut backup = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(library.join(format!("active.previous-{}.json", unique())))?;
        backup.write_all(&bytes)?;
        backup.sync_all()?;
    }
    let temp = library.join(format!(".active-{}.part", unique()));
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)?;
    f.write_all(&serde_json::to_vec(&selection)?)?;
    f.sync_all()?;
    drop(f);
    cancel.check()?;
    fs::rename(&temp, &active)?;
    #[cfg(target_os = "linux")]
    File::open(&library)?.sync_all()?;
    progress(&sink, "complete", size, Some(size));
    Ok(selection)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn capacity_and_digest_bounds() {
        assert!(capacity_check(100, 65_636).is_ok());
        assert!(capacity_check(100, 65_635).is_err());
        assert!(capacity_check(u64::MAX, u64::MAX).is_err());
        assert!(valid_digest(&"a".repeat(64)));
        assert!(!valid_digest("../file"));
    }
    #[test]
    fn publication_never_replaces_existing() {
        let dir = tempfile::tempdir().unwrap();
        let a = dir.path().join("a");
        let b = dir.path().join("b");
        fs::write(&a, b"new").unwrap();
        fs::write(&b, b"old").unwrap();
        assert!(rename_new(&a, &b).is_err());
        assert_eq!(fs::read(&b).unwrap(), b"old");
    }
}
