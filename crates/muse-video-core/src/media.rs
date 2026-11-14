use crate::{
    container::{Header, Rate, Video, Writer, MAX_FILE},
    jpeg, Cancellation, Error, Result,
};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, File, OpenOptions},
    io::{BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdout, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::SyncSender,
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

#[derive(Debug, Clone)]
pub struct Tools {
    pub ffmpeg: PathBuf,
    pub ffprobe: PathBuf,
}
impl Default for Tools {
    fn default() -> Self {
        Self {
            ffmpeg: "ffmpeg".into(),
            ffprobe: "ffprobe".into(),
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Metadata {
    pub width: u32,
    pub height: u32,
    pub duration_us: Option<u64>,
    pub original_fps: Option<Rate>,
    pub nominal_fps: Option<Rate>,
    pub sample_aspect_ratio: Option<String>,
    pub display_aspect_ratio: Option<String>,
    pub rotation_deg: i32,
    pub codec: String,
    pub interlaced: bool,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub enum Fit {
    Contain,
    Crop,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Scaling {
    Bicubic,
    Area,
    Lanczos,
}
impl Scaling {
    fn filter_name(&self) -> &'static str {
        match self {
            Self::Bicubic => "bicubic",
            Self::Area => "area",
            Self::Lanczos => "lanczos",
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Options {
    pub fps: Rate,
    pub qscale: u8,
    pub fit: Fit,
    pub width: u16,
    pub height: u16,
    pub scaling: Scaling,
}
pub const DEFAULT_ENCODING_FPS: u32 = 25;
fn frame_filter(interlaced: bool, options: &Options) -> String {
    let aspect = match options.fit {
        Fit::Contain => "decrease",
        Fit::Crop => "increase",
    };
    let prefix = if interlaced {
        "bwdif=mode=send_frame,"
    } else {
        ""
    };
    let crop = if matches!(options.fit, Fit::Crop) {
        format!(",crop={}:{}", options.width, options.height)
    } else {
        String::new()
    };
    // The classic-ESP32 block decoder accepts multiples of eight. Prepare the
    // small border here while preserving the existing 4:2:0 samples. An even
    // offset can displace the ideal centre by at most one pixel. Because both
    // requested bounds are aligned, rounding up cannot exceed either bound.
    let pad = if matches!(options.fit, Fit::Contain)
        && options.width.is_multiple_of(8)
        && options.height.is_multiple_of(8)
    {
        ",pad=ceil(iw/8)*8:ceil(ih/8)*8:floor((ow-iw)/4)*2:floor((oh-ih)/4)*2:color=black"
    } else {
        ""
    };
    format!(
        "{prefix}fps={}/{},scale={}:{}:flags={}:force_original_aspect_ratio={aspect}:force_divisible_by=2:reset_sar=1:out_range=full:out_color_matrix=bt601{crop},format=yuvj420p{pad}",
        options.fps.numerator, options.fps.denominator, options.width,
        options.height, options.scaling.filter_name()
    )
}
impl Default for Options {
    fn default() -> Self {
        Self {
            fps: Rate {
                numerator: DEFAULT_ENCODING_FPS,
                denominator: 1,
            },
            qscale: 8,
            fit: Fit::Contain,
            width: 320,
            height: 240,
            scaling: Scaling::Bicubic,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Estimate {
    pub frames: Option<u64>,
    pub worst_case_bytes: Option<u64>,
    pub frame_cap_bytes: usize,
}
#[derive(Debug, Clone, Serialize)]
pub struct Progress {
    pub stage: String,
    pub completed: u64,
    pub total: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes_per_second: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remaining_seconds: Option<f64>,
    pub item_index: Option<usize>,
    pub items_total: Option<usize>,
    pub filename: Option<String>,
}
pub type ProgressSink = Option<SyncSender<Progress>>;
pub(crate) fn progress(sink: &ProgressSink, stage: &str, completed: u64, total: Option<u64>) {
    if let Some(s) = sink {
        let _ = s.try_send(Progress {
            stage: stage.into(),
            completed,
            total,
            bytes_per_second: None,
            remaining_seconds: None,
            item_index: None,
            items_total: None,
            filename: None,
        });
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Artifact {
    pub schema_version: u32,
    pub sha256: String,
    pub header: Header,
    pub options: Options,
    pub input: Metadata,
    pub converter: String,
}

/// Finite watchdog plus concurrent stderr draining: cancellation can break pipe reads/writes.
struct Process {
    child: Arc<Mutex<Child>>,
    done: Arc<AtomicBool>,
    timed_out: Arc<AtomicBool>,
    watcher: Option<JoinHandle<()>>,
    diagnostics: Option<JoinHandle<String>>,
    stdout: Option<ChildStdout>,
    cancel: Cancellation,
}
impl Process {
    fn spawn(
        command: &mut Command,
        cancel: &Cancellation,
        timeout: Duration,
        sink: ProgressSink,
        total: Option<u64>,
    ) -> Result<Self> {
        cancel.check()?;
        let mut child = command
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        let stdout = child.stdout.take();
        let mut stderr = child
            .stderr
            .take()
            .ok_or_else(|| Error::new("process_failed", "No stderr"))?;
        let diagnostics = thread::spawn(move || {
            let mut buf = [0; 1024];
            let mut tail = Vec::new();
            let mut line = Vec::new();
            loop {
                let n = match stderr.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => n,
                    Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(_) => break,
                };
                tail.extend_from_slice(&buf[..n]);
                if tail.len() > 8192 {
                    tail.drain(..tail.len() - 8192);
                }
                for b in &buf[..n] {
                    if *b == b'\n' {
                        if let Ok(text) = std::str::from_utf8(&line) {
                            if let Some(us) = text
                                .strip_prefix("out_time_us=")
                                .and_then(|n| n.parse::<u64>().ok())
                            {
                                progress(&sink, "converting", us, total);
                            }
                        }
                        line.clear();
                    } else if line.len() < 512 {
                        line.push(*b);
                    }
                }
            }
            String::from_utf8_lossy(&tail).into_owned()
        });
        let child = Arc::new(Mutex::new(child));
        let done = Arc::new(AtomicBool::new(false));
        let timed_out = Arc::new(AtomicBool::new(false));
        let c = child.clone();
        let d = done.clone();
        let timed = timed_out.clone();
        let token = cancel.clone();
        let watcher = thread::spawn(move || {
            let started = Instant::now();
            while !d.load(Ordering::SeqCst) {
                if token.is_cancelled() || started.elapsed() > timeout {
                    if !token.is_cancelled() {
                        timed.store(true, Ordering::SeqCst);
                    }
                    if let Ok(mut child) = c.lock() {
                        let _ = child.kill();
                    }
                    break;
                }
                thread::sleep(Duration::from_millis(25));
            }
        });
        Ok(Self {
            child,
            done,
            timed_out,
            watcher: Some(watcher),
            diagnostics: Some(diagnostics),
            stdout,
            cancel: cancel.clone(),
        })
    }
    fn finish(mut self) -> Result<String> {
        let status = loop {
            let status = self
                .child
                .lock()
                .map_err(|_| Error::new("process_failed", "Poisoned process lock"))?
                .try_wait()?;
            if let Some(status) = status {
                break status;
            }
            thread::sleep(Duration::from_millis(10));
        };
        self.done.store(true, Ordering::SeqCst);
        if let Some(w) = self.watcher.take() {
            let _ = w.join();
        }
        let diagnostic = self
            .diagnostics
            .take()
            .and_then(|h| h.join().ok())
            .unwrap_or_default();
        self.cancel.check()?;
        if self.timed_out.load(Ordering::SeqCst) {
            return Err(Error::new("timeout", "Media process exceeded deadline"));
        }
        if !status.success() {
            return Err(Error::new("conversion_failed", diagnostic));
        }
        Ok(diagnostic)
    }
}
impl Drop for Process {
    fn drop(&mut self) {
        if let Ok(mut c) = self.child.lock() {
            let _ = c.kill();
            let _ = c.wait();
        }
        self.done.store(true, Ordering::SeqCst);
        if let Some(h) = self.watcher.take() {
            let _ = h.join();
        }
        if let Some(h) = self.diagnostics.take() {
            let _ = h.join();
        }
    }
}
pub(crate) fn capture(
    command: &mut Command,
    cancel: &Cancellation,
    timeout: Duration,
) -> Result<Vec<u8>> {
    let mut p = Process::spawn(command.stdin(Stdio::null()), cancel, timeout, None, None)?;
    let mut data = Vec::new();
    p.stdout
        .take()
        .ok_or_else(|| Error::new("process_failed", "No stdout"))?
        .take(1_048_577)
        .read_to_end(&mut data)?;
    if data.len() > 1_048_576 {
        return Err(Error::new(
            "process_failed",
            "Output exceeds bounded JSON/feature limit",
        ));
    }
    p.finish()?;
    Ok(data)
}
fn original_rate(value: Option<&str>) -> Option<Rate> {
    let (n, d) = value?.split_once('/')?;
    let n = n.parse::<u32>().ok()?;
    let d = d.parse::<u32>().ok()?;
    if n == 0 || d == 0 {
        None
    } else {
        Some(Rate {
            numerator: n,
            denominator: d,
        })
    }
}
pub fn probe(path: &Path, tools: &Tools, cancel: &Cancellation) -> Result<Metadata> {
    if !path.is_file()
        || path
            .extension()
            .and_then(|x| x.to_str())
            .map(|x| !x.eq_ignore_ascii_case("mp4"))
            .unwrap_or(true)
    {
        return Err(Error::new(
            "unsupported_input",
            "Select an existing local MP4 file",
        ));
    }
    let input = fs::canonicalize(path)?;
    let bytes = capture(
        Command::new(&tools.ffprobe)
            .args([
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-select_streams",
                "v:0",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
            ])
            .arg(input),
        cancel,
        Duration::from_secs(30),
    )?;
    let v: serde_json::Value = serde_json::from_slice(&bytes)?;
    let s = v["streams"]
        .as_array()
        .and_then(|a| a.first())
        .ok_or_else(|| Error::new("unsupported_input", "No video stream"))?;
    if !v["format"]["format_name"]
        .as_str()
        .unwrap_or("")
        .split(',')
        .any(|x| x == "mp4")
    {
        return Err(Error::new("unsupported_input", "Not MP4 container"));
    }
    if ["smpte2084", "arib-std-b67"].contains(&s["color_transfer"].as_str().unwrap_or("")) {
        return Err(Error::new(
            "unsupported_input",
            "HDR tone mapping is not qualified",
        ));
    }
    let width = s["width"].as_u64().unwrap_or(0);
    let height = s["height"].as_u64().unwrap_or(0);
    if width == 0 || height == 0 || width > 8192 || height > 8192 || width * height > 33_554_432 {
        return Err(Error::new(
            "unsupported_input",
            "Input dimensions exceed resource limit",
        ));
    }
    let duration = s["duration"]
        .as_str()
        .or_else(|| v["format"]["duration"].as_str())
        .and_then(|s| s.parse::<f64>().ok())
        .filter(|x| x.is_finite() && *x > 0.0 && *x < 100_000_000.0)
        .map(|x| (x * 1_000_000.0).round() as u64);
    let rotation = s["side_data_list"]
        .as_array()
        .and_then(|a| a.iter().find_map(|x| x["rotation"].as_i64()))
        .unwrap_or(0);
    Ok(Metadata {
        width: width as u32,
        height: height as u32,
        duration_us: duration,
        original_fps: original_rate(s["avg_frame_rate"].as_str()),
        nominal_fps: original_rate(s["r_frame_rate"].as_str()),
        sample_aspect_ratio: s["sample_aspect_ratio"].as_str().map(str::to_owned),
        display_aspect_ratio: s["display_aspect_ratio"].as_str().map(str::to_owned),
        rotation_deg: rotation as i32,
        codec: s["codec_name"].as_str().unwrap_or("unknown").into(),
        interlaced: matches!(s["field_order"].as_str(), Some("tt" | "bb" | "tb" | "bt")),
    })
}
pub fn estimate(metadata: &Metadata, options: &Options) -> Result<Estimate> {
    if options.width < 16
        || options.width > 320
        || !options.width.is_multiple_of(2)
        || options.height < 16
        || options.height > 240
        || !options.height.is_multiple_of(2)
    {
        return Err(Error::new(
            "invalid_parameter",
            "Encoded dimensions must be even, 16..320 by 16..240",
        ));
    }
    Rate::new(options.fps.numerator, options.fps.denominator)?;
    if !(2..=31).contains(&options.qscale) {
        return Err(Error::new("invalid_parameter", "JPEG qscale must be 2..31"));
    }
    let frames = metadata.duration_us.map(|us| {
        (u128::from(us) * u128::from(options.fps.numerator))
            .div_ceil(u128::from(options.fps.denominator) * 1_000_000)
            .min(u128::from(u64::MAX)) as u64
    });
    let bytes = frames
        .and_then(|n| n.checked_mul(jpeg::MAX_JPEG as u64 + 40))
        .and_then(|n| n.checked_add(96));
    Ok(Estimate {
        frames,
        worst_case_bytes: bytes,
        frame_cap_bytes: jpeg::MAX_JPEG,
    })
}

/// Dedicated, exclusively created job directory; failed/cancelled files stay identifiable.
pub fn convert(
    input: &Path,
    output: &Path,
    options: &Options,
    tools: &Tools,
    cancel: &Cancellation,
    sink: ProgressSink,
) -> Result<Artifact> {
    let metadata = probe(input, tools, cancel)?;
    let estimate = estimate(&metadata, options)?;
    if estimate.frames.is_some_and(|n| n > 1_000_000) {
        return Err(Error::new(
            "file_too_large",
            "Estimated frame count exceeds profile",
        ));
    }
    let features = capture(
        Command::new(&tools.ffmpeg).args(["-hide_banner", "-h", "filter=scale"]),
        cancel,
        Duration::from_secs(15),
    )?;
    if !String::from_utf8_lossy(&features).contains("reset_sar") {
        return Err(Error::new(
            "unsupported_tool",
            "FFmpeg scale filter must support reset_sar",
        ));
    }
    let version = capture(
        Command::new(&tools.ffmpeg).arg("-version"),
        cancel,
        Duration::from_secs(15),
    )?;
    let version = String::from_utf8_lossy(&version)
        .lines()
        .next()
        .unwrap_or("unknown")
        .to_owned();
    fs::create_dir(output)?;
    let result = (|| {
        let file = OpenOptions::new()
            .write(true)
            .read(true)
            .create_new(true)
            .open(output.join("video.mvb.part"))?;
        let index = OpenOptions::new()
            .write(true)
            .read(true)
            .create_new(true)
            .open(output.join("index.part"))?;
        let mut writer = Writer::new(file, index, options.fps)?;
        let filter = frame_filter(metadata.interlaced, options);
        let mut cmd = Command::new(&tools.ffmpeg);
        cmd.args([
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-nostats",
            "-progress",
            "pipe:2",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
        ])
        .arg(fs::canonicalize(input)?);
        cmd.args([
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            &filter,
            "-c:v",
            "mjpeg",
            "-q:v",
            &options.qscale.to_string(),
            "-qmin",
            &options.qscale.to_string(),
            "-qmax",
            &options.qscale.to_string(),
            "-huffman",
            "optimal",
            "-threads",
            "1",
            "-f",
            "image2pipe",
            "pipe:1",
        ])
        .stdin(Stdio::null());
        let mut process = Process::spawn(
            &mut cmd,
            cancel,
            Duration::from_secs(24 * 3600),
            sink.clone(),
            metadata.duration_us,
        )?;
        let stream = process
            .stdout
            .take()
            .ok_or_else(|| Error::new("process_failed", "No frame stream"))?;
        let mut stream = BufReader::with_capacity(65536, stream);
        loop {
            cancel.check()?;
            match jpeg::next_frame(&mut stream)? {
                Some(data) => writer.push(&data)?,
                None => break,
            }
        }
        process.finish()?;
        let header = writer.finish(cancel)?;
        progress(&sink, "verifying", 0, Some(u64::from(header.frames)));
        let file = output.join("video.mvb.part");
        verify_decoding(&file, tools, cancel, sink.clone())?;
        let sha256 = crate::storage::digest_file(&file, cancel)?;
        let artifact = Artifact {
            schema_version: 1,
            sha256,
            header,
            options: options.clone(),
            input: metadata.clone(),
            converter: version.clone(),
        };
        let mut manifest = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(output.join("manifest.json.part"))?;
        manifest.write_all(&serde_json::to_vec_pretty(&artifact)?)?;
        manifest.sync_all()?;
        cancel.check()?;
        crate::storage::rename_new(&file, &output.join("video.mvb"))?;
        crate::storage::rename_new(
            &output.join("manifest.json.part"),
            &output.join("manifest.json"),
        )?;
        progress(
            &sink,
            "complete",
            artifact.header.file_bytes,
            Some(artifact.header.file_bytes),
        );
        Ok(artifact)
    })();
    if let Err(error) = &result {
        if let Ok(mut f) = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(output.join("failure.json"))
        {
            let _ = f.write_all(&serde_json::to_vec_pretty(error).unwrap_or_default());
            let _ = f.sync_all();
        }
    }
    result
}
pub fn verify_decoding(
    path: &Path,
    tools: &Tools,
    cancel: &Cancellation,
    sink: ProgressSink,
) -> Result<Header> {
    let mut video = Video::open(File::open(path)?, cancel)?;
    let mut command = Command::new(&tools.ffmpeg);
    command
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-f",
            "mjpeg",
            "-i",
            "pipe:0",
            "-f",
            "null",
            "-",
        ])
        .stdin(Stdio::piped());
    let process = Process::spawn(
        &mut command,
        cancel,
        Duration::from_secs(24 * 3600),
        None,
        None,
    )?;
    let mut stdin = process
        .child
        .lock()
        .map_err(|_| Error::new("process_failed", "Process lock"))?
        .stdin
        .take()
        .ok_or_else(|| Error::new("process_failed", "No decode input"))?;
    let mut last = Instant::now();
    for i in 0..video.header.frames {
        cancel.check()?;
        let frame = video.frame(i)?;
        stdin.write_all(&frame)?;
        if last.elapsed() >= Duration::from_millis(100) {
            progress(
                &sink,
                "verifying",
                u64::from(i + 1),
                Some(u64::from(video.header.frames)),
            );
            last = Instant::now();
        }
    }
    drop(stdin);
    let diagnostic = process.finish()?;
    if !diagnostic.trim().is_empty() {
        return Err(Error::new("verification_failed", diagnostic));
    }
    Ok(video.header)
}
pub fn profile_limit() -> u64 {
    MAX_FILE
}

#[cfg(test)]
mod padding_tests {
    use super::*;

    fn rgb(source: &str, filter: &str) -> Vec<u8> {
        let result = Command::new("ffmpeg")
            .args([
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
            ])
            .arg(format!("testsrc2=size={source}:rate=25"))
            .args([
                "-vf",
                filter,
                "-frames:v",
                "1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ])
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        result.stdout
    }

    #[test]
    fn prepared_alignment_preserves_rgb_content_black_borders_and_explicit_bounds() {
        for (source, bounds_w, bounds_h, content_w, content_h, out_w, out_h, fit) in [
            (
                "640x360",
                320u16,
                240u16,
                320usize,
                180usize,
                320usize,
                184usize,
                Fit::Contain,
            ),
            ("240x320", 320, 240, 180, 240, 184, 240, Fit::Contain),
            ("640x360", 304, 224, 304, 172, 304, 176, Fit::Contain),
            ("640x340", 320, 240, 320, 170, 320, 176, Fit::Contain),
            ("268x480", 320, 240, 134, 240, 136, 240, Fit::Contain),
            ("640x360", 318, 238, 318, 178, 318, 178, Fit::Contain),
            ("640x360", 320, 238, 320, 180, 320, 180, Fit::Contain),
            ("640x360", 320, 240, 320, 240, 320, 240, Fit::Crop),
            ("640x480", 320, 240, 320, 240, 320, 240, Fit::Contain),
        ] {
            let options = Options {
                width: bounds_w,
                height: bounds_h,
                fit,
                ..Options::default()
            };
            let actual = rgb(source, &frame_filter(false, &options));
            let aspect = if matches!(fit, Fit::Crop) {
                "increase"
            } else {
                "decrease"
            };
            let crop = if matches!(fit, Fit::Crop) {
                format!(",crop={bounds_w}:{bounds_h}")
            } else {
                String::new()
            };
            // Independent previous pipeline provides the scaled pixel oracle.
            let reference = rgb(source, &format!(
                "fps=25/1,scale={bounds_w}:{bounds_h}:flags=bicubic:force_original_aspect_ratio={aspect}:force_divisible_by=2:reset_sar=1:out_range=full:out_color_matrix=bt601{crop},format=yuvj420p"
            ));
            assert_eq!(actual.len(), out_w * out_h * 3);
            assert_eq!(reference.len(), content_w * content_h * 3);
            let left = (out_w - content_w) / 4 * 2;
            let top = (out_h - content_h) / 4 * 2;
            assert!(out_w <= usize::from(bounds_w) && out_h <= usize::from(bounds_h));
            assert!(out_w - content_w - 2 * left <= 2);
            assert!(out_h - content_h - 2 * top <= 2);
            for y in 0..out_h {
                for x in 0..out_w {
                    let pixel = &actual[(y * out_w + x) * 3..(y * out_w + x + 1) * 3];
                    if x >= left && x < left + content_w && y >= top && y < top + content_h {
                        let index = ((y - top) * content_w + x - left) * 3;
                        assert_eq!(
                            pixel,
                            &reference[index..index + 3],
                            "{source}: content pixel {x},{y}"
                        );
                    } else {
                        assert_eq!(pixel, [0, 0, 0], "{source}: black border pixel {x},{y}");
                    }
                }
            }
        }
    }
}

#[cfg(all(test, unix))]
mod process_tests {
    use super::*;
    #[test]
    fn closed_stdout_cannot_deadlock_timeout_or_cancel() {
        for cancelled in [false, true] {
            let token = Cancellation::default();
            let trigger = token.clone();
            let thread = std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(100));
                if cancelled {
                    trigger.cancel();
                }
            });
            let start = Instant::now();
            let e = capture(
                Command::new("python3").args(["-c", "import os,time;os.close(1);time.sleep(10)"]),
                &token,
                Duration::from_millis(200),
            )
            .unwrap_err();
            assert_eq!(e.code, if cancelled { "cancelled" } else { "timeout" });
            assert!(start.elapsed() < Duration::from_secs(3));
            thread.join().unwrap();
        }
    }
}
/// A bounded local first-frame preview; independent of prepared playback timing.
pub fn thumbnail(path: &Path, tools: &Tools, cancel: &Cancellation) -> Result<Vec<u8>> {
    probe(path, tools, cancel)?;
    capture(Command::new(&tools.ffmpeg).args(["-hide_banner","-loglevel","error","-nostdin","-protocol_whitelist","file,pipe","-i"]).arg(std::fs::canonicalize(path)?).args(["-frames:v","1","-vf","scale=320:240:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1","-c:v","mjpeg","-q:v","8","-threads","1","-f","image2pipe","pipe:1"]),cancel,Duration::from_secs(30))
}
