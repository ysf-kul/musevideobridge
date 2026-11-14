//! Explicit real-device acceptance through the production desktop backend.
//! No flashing, formatting, media deletion, or synthetic touch commands.
use muse_video_core::{media::Options, Error, Result};
use muse_video_desktop::App;
use serde_json::{json, Value};
use std::{
    fs::OpenOptions,
    io::Write,
    path::PathBuf,
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

fn wait(app: &App, predicate: impl Fn(&Value) -> bool) -> Result<Value> {
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        let snapshot = serde_json::to_value(app.snapshot())?;
        if predicate(&snapshot) {
            return Ok(snapshot);
        }
        if snapshot["job"]["stage"] == "error" {
            return Err(Error::new("verification_failed", &snapshot["job"]));
        }
        if Instant::now() >= deadline {
            return Err(Error::new("timeout", snapshot));
        }
        thread::sleep(Duration::from_millis(25));
    }
}
fn run() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let expect_no_touch = args.get(4).is_some_and(|arg| arg == "--expect-no-touch");
    if args.len() != 4 && !(args.len() == 5 && expect_no_touch) {
        return Err(Error::new(
            "invalid_parameter",
            "Specify PORT FIRST.mp4 SECOND.mp4 NEW-OUTPUT.jsonl [--expect-no-touch]",
        ));
    }
    let file = Arc::new(Mutex::new(
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&args[3])?,
    ));
    let output = file.clone();
    let root = tempfile::tempdir()?;
    let app = App::new(
        root.path().join("config"),
        root.path().join("cache"),
        Arc::new(move |name, payload| {
            writeln!(
                output.lock().unwrap(),
                "{}",
                json!({"event":name,"payload":payload})
            )
            .expect("Write hardware evidence");
        }),
        None,
    )?;
    let check = (|| {
        app.device.connect(args[0].clone())?;
        let connected = wait(&app, |s| s["device"]["connection"] == "connected")?;
        if connected["device"]["hello"]["capabilities"]["playback_queue"] != true {
            return Err(Error::new(
                "unsupported_firmware",
                "Install queue firmware after verified backup",
            ));
        }
        // A research card may boot a long historical clip. Select its first
        // queue entry through the legacy interruptible load path before testing.
        let first = connected["device"]["status"]["queue"][0].as_str();
        if let Some(id) = first {
            let generation = connected["device"]["status"]["playhead_generation"]
                .as_u64()
                .unwrap_or(0);
            app.command(json!({"method":"select_video","sha256":id}))?;
            wait(&app, |s| {
                s["device"]["status"]["state"] == "playing"
                    && s["device"]["status"]["playhead_generation"]
                        .as_u64()
                        .is_some_and(|g| g > generation)
                    && s["device"]["status"]["current_video_id"] == id
            })?;
            app.command(json!({"method":"pause"}))?;
        }
        let initial = app.command(json!({"method":"get_playback_state"}))?;
        // Leave room for the fixtures on research cards containing many old videos.
        // Queue replacement never deletes their SD files.
        if initial["queue"]
            .as_array()
            .is_some_and(|queue| queue.len() > 10)
        {
            let current: Vec<Value> = initial["current_video_id"]
                .as_str()
                .map(|id| vec![json!(id)])
                .unwrap_or_default();
            app.command(json!({"method":"set_queue","ids":current,
                "expected_queue_revision":initial["queue_revision"]}))?;
        }
        app.select_many(&args[1..3].iter().map(PathBuf::from).collect::<Vec<_>>())?;
        app.convert_many(Options {
            fps: muse_video_core::container::Rate::new(10, 1)?,
            ..Options::default()
        })?;
        let prepared = wait(&app, |s| {
            s["job"]["stage"] == "complete" && s["job"]["running"] == false
        })?;
        let ids: Vec<String> = prepared["prepared"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m["artifact"]["sha256"].as_str().unwrap().to_owned())
            .collect();
        app.upload_many(ids.clone())?;
        let uploaded = wait(&app, |s| {
            s["job"]["stage"] == "complete" && s["job"]["running"] == false
        })?;
        if uploaded["job"]["result"]["playback"]["current_video_id"] != ids[0] {
            return Err(Error::new(
                "verification_failed",
                "Batch did not select its first video",
            ));
        }
        let snapshot = wait(&app, |s| {
            s["device"]["status"]["queue"]
                .as_array()
                .is_some_and(|q| q.len() >= 2)
        })?;
        if !ids.iter().all(|id| {
            snapshot["device"]["status"]["queue"]
                .as_array()
                .unwrap()
                .contains(&json!(id))
        }) {
            return Err(Error::new(
                "verification_failed",
                "Batch queue is incomplete",
            ));
        }
        let command = |value: Value| -> Result<Value> {
            let reply = app.command(value.clone());
            writeln!(
                file.lock().unwrap(),
                "{}",
                json!({"event":"command","request":value,"reply":reply})
            )?;
            let reply = reply?;
            // A matching video/state may still be an earlier snapshot. Match
            // the acknowledged revision before observing the next transition,
            // just as the production queue UI waits for confirmation.
            if let Some(revision) = reply["state_revision"].as_u64() {
                wait(&app, |s| {
                    s["device"]["status"]["state_revision"]
                        .as_u64()
                        .is_some_and(|current| current >= revision)
                })?;
            }
            Ok(reply)
        };
        let state = command(json!({"method":"get_playback_state"}))?;
        command(
            json!({"method":"set_queue","ids":ids,"expected_queue_revision":state["queue_revision"]}),
        )?;
        command(json!({"method":"select_video","video_id":ids[0]}))?;
        wait(&app, |s| {
            s["device"]["status"]["state"] == "playing"
                && s["device"]["status"]["content_sha256"] == ids[0]
        })?;
        command(json!({"method":"pause"}))?;
        wait(&app, |s| s["device"]["status"]["state"] == "paused")?;
        command(json!({"method":"next_video"}))?;
        wait(&app, |s| {
            s["device"]["status"]["state"] == "paused"
                && s["device"]["status"]["current_video_id"] == ids[1]
        })?;
        command(json!({"method":"previous_video"}))?;
        wait(&app, |s| {
            s["device"]["status"]["state"] == "paused"
                && s["device"]["status"]["current_video_id"] == ids[0]
        })?;
        let state = command(json!({"method":"get_playback_state"}))?;
        let reversed: Vec<_> = ids.iter().rev().cloned().collect();
        command(
            json!({"method":"set_queue","ids":reversed,"expected_queue_revision":state["queue_revision"]}),
        )?;
        wait(&app, |s| {
            s["device"]["status"]["queue"] == json!(reversed)
                && s["device"]["status"]["queue_position"] == 1
                && s["device"]["status"]["state"] == "paused"
        })?;
        let state = command(json!({"method":"get_playback_state"}))?;
        command(
            json!({"method":"set_queue","ids":[ids[0]],"expected_queue_revision":state["queue_revision"]}),
        )?;
        wait(&app, |s| {
            s["device"]["status"]["queue"] == json!([ids[0]])
                && s["device"]["status"]["current_video_id"] == ids[0]
        })?;
        let state = command(json!({"method":"get_playback_state"}))?;
        // Restore the still-prepared SD item without transferring it again.
        command(
            json!({"method":"set_queue","ids":ids,"expected_queue_revision":state["queue_revision"]}),
        )?;
        for mode in ["loop_current", "loop_queue", "play_once"] {
            command(json!({"method":"set_loop_mode","mode":mode}))?;
            wait(&app, |s| s["device"]["status"]["loop_mode"] == mode)?;
            command(json!({"method":"select_video","video_id":ids[1]}))?;
            wait(&app, |s| {
                s["device"]["status"]["state"] == "playing"
                    && s["device"]["status"]["content_sha256"] == ids[1]
            })?;
            let duration = app.snapshot().prepared[1]
                .artifact
                .as_ref()
                .unwrap()
                .header
                .duration_us;
            command(json!({"method":"seek","position_us":duration.saturating_sub(100000)}))?;
            thread::sleep(Duration::from_millis(350));
            match mode {
                "loop_current" => {
                    wait(&app, |s| {
                        s["device"]["status"]["state"] == "playing"
                            && s["device"]["status"]["position_us"]
                                .as_u64()
                                .is_some_and(|p| p < duration - 100000)
                    })?;
                }
                "loop_queue" => {
                    wait(&app, |s| {
                        s["device"]["status"]["current_video_id"] == ids[0]
                            && s["device"]["status"]["state"] == "playing"
                    })?;
                }
                _ => {
                    wait(&app, |s| s["device"]["status"]["state"] == "ended")?;
                }
            }
            command(json!({"method":"pause"}))?;
        }
        command(json!({"method":"set_loop_mode","mode":"loop_queue"}))?;
        if expect_no_touch {
            let telemetry = app.device.command(json!({"method":"get_telemetry"}))?;
            if telemetry["hardware"]["touch_contacts_accepted"] != 0 {
                let diagnostics = app
                    .device
                    .command(json!({"method":"get_serial_diagnostics"}))?;
                return Err(Error::new(
                    "verification_failed",
                    format!("Idle touchscreen accepted contacts: {diagnostics}"),
                ));
            }
        }
        let before = command(json!({"method":"get_playback_state"}))?;
        let before_uptime = app.snapshot().device["status"]["hardware"]["uptime_us"]
            .as_u64()
            .unwrap_or(0);
        app.device.disconnect()?;
        wait(&app, |s| s["device"]["connection"] == "disconnected")?;
        app.device.connect(args[0].clone())?;
        let after = wait(&app, |s| s["device"]["connection"] == "connected")?;
        let restarted = after["device"]["hello"]["hardware"]["uptime_us"]
            .as_u64()
            .unwrap_or(u64::MAX)
            < before_uptime;
        if before["queue"] != after["device"]["status"]["queue"]
            || after["device"]["status"]["loop_mode"] != before["loop_mode"]
        {
            return Err(Error::new(
                "verification_failed",
                "Reconnect did not restore the device's saved queue/mode",
            ));
        }
        if expect_no_touch {
            let telemetry = app.device.command(json!({"method":"get_telemetry"}))?;
            if telemetry["hardware"]["touch_contacts_accepted"] != 0 {
                return Err(Error::new(
                    "verification_failed",
                    "Restarted idle touchscreen accepted contacts",
                ));
            }
        }
        writeln!(
            file.lock().unwrap(),
            "{}",
            json!({"event":"acceptance_complete","reconnect_device_restarted":restarted,"idle_touch_verified":expect_no_touch,"physical_touch":"unverified","physical_visual":"unverified","platform":"Linux","interfaces":"production desktop backend and real USB firmware"})
        )?;
        Ok(())
    })();
    app.shutdown();
    check
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{}", json!({"error":error}));
        std::process::exit(1);
    }
}
