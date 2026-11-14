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
fn other_input(root: &Path, source: &Path) -> std::path::PathBuf {
    let other = root.join("other.mp4");
    assert!(Command::new("ffmpeg")
        .args(["-hide_banner", "-loglevel", "error", "-i"])
        .arg(source)
        .args(["-vf", "hue=h=90", "-c:v", "libx264", "-threads", "1"])
        .arg(&other)
        .status()
        .unwrap()
        .success());
    other
}
#[test]
fn batch_preparation_export_and_local_library_restart() {
    let root = tempfile::tempdir().unwrap();
    let source = input(root.path());
    let other = other_input(root.path(), &source);
    let app = app(root.path(), false);
    app.select_many(&[source.clone(), other]).unwrap();
    assert_eq!(app.snapshot().sources.len(), 2);
    app.convert_many(Options::default()).unwrap();
    assert_eq!(job(&app)["stage"], "complete");
    let prepared = app.snapshot().prepared;
    assert_eq!(prepared.len(), 2);
    let ids = prepared
        .iter()
        .map(|m| m.artifact.as_ref().unwrap().sha256.clone())
        .collect::<Vec<_>>();
    assert_ne!(ids[0], ids[1]);
    assert_eq!(
        app.upload_many(vec![ids[0].clone(), ids[0].clone()])
            .unwrap_err()
            .code,
        "invalid_parameter"
    );
    let card = root.path().join("batch-card");
    fs::create_dir(&card).unwrap();
    app.destination(&card).unwrap();
    app.export_many(card.clone(), ids.clone(), true).unwrap();
    assert_eq!(job(&app)["stage"], "complete");
    let queue = muse_video_core::storage::queue::load(&card.join("muse-video")).unwrap();
    assert_eq!(queue.ids, ids);
    assert_eq!(queue.position, Some(0));
    for id in &ids {
        assert!(card.join(format!("muse-video/{id}.mvb")).is_file());
    }
    app.shutdown();
    let restored = App::new(
        root.path().join("config"),
        root.path().join("cache"),
        Arc::new(|_, _| {}),
        None,
    )
    .unwrap();
    assert_eq!(restored.snapshot().prepared.len(), 2);
    assert!(restored.frame(0, &ids[0]).unwrap().starts_with(&[255, 216]));
    restored.shutdown();
}
#[test]
#[ignore = "requires separately built native simulator"]
fn batch_usb_queue_and_mode_survive_device_restart() {
    let root = tempfile::tempdir().unwrap();
    let source = input(root.path());
    let other = other_input(root.path(), &source);
    fs::create_dir(root.path().join("card")).unwrap();
    let app = app(root.path(), true);
    app.select_many(&[source, other]).unwrap();
    app.convert_many(Options::default()).unwrap();
    assert_eq!(job(&app)["stage"], "complete");
    let ids = app
        .snapshot()
        .prepared
        .iter()
        .map(|m| m.artifact.as_ref().unwrap().sha256.clone())
        .collect::<Vec<_>>();
    app.device.connect("host-test-simulator".into()).unwrap();
    let started = Instant::now();
    while app.snapshot().device["connection"] != "connected" {
        assert!(started.elapsed() < Duration::from_secs(10));
        thread::sleep(Duration::from_millis(20));
    }
    app.upload_many(ids.clone()).unwrap();
    assert_eq!(job(&app)["stage"], "complete");
    app.command(json!({"method":"pause"})).unwrap();
    let state = app.command(json!({"method":"get_playback_state"})).unwrap();
    assert_eq!(state["queue"], json!(ids));
    assert_eq!(state["current_video_id"], ids[0]);
    assert_eq!(state["queue_persistent"], true);
    app.command(json!({"method":"set_loop_mode","mode":"loop_queue"}))
        .unwrap();
    app.device.disconnect().unwrap();
    thread::sleep(Duration::from_millis(400));
    app.device.connect("host-test-simulator".into()).unwrap();
    thread::sleep(Duration::from_millis(800));
    let restored = app.command(json!({"method":"get_playback_state"})).unwrap();
    assert_eq!(restored["queue"], json!(ids));
    assert_eq!(restored["loop_mode"], "loop_queue");
    // Already stored media is hashed/loaded, without retransmitting video bytes.
    app.upload_video(Some(ids[0].clone())).unwrap();
    let result = job(&app);
    assert_eq!(result["stage"], "complete");
    assert_eq!(
        result["result"]["videos"][0]["host_transfer"]["newly_confirmed_bytes"],
        0
    );
    app.shutdown();
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
    app.select(&source).unwrap();
    app.convert(Options {
        qscale: 12,
        ..Options::default()
    })
    .unwrap();
    assert_eq!(job(&app)["stage"], "complete");
    let second = app.snapshot().media.unwrap().artifact.unwrap();
    assert_eq!(app.snapshot().prepared.len(), 2);
    assert_ne!(second.sha256, artifact.sha256);
    assert!(app
        .frame(3, &artifact.sha256)
        .unwrap()
        .starts_with(&[255, 216]));
    assert!(app
        .frame(3, &second.sha256)
        .unwrap()
        .starts_with(&[255, 216]));
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
