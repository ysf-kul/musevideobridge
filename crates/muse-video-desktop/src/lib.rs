pub mod device;
pub mod paths;
pub mod settings;
use device::{Device, Emitter};
use muse_video_core::{
    container::{Rate, Video},
    media::{self, Artifact, Fit, Options, Tools},
    storage, Cancellation, Error, Result,
};
use serde::Serialize;
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
#[derive(Clone, Serialize)]
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
    pub device: Value,
    pub job: Value,
    pub startup_error: Option<Error>,
}
struct State {
    snapshot: Snapshot,
    source: Option<PathBuf>,
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
        let settings_path = config.join("settings.json");
        let (settings, error) = match Settings::load(&settings_path) {
            Ok(s) => (s, None),
            Err(e) => (Settings::default(), Some(e)),
        };
        let state = Arc::new(Mutex::new(State {
            snapshot: Snapshot {
                settings,
                media: None,
                prepared: Vec::new(),
                device: json!({"connection":"disconnected"}),
                job: Value::Null,
                startup_error: error,
            },
            source: None,
            artifact: None,
            job: None,
            preview: None,
            destination: None,
            prepared_paths: std::collections::HashMap::new(),
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
        let _operation = self.operations.lock().unwrap();
        self.ensure_open()?;
        if self.state.lock().unwrap().job.is_some() {
            return Err(Error::new("busy", "Finish or cancel the current operation"));
        }
        let metadata = media::probe(path, &self.tools(), &self.lifetime)?;
        let s = self.snapshot().settings;
        let estimate = media::estimate(
            &metadata,
            &Options {
                fps: Rate::new(s.fps, 1)?,
                qscale: s.qscale,
                fit: if s.crop { Fit::Crop } else { Fit::Contain },
                ..Options::default()
            },
        )?;
        let media = Media {
            selection_id: format!(
                "{}",
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_nanos()
            ),
            filename: path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into(),
            metadata,
            estimate,
            artifact: None,
        };
        let mut state = self.state.lock().unwrap();
        state.source = Some(fs::canonicalize(path)?);
        state.artifact = None;
        state.preview = None;
        state.preview_digest = None;
        state.destination = None;
        state.snapshot.media = Some(media.clone());
        Ok(media)
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
                        json!({"id":progress_id,"kind":progress_kind,"running":true,"stage":p.stage,"completed":p.completed,"total":p.total}),
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
    pub fn convert(&self, options: Options) -> Result<()> {
        let _operation = self.operations.lock().unwrap();
        let source = self
            .state
            .lock()
            .unwrap()
            .source
            .clone()
            .ok_or_else(|| Error::new("unsupported_input", "No video selected"))?;
        let tools = self.tools();
        let output = self.cache.join(format!("job-{}", device::now_ms()));
        self.start_job("conversion", move |app, cancel, sink| {
            let artifact = media::convert(&source, &output, &options, &tools, &cancel, sink)?;
            if cancel.is_cancelled() {
                return Err(Error::new("cancelled", "Application closing"));
            }
            let preview = Video::open(File::open(output.join("video.mvb"))?, &cancel)?;
            {
                let mut s = app.state.lock().unwrap();
                s.preview = Some(preview);
                s.preview_digest = Some(artifact.sha256.clone());
                s.artifact = Some(output.join("video.mvb"));
                if let Some(m) = s.snapshot.media.as_mut() {
                    m.artifact = Some(artifact.clone());
                }
                let prepared = s.snapshot.media.clone().unwrap();
                s.snapshot.prepared.retain(|m| {
                    m.artifact
                        .as_ref()
                        .is_none_or(|a| a.sha256 != artifact.sha256)
                });
                s.snapshot.prepared.push(prepared);
                s.prepared_paths
                    .insert(artifact.sha256.clone(), output.join("video.mvb"));
            }
            (app.emit)("media", serde_json::to_value(app.snapshot().media)?);
            (app.emit)("prepared", serde_json::to_value(app.snapshot().prepared)?);
            Ok(serde_json::to_value(artifact)?)
        })
    }
    pub fn export(&self, destination: PathBuf, test_directory: bool) -> Result<()> {
        let _operation = self.operations.lock().unwrap();
        let destination = fs::canonicalize(destination)?;
        if self.state.lock().unwrap().destination.as_ref() != Some(&destination) {
            return Err(Error::new(
                "unsafe_path",
                "Select the destination through the native dialog first",
            ));
        }
        let source = self.artifact()?;
        self.start_job("export", move |_, cancel, sink| {
            Ok(serde_json::to_value(storage::export(
                &source,
                &destination,
                test_directory,
                &cancel,
                sink,
            )?)?)
        })
    }
    pub fn upload(&self) -> Result<()> {
        self.upload_video(None)
    }
    pub fn upload_video(&self, video_id: Option<String>) -> Result<()> {
        let _operation = self.operations.lock().unwrap();
        let source = match video_id {
            Some(id) => self
                .state
                .lock()
                .unwrap()
                .prepared_paths
                .get(&id)
                .cloned()
                .ok_or_else(|| Error::new("invalid_container", "Unknown prepared video"))?,
            None => self.artifact()?,
        };
        self.start_job("upload", move |app, cancel, sink| {
            app.device.upload(source, cancel, sink)
        })
    }
    fn artifact(&self) -> Result<PathBuf> {
        self.state
            .lock()
            .unwrap()
            .artifact
            .clone()
            .ok_or_else(|| Error::new("invalid_container", "Convert a video first"))
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
