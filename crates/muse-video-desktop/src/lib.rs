pub mod device;
pub mod paths;
mod prepared;
pub mod settings;
use device::{Device, Emitter};
use muse_video_core::{
    container::{Rate, Video},
    media::{self, Artifact, Fit, Options, Tools},
    storage, Cancellation, Error, Result,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use settings::Settings;
use std::{
    fs::{self, File},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
};
#[derive(Clone, Serialize, Deserialize)]
pub struct Media {
    pub filename: String,
    pub selection_id: String,
    pub metadata: media::Metadata,
    pub estimate: media::Estimate,
    pub artifact: Option<Artifact>,
}
#[derive(Clone, Serialize)]
pub struct Snapshot {
    pub settings: Settings,
    pub media: Option<Media>,
    pub prepared: Vec<Media>,
    pub sources: Vec<Media>,
    pub device: Value,
    pub job: Value,
    pub startup_error: Option<Error>,
}
struct State {
    snapshot: Snapshot,
    source: Option<PathBuf>,
    source_paths: std::collections::HashMap<String, PathBuf>,
    artifact: Option<PathBuf>,
    job: Option<Cancellation>,
    preview: Option<Video<File>>,
    destination: Option<PathBuf>,
    prepared_paths: std::collections::HashMap<String, PathBuf>,
    preview_digest: Option<String>,
}
#[derive(Clone)]
pub struct App {
    state: Arc<Mutex<State>>,
    pub device: Device,
    settings_path: PathBuf,
    cache: PathBuf,
    emit: Emitter,
    operations: Arc<Mutex<()>>,
    worker: Arc<Mutex<Option<thread::JoinHandle<()>>>>,
    closing: Arc<AtomicBool>,
    lifetime: Cancellation,
}
impl App {
    pub fn new(
        config: PathBuf,
        cache: PathBuf,
        emitter: Emitter,
        simulator: Option<(PathBuf, PathBuf)>,
    ) -> Result<Self> {
        fs::create_dir_all(&cache)?;
        let cache = fs::canonicalize(cache)?;
        let settings_path = config.join("settings.json");
        let (settings, error) = match Settings::load(&settings_path) {
            Ok(s) => (s, None),
            Err(e) => (Settings::default(), Some(e)),
        };
        let (library, library_paths, library_error) = match prepared::load(&cache) {
            Ok((media, paths)) => (media, paths, None),
            Err(e) => (Vec::new(), std::collections::HashMap::new(), Some(e)),
        };
        let state = Arc::new(Mutex::new(State {
            snapshot: Snapshot {
                settings,
                media: None,
                prepared: library,
                sources: Vec::new(),
                device: json!({"connection":"disconnected"}),
                job: Value::Null,
                startup_error: error.or(library_error),
            },
            source: None,
            source_paths: std::collections::HashMap::new(),
            artifact: None,
            job: None,
            preview: None,
            destination: None,
            prepared_paths: library_paths,
            preview_digest: None,
        }));
        let weak = Arc::downgrade(&state);
        let external = emitter.clone();
        let emit: Emitter = Arc::new(move |name, value| {
            if let Some(state) = weak.upgrade() {
                let mut s = state.lock().unwrap();
                if name == "device" {
                    if let (Some(existing), Some(next)) =
                        (s.snapshot.device.as_object_mut(), value.as_object())
                    {
                        for (k, v) in next {
                            existing.insert(k.clone(), v.clone());
                        }
                    }
                } else if name == "job" {
                    s.snapshot.job = value.clone();
                }
            }
            external(name, value);
        });
        let device = Device::start(emit.clone(), simulator);
        Ok(Self {
            state,
            device,
            settings_path,
            cache,
            emit,
            operations: Arc::new(Mutex::new(())),
            worker: Arc::new(Mutex::new(None)),
            closing: Arc::new(AtomicBool::new(false)),
            lifetime: Cancellation::default(),
        })
    }
    pub fn snapshot(&self) -> Snapshot {
        self.state.lock().unwrap().snapshot.clone()
    }
    pub fn settings(&self, next: Settings) -> Result<Settings> {
        let _operation = self.operations.lock().unwrap();
        next.save(&self.settings_path)?;
        self.state.lock().unwrap().snapshot.settings = next.clone();
        Ok(next)
    }
    fn tools(&self) -> Tools {
        let s = &self.state.lock().unwrap().snapshot.settings;
        Tools {
            ffmpeg: paths::tool(&std::env::current_exe().unwrap_or_default(), &s.ffmpeg),
            ffprobe: paths::tool(&std::env::current_exe().unwrap_or_default(), &s.ffprobe),
        }
    }
    pub fn select(&self, path: &Path) -> Result<Media> {
        Ok(self.select_many(&[path.to_path_buf()])?.remove(0))
    }
    pub fn select_many(&self, paths: &[PathBuf]) -> Result<Vec<Media>> {
        let _operation = self.operations.lock().unwrap();
        self.ensure_open()?;
        if self.state.lock().unwrap().job.is_some() {
            return Err(Error::new("busy", "Finish or cancel the current operation"));
        }
        if paths.is_empty() || paths.len() > 12 {
            return Err(Error::new(
                "invalid_parameter",
                "Select between 1 and 12 videos",
            ));
        }
        let settings = self.snapshot().settings;
        let tools = self.tools();
        let mut selected = Vec::new();
        let mut sources = std::collections::HashMap::new();
        for (index, path) in paths.iter().enumerate() {
            let path = fs::canonicalize(path)?;
            let metadata = media::probe(&path, &tools, &self.lifetime)?;
            let estimate = media::estimate(
                &metadata,
                &Options {
                    fps: Rate::new(settings.fps, 1)?,
                    qscale: settings.qscale,
                    fit: if settings.crop {
                        Fit::Crop
                    } else {
                        Fit::Contain
                    },
                    ..Options::default()
                },
            )?;
            let item = Media {
                selection_id: format!("{}-{index}", device::now_ms()),
                filename: path
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into(),
                metadata,
                estimate,
                artifact: None,
            };
            sources.insert(item.selection_id.clone(), path);
            selected.push(item);
        }
        let mut state = self.state.lock().unwrap();
        state.source = sources.get(&selected[0].selection_id).cloned();
        state.source_paths = sources;
        state.artifact = None;
        state.preview = None;
        state.preview_digest = None;
        state.snapshot.media = Some(selected[0].clone());
        state.snapshot.sources = selected.clone();
        Ok(selected)
    }
    pub fn estimate(&self, options: &Options) -> Result<media::Estimate> {
        let s = self.state.lock().unwrap();
        media::estimate(
            &s.snapshot
                .media
                .as_ref()
                .ok_or_else(|| Error::new("unsupported_input", "No video selected"))?
                .metadata,
            options,
        )
    }
    pub fn destination(&self, path: &Path) -> Result<storage::Destination> {
        let _operation = self.operations.lock().unwrap();
        self.ensure_open()?;
        let info = storage::inspect(path)?;
        self.state.lock().unwrap().destination = Some(fs::canonicalize(path)?);
        Ok(info)
    }
    fn start_job<F>(&self, kind: &str, operation: F) -> Result<()>
    where
        F: FnOnce(App, Cancellation, media::ProgressSink) -> Result<Value> + Send + 'static,
    {
        self.ensure_open()?;
        if self.state.lock().unwrap().job.is_some() {
            return Err(Error::new("busy", "Another operation is running"));
        }
        if let Some(worker) = self.worker.lock().unwrap().take() {
            worker
                .join()
                .map_err(|_| Error::new("io_error", "Previous job panicked"))?;
        }
        let cancel = Cancellation::default();
        {
            let mut s = self.state.lock().unwrap();
            if s.job.is_some() {
                return Err(Error::new("busy", "Another operation is running"));
            }
            s.job = Some(cancel.clone());
        }
        let id = format!("{}", device::now_ms());
        (self.emit)(
            "job",
            json!({"id":id,"kind":kind,"running":true,"stage":"starting","completed":0}),
        );
        let app = self.clone();
        let kind = kind.to_owned();
        let worker = thread::spawn(move || {
            let (tx, rx) = mpsc::sync_channel::<media::Progress>(16);
            let emit = app.emit.clone();
            let progress_id = id.clone();
            let progress_kind = kind.clone();
            let watcher = thread::spawn(move || {
                for p in rx {
                    emit(
                        "job",
                        json!({"id":progress_id,"kind":progress_kind,"running":true,"stage":p.stage,"completed":p.completed,"total":p.total,
                            "bytes_per_second":p.bytes_per_second,"remaining_seconds":p.remaining_seconds,
                            "item_index":p.item_index,"items_total":p.items_total,"filename":p.filename}),
                    );
                }
            });
            let result = operation(app.clone(), cancel, Some(tx));
            let _ = watcher.join();
            app.state.lock().unwrap().job = None;
            match result {
                Ok(value) => (app.emit)(
                    "job",
                    json!({"id":id,"kind":kind,"running":false,"stage":"complete","result":value}),
                ),
                Err(error) => (app.emit)(
                    "job",
                    json!({"id":id,"kind":kind,"running":false,"stage":if error.code=="cancelled"{"cancelled"}else{"error"},"error":error}),
                ),
            };
        });
        *self.worker.lock().unwrap() = Some(worker);
        Ok(())
    }
    fn item<T>(
        sink: media::ProgressSink,
        index: usize,
        total: usize,
        filename: String,
        operation: impl FnOnce(media::ProgressSink) -> Result<T>,
    ) -> Result<T> {
        let (tx, rx) = mpsc::sync_channel::<media::Progress>(16);
        let label = filename.clone();
        let watcher = thread::spawn(move || {
            for mut p in rx {
                p.item_index = Some(index + 1);
                p.items_total = Some(total);
                p.filename = Some(label.clone());
                if let Some(sender) = &sink {
                    let _ = sender.try_send(p);
                }
            }
        });
        let result = operation(Some(tx));
        let _ = watcher.join();
        result.map_err(|mut e| {
            e.details = format!(
                "Video {}/{total} ({filename}): {}. Completed videos are retained",
                index + 1,
                e.details
            );
            e
        })
    }
    pub fn convert(&self, options: Options) -> Result<()> {
        self.convert_sources(options, false)
    }
    pub fn convert_many(&self, options: Options) -> Result<()> {
        self.convert_sources(options, true)
    }
    fn convert_sources(&self, options: Options, all: bool) -> Result<()> {
        let _operation = self.operations.lock().unwrap();
        let selected = {
            let state = self.state.lock().unwrap();
            let items = if all {
                state.snapshot.sources.clone()
            } else {
                state.snapshot.media.clone().into_iter().collect()
            };
            if items.is_empty() {
                return Err(Error::new("unsupported_input", "Select videos first"));
            }
            items
                .into_iter()
                .map(|m| {
                    let path = state
                        .source_paths
                        .get(&m.selection_id)
                        .cloned()
                        .ok_or_else(|| {
                            Error::new("unsupported_input", "Source no longer selected")
                        })?;
                    Ok((m, path))
                })
                .collect::<Result<Vec<_>>>()?
        };
        let tools = self.tools();
        self.start_job("conversion", move |app, cancel, sink| {
            let count = selected.len();
            let mut results = Vec::new();
            for (index, (mut item, source)) in selected.into_iter().enumerate() {
                cancel.check()?;
                let output = app.cache.join(format!("job-{}-{index}", device::now_ms()));
                let artifact = Self::item(
                    sink.clone(),
                    index,
                    count,
                    item.filename.clone(),
                    |progress| {
                        media::convert(&source, &output, &options, &tools, &cancel, progress)
                    },
                )?;
                cancel.check()?;
                item.artifact = Some(artifact.clone());
                {
                    let mut state = app.state.lock().unwrap();
                    state.preview = None;
                    state.preview_digest = None;
                    state.source = Some(source);
                    state.artifact = Some(output.join("video.mvb"));
                    state.snapshot.media = Some(item.clone());
                    if let Some(m) = state
                        .snapshot
                        .sources
                        .iter_mut()
                        .find(|m| m.selection_id == item.selection_id)
                    {
                        *m = item.clone();
                    }
                    state.snapshot.prepared.retain(|m| {
                        m.artifact
                            .as_ref()
                            .is_none_or(|a| a.sha256 != artifact.sha256)
                    });
                    state.snapshot.prepared.push(item);
                    state
                        .prepared_paths
                        .insert(artifact.sha256.clone(), output.join("video.mvb"));
                    prepared::save(&app.cache, &state.snapshot.prepared, &state.prepared_paths)?;
                }
                (app.emit)("media", serde_json::to_value(app.snapshot().media)?);
                (app.emit)("sources", serde_json::to_value(app.snapshot().sources)?);
                (app.emit)("prepared", serde_json::to_value(app.snapshot().prepared)?);
                results.push(artifact);
            }
            Ok(if count == 1 {
                serde_json::to_value(&results[0])?
            } else {
                json!({"videos": results})
            })
        })
    }
    fn prepared_items(&self, ids: &[String]) -> Result<Vec<(String, PathBuf)>> {
        if ids.is_empty() || ids.len() > 12 {
            return Err(Error::new(
                "invalid_parameter",
                "Choose between 1 and 12 prepared videos",
            ));
        }
        let state = self.state.lock().unwrap();
        let mut seen = std::collections::HashSet::new();
        ids.iter()
            .map(|id| {
                if !seen.insert(id) {
                    return Err(Error::new("invalid_parameter", "Duplicate prepared video"));
                }
                let path = state
                    .prepared_paths
                    .get(id)
                    .cloned()
                    .ok_or_else(|| Error::new("invalid_container", "Unknown prepared video"))?;
                let filename = state
                    .snapshot
                    .prepared
                    .iter()
                    .find(|m| m.artifact.as_ref().is_some_and(|a| &a.sha256 == id))
                    .map(|m| m.filename.clone())
                    .unwrap_or_else(|| id.clone());
                Ok((filename, path))
            })
            .collect()
    }
    pub fn export(&self, destination: PathBuf, test_directory: bool) -> Result<()> {
        let id = self
            .state
            .lock()
            .unwrap()
            .snapshot
            .media
            .as_ref()
            .and_then(|m| m.artifact.as_ref())
            .map(|a| a.sha256.clone())
            .ok_or_else(|| Error::new("invalid_container", "Convert a video first"))?;
        self.export_many(destination, vec![id], test_directory)
    }
    pub fn export_many(
        &self,
        destination: PathBuf,
        ids: Vec<String>,
        test_directory: bool,
    ) -> Result<()> {
        let _operation = self.operations.lock().unwrap();
        let destination = fs::canonicalize(destination)?;
        if self.state.lock().unwrap().destination.as_ref() != Some(&destination) {
            return Err(Error::new(
                "unsafe_path",
                "Select the destination through the native dialog first",
            ));
        }
        let items = self.prepared_items(&ids)?;
        self.start_job("export", move |_, cancel, sink| {
            let count = items.len();
            let mut results = Vec::new();
            for (index, (filename, source)) in items.into_iter().enumerate() {
                cancel.check()?;
                let selection = Self::item(sink.clone(), index, count, filename, |progress| {
                    storage::export(&source, &destination, test_directory, &cancel, progress)
                })?;
                results.push(selection);
            }
            Ok(json!({"videos":results}))
        })
    }
    pub fn upload(&self) -> Result<()> {
        self.upload_video(None)
    }
    pub fn upload_video(&self, video_id: Option<String>) -> Result<()> {
        let id = match video_id {
            Some(id) => id,
            None => self
                .state
                .lock()
                .unwrap()
                .snapshot
                .media
                .as_ref()
                .and_then(|m| m.artifact.as_ref())
                .map(|a| a.sha256.clone())
                .ok_or_else(|| Error::new("invalid_container", "Convert a video first"))?,
        };
        self.upload_many(vec![id])
    }
    pub fn upload_many(&self, ids: Vec<String>) -> Result<()> {
        let _operation = self.operations.lock().unwrap();
        let items = self.prepared_items(&ids)?;
        self.start_job("upload", move |app, cancel, sink| {
            let state = app.device.command(json!({"method":"get_playback_state"}))?;
            let mut queue: Vec<String> = serde_json::from_value(state["queue"].clone())?;
            for id in &ids {
                if !queue.contains(id) {
                    queue.push(id.clone());
                }
            }
            if queue.len() > 12 {
                return Err(Error::new(
                    "queue_full",
                    "Selected videos exceed the device's 12-video queue",
                ));
            }
            let count = items.len();
            let mut results = Vec::new();
            for (index, (filename, source)) in items.into_iter().enumerate() {
                cancel.check()?;
                let result = Self::item(sink.clone(), index, count, filename, |progress| {
                    app.device.upload(source, cancel.clone(), progress)
                })?;
                results.push(result);
            }
            if count > 1 {
                // Start a completed batch at its first selected video. Readiness
                // is confirmed by the engine, just as for each upload above.
                app.device
                    .command(json!({"method":"select_video","video_id":ids[0]}))?;
                let deadline =
                    std::time::Instant::now() + std::time::Duration::from_secs(24 * 3600);
                loop {
                    cancel.check()?;
                    let status = app.device.command(json!({"method":"get_status"}))?;
                    if status["content_sha256"] == ids[0]
                        && ["playing", "paused", "ended"]
                            .contains(&status["state"].as_str().unwrap_or(""))
                    {
                        break;
                    }
                    if status["state"] == "error" {
                        return Err(Error::new(
                            status["error"].as_str().unwrap_or("verification_failed"),
                            "First queue video could not be prepared",
                        ));
                    }
                    let preparation = &status["preparation"];
                    if let Some(sender) = &sink {
                        let _ = sender.try_send(media::Progress {
                            stage: preparation["stage"]
                                .as_str()
                                .unwrap_or("preparing_playback")
                                .into(),
                            completed: preparation["percent"].as_u64().unwrap_or(0),
                            total: preparation["percent"].as_u64().map(|_| 100),
                            bytes_per_second: None,
                            remaining_seconds: None,
                            item_index: Some(1),
                            items_total: Some(count),
                            filename: None,
                        });
                    }
                    if std::time::Instant::now() > deadline {
                        return Err(Error::new("timeout", "Queue playback preparation"));
                    }
                    thread::sleep(std::time::Duration::from_millis(100));
                }
            }
            // Each publication updates the device-owned queue. Use its final confirmed state.
            let queue = app.device.command(json!({"method":"get_playback_state"}))?;
            Ok(json!({"videos":results,"playback":queue}))
        })
    }
    pub fn source_preview(&self) -> Result<Vec<u8>> {
        let path = self
            .state
            .lock()
            .unwrap()
            .source
            .clone()
            .ok_or_else(|| Error::new("unsupported_input", "No source"))?;
        media::thumbnail(&path, &self.tools(), &self.lifetime)
    }
    pub fn frame(&self, index: u32, digest: &str) -> Result<Vec<u8>> {
        let mut s = self.state.lock().unwrap();
        let path = s.prepared_paths.get(digest).cloned().ok_or_else(|| {
            Error::new(
                "preview_mismatch",
                "Device video has no local prepared preview",
            )
        })?;
        if s.preview_digest.as_deref() != Some(digest) {
            if storage::digest_file(&path, &self.lifetime)? != digest {
                return Err(Error::new(
                    "preview_mismatch",
                    "Cached video changed; prepare it again",
                ));
            }
            s.preview = Some(Video::open(File::open(path)?, &self.lifetime)?);
            s.preview_digest = Some(digest.to_owned());
        }
        s.preview
            .as_mut()
            .ok_or_else(|| Error::new("invalid_container", "Preview reader unavailable"))?
            .frame(index)
    }
    pub fn command(&self, cmd: Value) -> Result<Value> {
        let method = cmd["method"].as_str().unwrap_or("");
        if ![
            "play",
            "pause",
            "resume",
            "stop",
            "restart",
            "seek",
            "set_transform",
            "set_position",
            "set_size",
            "set_scale",
            "set_rotation",
            "set_fps",
            "get_status",
            "get_playback_state",
            "set_queue",
            "set_loop_mode",
            "select_video",
            "next_video",
            "previous_video",
        ]
        .contains(&method)
        {
            return Err(Error::new(
                "unsupported_command",
                "Desktop command allowlist",
            ));
        }
        self.device.command(cmd)
    }
    pub fn cancel(&self) {
        if let Some(c) = &self.state.lock().unwrap().job {
            c.cancel();
        }
    }
    fn ensure_open(&self) -> Result<()> {
        if self.closing.load(Ordering::Acquire) {
            return Err(Error::new("cancelled", "Application closing"));
        }
        Ok(())
    }
    pub fn shutdown(&self) {
        self.closing.store(true, Ordering::Release);
        self.lifetime.cancel();
        let _operation = self.operations.lock().unwrap();
        self.cancel();
        if let Some(worker) = self.worker.lock().unwrap().take() {
            let _ = worker.join();
        }
        self.device.shutdown();
    }
}
