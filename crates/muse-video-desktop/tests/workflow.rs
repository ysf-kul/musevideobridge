//! Real desktop backend integration on temporary host files. No physical SD or port.
use muse_video_core::media::Options;
use muse_video_desktop::{settings::Settings, App};
use serde_json::{json, Value};
use std::{
    fs,
    path::Path,
    process::Command,
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

fn input(root: &Path) -> std::path::PathBuf {
    let path = root.join("input.mp4");
    assert!(Command::new("ffmpeg")
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=12",
            "-t",
            "8",
            "-c:v",
            "libx264",
            "-threads",
            "1"
        ])
        .arg(&path)
        .status()
        .unwrap()
        .success());
    path
}
fn app(root: &Path, simulator: bool) -> App {
    App::new(
        root.join("config"),
        root.join("cache"),
        Arc::new(|_, _| {}),
        simulator.then(|| {
            (
                std::env::var("MVB_SIMULATOR")
                    .expect("native simulator required")
                    .into(),
                root.join("card"),
            )
        }),
    )
    .unwrap()
}
fn job(app: &App) -> Value {
    let start = Instant::now();
    loop {
        let state = app.snapshot().job;
        if state["running"] == false {
            return state;
        }
        assert!(
            start.elapsed() < Duration::from_secs(30),
            "Job did not finish: {state}"
        );
        thread::sleep(Duration::from_millis(10));
    }
}
#[test]
fn real_conversion_preview_guard_and_safe_export() {
    let root = tempfile::tempdir().unwrap();
    let source = input(root.path());
    let original = fs::read(&source).unwrap();
    let app = app(root.path(), false);
    assert_eq!(app.snapshot().settings.language, "tr");
    app.select(&source).unwrap();
    assert!(app.source_preview().unwrap().starts_with(&[255, 216]));
    app.convert(Options::default()).unwrap();
    assert_eq!(app.select(&source).err().unwrap().code, "busy");
    assert_eq!(job(&app)["stage"], "complete");
    let artifact = app.snapshot().media.unwrap().artifact.unwrap();
    assert!(app
        .frame(3, &artifact.sha256)
        .unwrap()
        .starts_with(&[255, 216]));
    assert_eq!(app.frame(0, "wrong").unwrap_err().code, "preview_mismatch");
    assert!(app.frame(artifact.header.frames, &artifact.sha256).is_err());
    let card = root.path().join("card");
    fs::create_dir(&card).unwrap();
    fs::write(card.join("unrelated.txt"), b"preserve").unwrap();
    assert_eq!(
        app.export(card.clone(), true).unwrap_err().code,
        "unsafe_path"
    );
    app.destination(&card).unwrap();
    app.export(card.clone(), true).unwrap();
    assert_eq!(job(&app)["stage"], "complete");
    assert_eq!(fs::read(card.join("unrelated.txt")).unwrap(), b"preserve");
    assert_eq!(fs::read(source).unwrap(), original);
    app.shutdown();
}
#[test]
fn shutdown_cancels_and_joins_owned_conversion() {
    let root = tempfile::tempdir().unwrap();
    let source = input(root.path());
    let app = app(root.path(), false);
    app.select(&source).unwrap();
    app.convert(Options::default()).unwrap();
    assert_eq!(app.convert(Options::default()).unwrap_err().code, "busy");
    app.shutdown();
    assert_eq!(app.snapshot().job["running"], false);
    assert_eq!(app.snapshot().job["stage"], "cancelled");
    assert_eq!(
        app.convert(Options::default()).unwrap_err().code,
        "cancelled"
    );
}
#[test]
fn invalid_settings_are_preserved_before_repair() {
    let root = tempfile::tempdir().unwrap();
    let config = root.path().join("config");
    fs::create_dir(&config).unwrap();
    fs::write(config.join("settings.json"), b"future-or-damaged-settings").unwrap();
    let app = app(root.path(), false);
    assert!(app.snapshot().startup_error.is_some());
    app.settings(Settings {
        language: "en".into(),
        ..Settings::default()
    })
    .unwrap();
    let backup = fs::read_dir(config)
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| {
            p.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("settings-unrecognized-")
        })
        .unwrap();
    assert_eq!(fs::read(backup).unwrap(), b"future-or-damaged-settings");
    app.shutdown();
}
#[test]
#[ignore = "requires separately built native simulator"]
fn actor_handshake_upload_confirmed_controls_and_reconnect() {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("card")).unwrap();
    let source = input(root.path());
    let events = Arc::new(Mutex::new(Vec::<Value>::new()));
    let sink = events.clone();
    let app = App::new(
        root.path().join("config"),
        root.path().join("cache"),
        Arc::new(move |name, payload| {
            if name == "frame" {
                let mut e = sink.lock().unwrap();
                if e.len() < 32 {
                    e.push(payload);
                }
            }
        }),
        Some((
            std::env::var("MVB_SIMULATOR").unwrap().into(),
            root.path().join("card"),
        )),
    )
    .unwrap();
    let wait = |wanted: &str| {
        let start = Instant::now();
        loop {
            let state = app.snapshot().device;
            if state["status"]["state"] == wanted && state["connection"] == "connected" {
                return state;
            }
            assert!(start.elapsed() < Duration::from_secs(15), "{state}");
            thread::sleep(Duration::from_millis(20));
        }
    };
    app.device.connect("host-test".into()).unwrap();
    let idle = wait("idle");
    assert_eq!(idle["hello"]["hardware"]["simulator"], true);
    assert!(idle["status"]["hardware"]["temperature_c"].is_null());
    app.select(&source).unwrap();
    app.convert(Options::default()).unwrap();
    assert_eq!(job(&app)["stage"], "complete");
    app.upload().unwrap();
    assert_eq!(job(&app)["stage"], "complete");
    wait("playing");
    app.command(json!({"method":"pause"})).unwrap();
    wait("paused");
    let ack=app.command(json!({"method":"set_transform","x":10,"y":5,"width":160,"height":120,"rotation_deg":180,"lock_aspect":true})).unwrap();
    let confirmed = json!({"status":app.command(json!({"method":"get_status"})).unwrap()});
    assert!(
        confirmed["status"]["state_revision"].as_u64().unwrap()
            >= ack["state_revision"].as_u64().unwrap()
    );
    assert_eq!(confirmed["status"]["transform"]["x"], 10);
    let seek = app
        .command(json!({"method":"seek","position_us":1_000_000}))
        .unwrap();
    // The pre-seek snapshot is already paused. Query confirmed status after the
    // seek receipt instead of treating that stale state as an applied seek.
    let sought = app.command(json!({"method":"get_status"})).unwrap();
    assert_eq!(sought["state"], "paused");
    assert_eq!(sought["encoded_fps"]["numerator"], 25);
    assert_eq!(sought["encoded_fps"]["denominator"], 1);
    assert_eq!(sought["frame_index"], 25);
    assert!(sought["state_revision"].as_u64().unwrap() >= seek["state_revision"].as_u64().unwrap());
    assert_eq!(
        app.command(json!({"method":"erase_flash"}))
            .unwrap_err()
            .code,
        "unsupported_command"
    );
    let old_session = app.snapshot().device["session"].clone();
    app.device.disconnect().unwrap();
    app.device.connect("host-test".into()).unwrap();
    let start = Instant::now();
    while app.snapshot().device["session"] == old_session {
        assert!(start.elapsed() < Duration::from_secs(10));
        thread::sleep(Duration::from_millis(20));
    }
    wait("playing");
    assert!(!events.lock().unwrap().is_empty());
    app.shutdown();
}
