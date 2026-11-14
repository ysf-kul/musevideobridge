use muse_video_core::{
    container::{Rate, Video, Writer},
    media::{self, Fit, Options, Tools},
    storage, Cancellation,
};
use std::{
    fs::{self, File},
    io::{Cursor, Write},
    path::Path,
    process::Command,
    sync::mpsc,
};
fn input(root: &Path, size: &str, sar: Option<&str>) -> std::path::PathBuf {
    let path = root.join("input.mp4");
    let mut c = Command::new("ffmpeg");
    c.args([
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        &format!("testsrc2=size={size}:rate=12"),
        "-t",
        "3",
    ]);
    if let Some(sar) = sar {
        c.args(["-vf", &format!("setsar={sar}")]);
    }
    assert!(c
        .args(["-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p"])
        .arg(&path)
        .status()
        .unwrap()
        .success());
    path
}
fn artifact(root: &Path) -> std::path::PathBuf {
    let source = input(root, "320x240", None);
    let job = root.join("job");
    media::convert(
        &source,
        &job,
        &Options::default(),
        &Tools::default(),
        &Cancellation::default(),
        None,
    )
    .unwrap();
    job.join("video.mvb")
}
#[test]
fn real_mp4_conversion_metadata_aspect_quality_and_indexing() {
    for (dimensions, sar, fit, expected) in [
        ("640x360", None, Fit::Contain, (320, 184)),
        ("240x320", None, Fit::Contain, (184, 240)),
        ("320x240", Some("2/1"), Fit::Contain, (320, 120)),
        ("640x360", None, Fit::Crop, (320, 240)),
    ] {
        let root = tempfile::tempdir().unwrap();
        let source = input(root.path(), dimensions, sar);
        let cancel = Cancellation::default();
        let metadata = media::probe(&source, &Tools::default(), &cancel).unwrap();
        assert_eq!(metadata.duration_us, Some(3_000_000));
        assert_eq!(
            metadata.original_fps.unwrap(),
            Rate {
                numerator: 12,
                denominator: 1
            }
        );
        let options = Options {
            fps: Rate::new(15, 1).unwrap(),
            qscale: 9,
            fit,
            ..Options::default()
        };
        let (tx, rx) = mpsc::sync_channel(100);
        let output = root.path().join("job");
        let a = media::convert(
            &source,
            &output,
            &options,
            &Tools::default(),
            &cancel,
            Some(tx),
        )
        .unwrap();
        assert_eq!((a.header.width, a.header.height), expected);
        // Default aligned bounds prepare dimensions accepted by the classic
        // ESP32 block decoder, including narrow/portrait fitted content.
        assert_eq!(a.header.width % 8, 0);
        assert_eq!(a.header.height % 8, 0);
        assert_eq!(a.options.qscale, 9);
        assert_eq!(a.header.fps, options.fps);
        assert_eq!(a.header.frames, 45);
        assert_eq!(a.header.duration_us, 3_000_000);
        assert!(rx.try_iter().any(|e| e.stage == "converting"));
        let mut v = Video::open(File::open(output.join("video.mvb")).unwrap(), &cancel).unwrap();
        v.verify(&cancel).unwrap();
        for i in [0, 1, 23, 44] {
            let e = v.entry(i).unwrap();
            assert_eq!(e.pts_us, options.fps.pts(i as u64));
            assert!(!v.frame(i).unwrap().is_empty());
        }
        assert!(v.frame(45).is_err());
        assert!(output.join("manifest.json").is_file());
        assert!(
            media::convert(&source, &output, &options, &Tools::default(), &cancel, None).is_err()
        );
    }
}
#[test]
fn audio_is_excluded_without_changing_video_timing_or_source_files() {
    let root = tempfile::tempdir().unwrap();
    let video = input(root.path(), "320x240", None);
    let audiovisual = root.path().join("longer-audio.mp4");
    assert!(Command::new("ffmpeg")
        .args(["-hide_banner", "-loglevel", "error", "-i"])
        .arg(&video)
        .args([
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=7",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
        ])
        .arg(&audiovisual)
        .status()
        .unwrap()
        .success());
    let probe = Command::new("ffprobe")
        .args([
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
        ])
        .arg(&audiovisual)
        .output()
        .unwrap();
    assert!(probe.status.success());
    let metadata: serde_json::Value = serde_json::from_slice(&probe.stdout).unwrap();
    assert!(metadata["streams"]
        .as_array()
        .unwrap()
        .iter()
        .any(|s| s["codec_type"] == "audio"));
    assert!(
        metadata["format"]["duration"]
            .as_str()
            .unwrap()
            .parse::<f64>()
            .unwrap()
            > 6.9
    );
    let cancel = Cancellation::default();
    let before = [&video, &audiovisual].map(|p| storage::digest_file(p, &cancel).unwrap());
    let mut outputs = Vec::new();
    for (name, source) in [("video-only", &video), ("with-audio", &audiovisual)] {
        let job = root.path().join(name);
        let artifact = media::convert(
            source,
            &job,
            &Options::default(),
            &Tools::default(),
            &cancel,
            None,
        )
        .unwrap();
        assert_eq!(artifact.input.duration_us, Some(3_000_000));
        assert_eq!(artifact.options.fps, Rate::new(25, 1).unwrap());
        assert_eq!(artifact.header.frames, 75);
        assert_eq!(artifact.header.duration_us, 3_000_000);
        let mut container =
            Video::open(File::open(job.join("video.mvb")).unwrap(), &cancel).unwrap();
        container.verify(&cancel).unwrap();
        outputs.push(artifact.sha256);
    }
    assert_eq!(
        outputs[0], outputs[1],
        "Audio must not alter encoded video bytes"
    );
    assert_eq!(
        before,
        [&video, &audiovisual].map(|p| storage::digest_file(p, &cancel).unwrap())
    );
}
#[test]
fn optimal_huffman_preserves_pixels_and_explicit_30_fps() {
    let root = tempfile::tempdir().unwrap();
    let source = input(root.path(), "320x240", None);
    let cancel = Cancellation::default();
    let mut optimized = Vec::new();
    for fps in [25, 30] {
        let options = if fps == 25 {
            Options::default()
        } else {
            Options {
                fps: Rate::new(fps, 1).unwrap(),
                ..Options::default()
            }
        };
        let job = root.path().join(format!("fps-{fps}"));
        let artifact =
            media::convert(&source, &job, &options, &Tools::default(), &cancel, None).unwrap();
        assert_eq!(artifact.options.fps, Rate::new(fps, 1).unwrap());
        assert_eq!(artifact.header.fps, options.fps);
        assert_eq!(artifact.header.frames, 3 * fps);
        assert_eq!(artifact.header.duration_us, 3_000_000);
        if fps == 25 {
            optimized = Video::open(File::open(job.join("video.mvb")).unwrap(), &cancel)
                .unwrap()
                .frame(0)
                .unwrap();
        }
    }
    // Independent real-encoder reference checks the observable compression and
    // pixel result, rather than only inspecting the implementation's arguments.
    let standard = Command::new("ffmpeg")
        .args(["-hide_banner", "-loglevel", "error", "-nostdin", "-i"])
        .arg(&source)
        .args([
            "-map", "0:v:0", "-an", "-sn", "-dn", "-vf",
            "fps=25/1,scale=320:240:flags=bicubic:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1:out_range=full:out_color_matrix=bt601,format=yuvj420p",
            "-c:v", "mjpeg", "-q:v", "8", "-qmin", "8", "-qmax", "8",
            "-huffman", "default", "-threads", "1", "-frames:v", "1",
            "-f", "image2pipe", "pipe:1",
        ])
        .output()
        .unwrap();
    assert!(standard.status.success());
    assert!(optimized.len() < standard.stdout.len());
    fn rgb(jpeg: &[u8]) -> Vec<u8> {
        let mut process = Command::new("ffmpeg")
            .args([
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "mjpeg",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-an",
                "-sn",
                "-dn",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        process.stdin.take().unwrap().write_all(jpeg).unwrap();
        let result = process.wait_with_output().unwrap();
        assert!(result.status.success());
        assert_eq!(result.stdout.len(), 320 * 240 * 3);
        result.stdout
    }
    assert_eq!(rgb(&optimized), rgb(&standard.stdout));
}
#[test]
fn corrupted_truncated_and_mutated_container_is_rejected() {
    let root = tempfile::tempdir().unwrap();
    let path = artifact(root.path());
    let bytes = fs::read(path).unwrap();
    let cancel = Cancellation::default();
    for n in [0, 1, 95, 96, bytes.len() / 2, bytes.len() - 1] {
        assert!(Video::open(Cursor::new(&bytes[..n]), &cancel).is_err());
    }
    for i in 0..96 {
        let mut b = bytes.clone();
        b[i] ^= 128;
        assert!(Video::open(Cursor::new(b), &cancel).is_err());
    }
    let index = u64::from_le_bytes(bytes[48..56].try_into().unwrap()) as usize;
    for i in [index, index + 8, index + 16, index + 20, bytes.len() - 1] {
        let mut b = bytes.clone();
        b[i] ^= 1;
        assert!(Video::open(Cursor::new(b), &cancel).is_err());
    }
    for i in [96, 100, 104, 108, 130] {
        let mut b = bytes.clone();
        b[i] ^= 1;
        let mut v = Video::open(Cursor::new(b), &cancel).unwrap();
        assert!(v.verify(&cancel).is_err());
    }
    // Seeded arbitrary buffers exercise bounded error paths, never large allocations.
    let mut seed = 17u32;
    for n in 0..300 {
        let mut b = vec![0; n];
        for x in &mut b {
            seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
            *x = (seed >> 24) as u8;
        }
        assert!(Video::open(Cursor::new(b), &cancel).is_err());
    }
}
#[test]
fn sd_export_preserves_unrelated_files_and_valid_prior_selection() {
    let root = tempfile::tempdir().unwrap();
    let source = artifact(root.path());
    let card = tempfile::tempdir().unwrap();
    fs::write(card.path().join("photo.jpg"), b"keep").unwrap();
    let cancel = Cancellation::default();
    let first = storage::export(&source, card.path(), true, &cancel, None).unwrap();
    let lib = card.path().join("muse-video");
    assert_eq!(
        storage::digest_file(&lib.join(&first.filename), &cancel).unwrap(),
        first.sha256
    );
    storage::export(&source, card.path(), true, &cancel, None).unwrap();
    assert_eq!(fs::read(card.path().join("photo.jpg")).unwrap(), b"keep");
    assert!(fs::read_dir(&lib).unwrap().any(|e| e
        .unwrap()
        .file_name()
        .to_string_lossy()
        .starts_with("active.previous-")));
    fs::write(lib.join("active.json"), b"unrelated config").unwrap();
    assert!(storage::export(&source, card.path(), true, &cancel, None).is_err());
    assert_eq!(
        fs::read(lib.join("active.json")).unwrap(),
        b"unrelated config"
    );
    let cancelled = Cancellation::default();
    cancelled.cancel();
    assert!(storage::export(&source, card.path(), true, &cancelled, None).is_err());
    assert!(storage::capacity_check(100, 100).is_err());
    assert!(storage::capacity_check(2_147_483_648, u64::MAX).is_err());
}
#[test]
fn invalid_inputs_and_rate_fail_without_publishing() {
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("bad.mp4");
    fs::write(&source, b"not video").unwrap();
    let cancel = Cancellation::default();
    assert!(media::probe(&source, &Tools::default(), &cancel).is_err());
    assert!(Writer::new(
        tempfile::tempfile().unwrap(),
        tempfile::tempfile().unwrap(),
        Rate {
            numerator: 0,
            denominator: 0
        }
    )
    .is_err());
    assert_eq!(Rate::new(1, 1).unwrap().pts(u64::MAX), u64::MAX);
    assert!(media::convert(
        &source,
        &root.path().join("job"),
        &Options::default(),
        &Tools::default(),
        &cancel,
        None
    )
    .is_err());
    assert!(!root.path().join("job").exists());
}
#[cfg(unix)]
#[test]
fn sd_symlink_destinations_are_rejected() {
    let root = tempfile::tempdir().unwrap();
    let source = artifact(root.path());
    let card = tempfile::tempdir().unwrap();
    let elsewhere = tempfile::tempdir().unwrap();
    std::os::unix::fs::symlink(elsewhere.path(), card.path().join("muse-video")).unwrap();
    assert!(storage::export(&source, card.path(), true, &Cancellation::default(), None).is_err());
    assert_eq!(fs::read_dir(elsewhere.path()).unwrap().count(), 0);
}
#[test]
fn conversion_cancellation_retains_identifiable_partial_and_can_retry() {
    let root = tempfile::tempdir().unwrap();
    let source = input(root.path(), "640x360", None);
    let output = root.path().join("cancelled-job");
    let cancel = Cancellation::default();
    let trigger = cancel.clone();
    let (tx, rx) = mpsc::sync_channel(100);
    let watcher = std::thread::spawn(move || {
        for p in rx {
            let p: muse_video_core::media::Progress = p;
            if p.stage == "converting" {
                trigger.cancel();
                break;
            }
        }
    });
    let error = media::convert(
        &source,
        &output,
        &Options::default(),
        &Tools::default(),
        &cancel,
        Some(tx),
    )
    .unwrap_err();
    watcher.join().unwrap();
    assert_eq!(error.code, "cancelled");
    assert!(output.join("failure.json").is_file());
    assert!(output.join("video.mvb.part").is_file());
    assert!(!output.join("video.mvb").exists());
    media::convert(
        &source,
        &root.path().join("retry-job"),
        &Options::default(),
        &Tools::default(),
        &Cancellation::default(),
        None,
    )
    .unwrap();
}
#[test]
fn malformed_jpeg_tables_are_rejected_before_decode() {
    let root = tempfile::tempdir().unwrap();
    let path = artifact(root.path());
    let mut v = Video::open(File::open(path).unwrap(), &Cancellation::default()).unwrap();
    let original = v.frame(0).unwrap();
    let mut at = 2;
    while original[at + 1] != 0xc4 {
        at += 2 + u16::from_be_bytes([original[at + 2], original[at + 3]]) as usize;
    }
    for (relative, value) in [(4, 255), (5, 255), (21, 255)] {
        let mut b = original.clone();
        b[at + relative] = value;
        assert!(muse_video_core::jpeg::validate(&b).is_err());
    }
    let mut b = original.clone();
    b.splice(b.len() - 2..b.len() - 2, [255, 0xc0]);
    assert!(muse_video_core::jpeg::validate(&b).is_err());
}
#[test]
fn variable_frame_rate_and_rotation_inputs_normalize_to_fixed_timing() {
    let root = tempfile::tempdir().unwrap();
    let source = input(root.path(), "320x240", None);
    let rotated = root.path().join("rotated.mp4");
    assert!(Command::new("ffmpeg")
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-display_rotation",
            "90",
            "-i"
        ])
        .arg(&source)
        .args(["-c", "copy"])
        .arg(&rotated)
        .status()
        .unwrap()
        .success());
    let cancel = Cancellation::default();
    let m = media::probe(&rotated, &Tools::default(), &cancel).unwrap();
    assert_eq!(m.rotation_deg.abs(), 90);
    let a = media::convert(
        &rotated,
        &root.path().join("rotate-job"),
        &Options::default(),
        &Tools::default(),
        &cancel,
        None,
    )
    .unwrap();
    assert_eq!((a.header.width, a.header.height), (184, 240));
    let vfr = root.path().join("vfr.mp4");
    assert!(Command::new("ffmpeg")
        .args(["-hide_banner", "-loglevel", "error", "-i"])
        .arg(&source)
        .args([
            "-vf",
            "setpts=if(lt(N\\,12)\\,N/(12*TB)\\,(1+(N-12)/6)/TB)",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-threads",
            "1"
        ])
        .arg(&vfr)
        .status()
        .unwrap()
        .success());
    let m = media::probe(&vfr, &Tools::default(), &cancel).unwrap();
    assert_ne!(m.original_fps, m.nominal_fps);
    let options = Options {
        fps: Rate::new(7, 1).unwrap(),
        ..Options::default()
    };
    let a = media::convert(
        &vfr,
        &root.path().join("vfr-job"),
        &options,
        &Tools::default(),
        &cancel,
        None,
    )
    .unwrap();
    assert_eq!(a.header.fps, options.fps);
    assert_eq!(
        a.header.duration_us,
        options.fps.pts(u64::from(a.header.frames))
    );
    assert!(a.header.duration_us.abs_diff(m.duration_us.unwrap()) < 300000);
}
#[test]
fn oversized_sparse_container_fails_before_index_or_frame_allocation() {
    let root = tempfile::tempdir().unwrap();
    let path = artifact(root.path());
    let file = fs::OpenOptions::new().write(true).open(&path).unwrap();
    file.set_len(2_147_483_648).unwrap();
    drop(file);
    assert!(Video::open(File::open(path).unwrap(), &Cancellation::default()).is_err());
}
