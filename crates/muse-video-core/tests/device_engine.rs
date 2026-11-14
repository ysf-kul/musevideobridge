//! These tests use the real shared C++ engine and JPEGDEC on a host test directory.
//! They intentionally do not claim physical ESP32/SPI/SD-card validation.
use muse_video_core::{
    container::Rate,
    media::{self, Options, Tools},
    protocol::Frame,
    serial::{Client, Simulator},
    storage, Cancellation,
};
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::Command,
    thread,
    time::{Duration, Instant},
};
fn simulator() -> PathBuf {
    PathBuf::from(
        std::env::var("MVB_SIMULATOR").expect("Set MVB_SIMULATOR to the native build path"),
    )
}
fn video(root: &Path) -> PathBuf {
    let source = root.join("input.mp4");
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
            "4",
            "-c:v",
            "libx264",
            "-threads",
            "1"
        ])
        .arg(&source)
        .status()
        .unwrap()
        .success());
    media::convert(
        &source,
        &root.join("job"),
        &Options {
            // This control fixture has intentional frame-10 seek assertions.
            fps: Rate::new(10, 1).unwrap(),
            ..Options::default()
        },
        &Tools::default(),
        &Cancellation::default(),
        None,
    )
    .unwrap();
    root.join("job/video.mvb")
}
fn wait_state<T: Read + Write>(client: &mut Client<T>, wanted: &str) -> Value {
    let start = Instant::now();
    loop {
        let s = client.request(json!({"method":"get_status"})).unwrap();
        if s["state"] == wanted {
            return s;
        }
        assert!(start.elapsed() < Duration::from_secs(10), "State: {s}");
        thread::sleep(Duration::from_millis(20));
    }
}
#[test]
#[ignore = "requires separately built native simulator"]
fn legacy_active_selection_stays_in_a_full_discovered_sd_queue() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let card = tempfile::tempdir().unwrap();
    let selection =
        storage::export(&path, card.path(), true, &Cancellation::default(), None).unwrap();
    let library = card.path().join("muse-video");
    // Emulate an old firmware card with an active selection and no queue record.
    fs::remove_file(library.join("queue.1.bin")).unwrap();
    for index in 0..12 {
        fs::write(
            library.join(format!("{index:064x}.mvb")),
            b"unselected legacy file",
        )
        .unwrap();
    }
    let mut client = Client::new(Simulator::spawn(&simulator(), card.path()).unwrap());
    client.hello().unwrap();
    wait_state(&mut client, "playing");
    let state = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    assert_eq!(state["queue"].as_array().unwrap().len(), 12);
    assert_eq!(state["queue"][0], selection.sha256);
    assert_eq!(state["current_video_id"], selection.sha256);
    assert_eq!(state["queue_position"], 0);
}
#[test]
#[ignore = "requires separately built native simulator"]
fn queue_order_navigation_modes_preparation_and_reconnect_are_device_owned() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let other = source.path().join("other");
    media::convert(
        &source.path().join("input.mp4"),
        &other,
        &Options {
            fps: Rate::new(10, 1).unwrap(),
            qscale: 12,
            ..Options::default()
        },
        &Tools::default(),
        &Cancellation::default(),
        None,
    )
    .unwrap();
    let card = tempfile::tempdir().unwrap();
    let mut client = Client::new(Simulator::spawn(&simulator(), card.path()).unwrap());
    assert_eq!(
        client.hello().unwrap()["capabilities"]["playback_queue"],
        true
    );
    let (tx, rx) = std::sync::mpsc::sync_channel(4096);
    let first = client
        .upload(&path, &Cancellation::default(), Some(tx))
        .unwrap();
    let progress: Vec<_> = rx.try_iter().collect();
    assert!(progress
        .iter()
        .any(|p| p.stage == "uploading" && p.completed > 0 && p.total.is_some()));
    // The host simulator can finish indeterminate stages between two polls.
    // If observed, their progress must never carry a fictitious denominator.
    assert!(progress
        .iter()
        .filter(|p| ["validating_video", "preparing_playback"].contains(&p.stage.as_str()))
        .all(|p| p.total.is_none()));
    assert_eq!(first["playback"]["preparation"]["stage"], "ready");
    let second = client
        .upload(&other.join("video.mvb"), &Cancellation::default(), None)
        .unwrap();
    let a = first["sha256"].as_str().unwrap();
    let b = second["sha256"].as_str().unwrap();
    assert_ne!(a, b);
    // This method exists only in the host test build. Production firmware has
    // no synthetic-touch command. The same engine touch handler drives state.
    client
        .request(json!({"method":"test_touch","x":160,"y":208}))
        .unwrap();
    client
        .request(json!({"method":"test_touch","x":160,"y":208}))
        .unwrap();
    assert_eq!(
        client
            .request(json!({"method":"get_playback_state"}))
            .unwrap()["state"],
        "paused"
    );
    client
        .request(json!({"method":"test_touch","x":50,"y":208}))
        .unwrap();
    wait_state(&mut client, "paused");
    assert_eq!(
        client
            .request(json!({"method":"get_playback_state"}))
            .unwrap()["current_video_id"],
        a
    );
    client
        .request(json!({"method":"test_touch","x":160,"y":100}))
        .unwrap(); // Reveal.
    client
        .request(json!({"method":"test_touch","x":270,"y":208}))
        .unwrap();
    wait_state(&mut client, "paused");
    assert_eq!(
        client
            .request(json!({"method":"get_playback_state"}))
            .unwrap()["current_video_id"],
        b
    );
    client.request(json!({"method":"resume"})).unwrap();
    let state = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    assert_eq!(state["queue"], json!([a, b]));
    assert_eq!(state["current_video_id"], b);
    client.request(json!({"method":"pause"})).unwrap();
    client.request(json!({"method":"previous_video"})).unwrap();
    wait_state(&mut client, "paused");
    let state = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    assert_eq!(state["current_video_id"], a);
    assert_eq!(state["next_video_id"], b);
    assert!(state["previous_video_id"].is_null());
    client.request(json!({"method":"next_video"})).unwrap();
    wait_state(&mut client, "paused");
    client.request(json!({"method":"set_queue","ids":[b,a],"expected_queue_revision":state["queue_revision"]})).unwrap();
    let reordered = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    assert_eq!(reordered["queue_position"], 0);
    assert_eq!(reordered["current_video_id"], b);
    assert_eq!(client.request(json!({"method":"set_queue","ids":[a,b],"expected_queue_revision":state["queue_revision"]})).unwrap_err().code, "queue_conflict");
    assert_eq!(client.request(json!({"method":"set_queue","ids":[a,a],"expected_queue_revision":reordered["queue_revision"]})).unwrap_err().code, "invalid_parameter");
    client
        .request(json!({"method":"select_video","video_id":a}))
        .unwrap();
    wait_state(&mut client, "playing");
    client
        .request(json!({"method":"seek","position_us":3900000}))
        .unwrap();
    wait_state(&mut client, "ended"); // Play Once stops at the last item.
    client
        .request(json!({"method":"set_loop_mode","mode":"loop_current"}))
        .unwrap();
    client.request(json!({"method":"restart"})).unwrap();
    client
        .request(json!({"method":"seek","position_us":3900000}))
        .unwrap();
    thread::sleep(Duration::from_millis(180));
    let repeat = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    assert_eq!(repeat["current_video_id"], a);
    assert_eq!(repeat["state"], "playing");
    assert!(repeat["position_us"].as_u64().unwrap() < 3900000);
    client
        .request(json!({"method":"set_loop_mode","mode":"loop_queue"}))
        .unwrap();
    client
        .request(json!({"method":"seek","position_us":3900000}))
        .unwrap();
    thread::sleep(Duration::from_millis(200));
    wait_state(&mut client, "playing");
    assert_eq!(
        client
            .request(json!({"method":"get_playback_state"}))
            .unwrap()["current_video_id"],
        b
    );
    client.request(json!({"method":"pause"})).unwrap();
    client.request(json!({"method":"previous_video"})).unwrap();
    wait_state(&mut client, "paused"); // Loop Queue wraps backwards.
    let before = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    assert_eq!(before["current_video_id"], a);
    client.hello().unwrap(); // New handshake never replaces the queue or mode.
    let after = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    assert_eq!(before["queue"], after["queue"]);
    assert_eq!(after["loop_mode"], "loop_queue");
    // An SD file may become unavailable after admission. Advertised neighbors
    // must match the navigation actions, including a single valid looping item.
    let unavailable = card.path().join(format!("muse-video/{b}.mvb"));
    let saved = unavailable.with_extension("unavailable");
    std::fs::rename(&unavailable, &saved).unwrap();
    let available = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    assert_eq!(available["next_video_id"], a);
    assert_eq!(available["previous_video_id"], a);
    client.request(json!({"method":"next_video"})).unwrap();
    wait_state(&mut client, "paused");
    assert_eq!(
        client
            .request(json!({"method":"get_playback_state"}))
            .unwrap()["current_video_id"],
        a
    );
    client
        .request(json!({"method":"set_loop_mode","mode":"play_once"}))
        .unwrap();
    let available = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    assert!(available["next_video_id"].is_null());
    assert!(available["previous_video_id"].is_null());
    std::fs::rename(&saved, &unavailable).unwrap();
    let after = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    client.request(json!({"method":"set_queue","ids":[],"expected_queue_revision":after["queue_revision"]})).unwrap();
    thread::sleep(Duration::from_millis(2200));
    let empty = client
        .request(json!({"method":"get_playback_state"}))
        .unwrap();
    assert_eq!(empty["queue"], json!([]));
    assert_eq!(empty["state"], "idle"); // Discovery cannot undo explicit removal.
    assert!(card.path().join(format!("muse-video/{a}.mvb")).is_file());
}
#[test]
#[ignore = "requires separately built native profile-tests"]
fn profiled_engine_counts_only_completed_presentations_and_clips_offscreen_rows() {
    let root = tempfile::tempdir().unwrap();
    let path = video(root.path());
    let executable = simulator().parent().unwrap().join("profile-tests");
    let result = Command::new(executable).arg(path).output().unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(
        String::from_utf8_lossy(&result.stdout).contains("No hardware performance measurements")
    );
}
#[test]
#[ignore = "requires separately built native simulator"]
fn pipeline_probes_do_not_count_as_video_frames_and_reject_parameters_without_pausing() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let card = tempfile::tempdir().unwrap();
    storage::export(&path, card.path(), true, &Cancellation::default(), None).unwrap();
    let mut client = Client::new(Simulator::spawn(&simulator(), card.path()).unwrap());
    client.hello().unwrap();
    wait_state(&mut client, "playing");
    assert_eq!(
        client
            .request(json!({"method":"get_display_info"}))
            .unwrap()["available"],
        false
    );
    for command in [
        json!({"method":"probe_pipeline","stage":"unknown"}),
        json!({"method":"probe_pipeline","stage":"decode","repetitions":"4"}),
        json!({"method":"probe_pipeline","stage":"decode","repetitions":0}),
        json!({"method":"probe_pipeline","stage":"decode","repetitions":5}),
        json!({"method":"set_render_options","mode":"blocks","big_endian":false,"spi_hz":1}),
    ] {
        assert_eq!(
            client.request(command).unwrap_err().code,
            "invalid_parameter"
        );
        assert_eq!(
            client.request(json!({"method":"get_status"})).unwrap()["state"],
            "playing"
        );
    }
    assert_eq!(client.request(json!({"method":"set_render_options","mode":"dma","big_endian":true,"spi_hz":40000000})).unwrap_err().code, "unsupported_configuration");
    client.request(json!({"method":"pause"})).unwrap();
    let before = client.request(json!({"method":"get_benchmark"})).unwrap();
    let decode = client
        .request(json!({"method":"probe_pipeline","stage":"decode","repetitions":4}))
        .unwrap();
    assert_eq!(decode["microbenchmark_only"], true);
    assert_eq!(decode["frames_decoded"], 4);
    assert!(decode["wall_us"].as_u64().unwrap() > 0);
    let read = client
        .request(json!({"method":"probe_pipeline","stage":"sd","repetitions":4}))
        .unwrap();
    assert_eq!(read["microbenchmark_only"], true);
    assert_eq!(read["buffer_bytes"], 16384);
    assert_eq!(read["io_available"], false);
    assert_eq!(
        client.request(json!({"method":"get_benchmark"})).unwrap()["frames"],
        before["frames"]
    );
    let profile = client
        .request(json!({"method":"get_pipeline_profile"}))
        .unwrap();
    assert_eq!(profile["frames"], before["frames"]);
    assert_eq!(profile["io_available"], false);
    assert_eq!(profile["display_available"], false);
    for key in [
        "sd_read",
        "sd_seek",
        "container_prepare",
        "display",
        "transform",
        "dma_wait_nested",
    ] {
        assert!(
            profile["average_us"][key].is_null(),
            "Host adapter cannot measure {key}"
        );
    }
    assert!(profile["pixels"].is_null());
}
#[test]
#[ignore = "requires separately built native simulator"]
fn valid_upload_preempts_startup_validation_and_malformed_metadata_preserves_it() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let bytes = fs::read(&path).unwrap();
    let digest = storage::digest_file(&path, &Cancellation::default()).unwrap();
    let card = tempfile::tempdir().unwrap();
    storage::export(&path, card.path(), true, &Cancellation::default(), None).unwrap();
    fs::write(card.path().join("unrelated.txt"), b"keep").unwrap();
    let mut client = Client::new(Simulator::spawn(&simulator(), card.path()).unwrap());
    client.hello().unwrap();
    wait_state(&mut client, "playing");
    client
        .request(json!({"method":"select_video","sha256":digest}))
        .unwrap();
    let loading = client.request(json!({"method":"get_status"})).unwrap();
    assert_eq!(loading["state"], "loading");
    assert_eq!(loading["validation_total_bytes"], bytes.len() as u64);
    for command in [
        json!({"method":"begin_upload","sha256":"bad","size":bytes.len(),"container_version":1}),
        json!({"method":"begin_upload","sha256":"b".repeat(64),"size":0,"container_version":1}),
        json!({"method":"begin_upload","sha256":"b".repeat(64),"size":"136","container_version":1}),
        json!({"method":"begin_upload","sha256":"b".repeat(64),"size":bytes.len(),"container_version":2}),
    ] {
        assert_eq!(
            client.request(command).unwrap_err().code,
            "invalid_parameter"
        );
        let preserved = client.request(json!({"method":"get_status"})).unwrap();
        assert_eq!(preserved["state"], "loading");
        assert_eq!(
            preserved["validation_total_bytes"],
            loading["validation_total_bytes"]
        );
        assert_eq!(
            client.request(json!({"method":"upload_status"})).unwrap()["state"],
            "none"
        );
    }
    let transfer = client.request(json!({"method":"begin_upload","sha256":"b".repeat(64),"size":bytes.len(),"container_version":1})).unwrap();
    assert_eq!(transfer["state"], "receiving");
    assert_eq!(transfer["accepted_offset"], 0);
    client
        .request(json!({"method":"cancel_upload","transfer_id":transfer["transfer_id"]}))
        .unwrap();
    assert_eq!(
        fs::read(card.path().join(format!("muse-video/{digest}.mvb"))).unwrap(),
        bytes
    );
    assert_eq!(
        fs::read(card.path().join("unrelated.txt")).unwrap(),
        b"keep"
    );
}
struct Raw {
    io: Simulator,
    session: u32,
    sequence: u32,
}
impl Raw {
    fn new(root: &Path) -> Self {
        let mut r = Self {
            io: Simulator::spawn(&simulator(), root).unwrap(),
            session: 0,
            sequence: 0,
        };
        let f=r.send(json!({"method":"hello","protocol_min":1,"protocol_max":1,"client_nonce":"raw-test-first"}));
        r.session = f.session;
        r
    }
    fn receive(&mut self, seq: u32) -> Frame {
        let start = Instant::now();
        let mut b = Vec::new();
        loop {
            let mut x = [0];
            match self.io.read(&mut x) {
                Ok(0) => panic!("Disconnected"),
                Ok(_) => {
                    if x[0] == 0 {
                        if let Ok(f) = Frame::decode(&b) {
                            if f.sequence == seq && f.kind != 3 {
                                return f;
                            }
                        }
                        b.clear();
                    } else {
                        b.push(x[0]);
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::TimedOut => {}
                Err(e) => panic!("{e}"),
            }
            assert!(start.elapsed() < Duration::from_secs(3));
        }
    }
    fn frame(&mut self, f: Frame) -> Frame {
        self.io.write_all(&f.encode().unwrap()).unwrap();
        self.io.flush().unwrap();
        self.receive(f.sequence)
    }
    fn send(&mut self, value: Value) -> Frame {
        self.sequence += 1;
        self.frame(Frame::json(1, self.session, self.sequence, &value).unwrap())
    }
    fn request(&mut self, value: Value) -> Value {
        serde_json::from_slice(&self.send(value).payload).unwrap()
    }
    fn chunk(&mut self, id: u32, offset: u32, data: &[u8]) -> Frame {
        self.sequence += 1;
        let mut payload = Vec::new();
        payload.extend(id.to_le_bytes());
        payload.extend(offset.to_le_bytes());
        payload.extend((data.len() as u16).to_le_bytes());
        payload.extend([0, 0]);
        payload.extend(data);
        self.frame(Frame {
            kind: 16,
            session: self.session,
            sequence: self.sequence,
            payload,
        })
    }
}
#[test]
#[ignore = "requires separately built native simulator"]
fn real_decoder_playback_controls_transform_and_telemetry() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let card = tempfile::tempdir().unwrap();
    storage::export(&path, card.path(), true, &Cancellation::default(), None).unwrap();
    let mut c = Client::new(Simulator::spawn(&simulator(), card.path()).unwrap());
    let hello = c.hello().unwrap();
    assert_eq!(hello["physical_verification"], "unverified");
    assert_eq!(hello["hardware"]["simulator"], true);
    let first = wait_state(&mut c, "playing");
    assert!(first["actual_fps"].is_null());
    thread::sleep(Duration::from_millis(2200));
    let later = c.request(json!({"method":"get_telemetry"})).unwrap();
    assert!(
        later["frame_index"].as_u64().unwrap() > first["frame_index"].as_u64().unwrap_or(0),
        "first={first}, later={later}"
    );
    assert!(later["actual_fps"].as_f64().unwrap() > 0.0);
    assert!(later["hardware"]["temperature_c"].is_null());
    assert!(later["hardware"]["free_heap_bytes"].is_null());
    let measured = c.request(json!({"method":"get_benchmark"})).unwrap();
    assert!(measured["frames"].as_u64().unwrap() > 1);
    assert!(measured["average_decode_us"].as_f64().unwrap() > 0.0);
    assert!(measured["average_read_us"].as_f64().unwrap() > 0.0);
    assert_eq!(measured["content_sha256"], later["content_sha256"]);
    c.request(json!({"method":"pause"})).unwrap();
    let paused = wait_state(&mut c, "paused");
    thread::sleep(Duration::from_millis(100));
    assert_eq!(
        wait_state(&mut c, "paused")["frame_index"],
        paused["frame_index"]
    );
    for rotation in [0, 90, 180, 270] {
        c.request(json!({"method":"set_transform","x":-5,"y":20,"width":80,"height":60,"lock_aspect":false,"rotation_deg":rotation})).unwrap();
        let s = wait_state(&mut c, "paused");
        assert_eq!(s["transform"]["rotation_deg"], rotation);
    }
    c.request(json!({"method":"set_position","x":10,"y":10}))
        .unwrap();
    c.request(json!({"method":"set_size","width":160,"height":120,"lock_aspect":false}))
        .unwrap();
    c.request(json!({"method":"set_scale","scale":0.5}))
        .unwrap();
    c.request(json!({"method":"seek","position_us":1000000}))
        .unwrap();
    assert_eq!(wait_state(&mut c, "paused")["frame_index"], 10);
    c.request(json!({"method":"set_fps","numerator":5,"denominator":1}))
        .unwrap();
    c.request(json!({"method":"resume"})).unwrap();
    thread::sleep(Duration::from_millis(200));
    c.request(json!({"method":"stop"})).unwrap();
    assert_eq!(wait_state(&mut c, "stopped")["frame_index"], Value::Null);
    c.request(json!({"method":"restart"})).unwrap();
    wait_state(&mut c, "playing");
    let ppm = fs::read(card.path().join(".simulator/frame.ppm")).unwrap();
    assert!(ppm.starts_with(b"P6\n320 240\n255\n"));
    assert!(ppm[15..].iter().any(|b| *b != 0));
}
#[test]
#[ignore = "requires separately built native simulator"]
fn invalid_commands_receipts_new_sessions_and_waiting_state() {
    let card = tempfile::tempdir().unwrap();
    let mut r = Raw::new(card.path());
    let wait: Value =
        serde_json::from_slice(&fs::read(card.path().join(".simulator/waiting.json")).unwrap())
            .unwrap();
    assert_eq!(wait["message"], "Waiting for video...");
    assert_eq!(wait["title"], "Muse Video Bridge");
    for cmd in [
        json!({"method":"wat"}),
        json!({"method":"pause"}),
        json!({"method":"subscribe","telemetry_interval_ms":0}),
        json!({"method":"discard_upload","transfer_id":0,"confirmed":true}),
        json!({"method":"hello","protocol_min":9,"protocol_max":9,"client_nonce":"bad"}),
    ] {
        assert_eq!(r.request(cmd)["ok"], false);
    }
    let sequence = r.sequence + 1;
    let f = Frame::json(
        1,
        r.session,
        sequence,
        &json!({"method":"subscribe","telemetry_interval_ms":1000}),
    )
    .unwrap();
    r.sequence = sequence;
    let first = r.frame(f.clone());
    let retry = r.frame(f.clone());
    assert_eq!(first.payload, retry.payload);
    let conflict = r.frame(
        Frame::json(
            1,
            r.session,
            sequence,
            &json!({"method":"subscribe","telemetry_interval_ms":2000}),
        )
        .unwrap(),
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&conflict.payload).unwrap()["error"]["code"],
        "sequence_conflict"
    );
    for _ in 0..10 {
        assert_eq!(r.request(json!({"method":"ping"}))["ok"], true);
    }
    let stale = r.frame(f);
    assert_eq!(
        serde_json::from_slice::<Value>(&stale.payload).unwrap()["error"]["code"],
        "stale_sequence"
    );
    let old = r.session;
    r.session = 0;
    let hello = r.send(
        json!({"method":"hello","protocol_min":1,"protocol_max":1,"client_nonce":"new-client"}),
    );
    assert_ne!(hello.session, old);
    r.session = hello.session;
    let bad =
        r.frame(Frame::json(1, old, r.sequence + 1, &json!({"method":"get_status"})).unwrap());
    assert_eq!(
        serde_json::from_slice::<Value>(&bad.payload).unwrap()["error"]["code"],
        "stale_session"
    );
    // Oversize/CRC garbage is ignored, then a valid frame still gets through.
    r.io.write_all(&vec![1; 2200]).unwrap();
    r.io.write_all(&[0]).unwrap();
    assert_eq!(r.request(json!({"method":"get_status"}))["ok"], true);
}
struct DropAck {
    io: Simulator,
    pending: VecDeque<u8>,
    drop: u8,
    cancel: Option<Cancellation>,
}
impl Read for DropAck {
    fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
        if b.is_empty() {
            return Ok(0);
        }
        while self.pending.is_empty() {
            let mut wire = Vec::new();
            loop {
                let mut x = [0];
                self.io.read_exact(&mut x)?;
                wire.push(x[0]);
                if x[0] == 0 {
                    break;
                }
            }
            if self.drop > 0 && Frame::decode(&wire[..wire.len() - 1]).is_ok_and(|f| f.kind == 17) {
                self.drop -= 1;
                if let Some(cancel) = &self.cancel {
                    cancel.cancel();
                    return Err(std::io::ErrorKind::TimedOut.into());
                }
                continue;
            }
            self.pending.extend(wire);
        }
        let n = b.len().min(self.pending.len());
        for x in &mut b[..n] {
            *x = self.pending.pop_front().unwrap();
        }
        Ok(n)
    }
}
impl Write for DropAck {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
        self.io.write(b)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.io.flush()
    }
}
#[test]
#[ignore = "requires separately built native simulator"]
fn automatic_upload_recovery_rehashes_the_acknowledged_checkpoint() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let card = tempfile::tempdir().unwrap();
    let mut client = Client::new(DropAck {
        io: Simulator::spawn(&simulator(), card.path()).unwrap(),
        pending: VecDeque::new(),
        drop: 3,
        cancel: None,
    });
    client.response_timeout = Duration::from_millis(200);
    client.hello().unwrap();
    let result = client
        .upload_resumable(&path, &Cancellation::default(), None)
        .unwrap();
    assert_eq!(result["state"], "complete");
    assert_eq!(result["host_transfer"]["recovery_attempts"], 1);
    assert!(result["host_transfer"]["resumed_offset"].as_u64().unwrap() > 0);
    assert_eq!(
        storage::digest_file(
            &card.path().join(format!(
                "muse-video/{}.mvb",
                result["sha256"].as_str().unwrap()
            )),
            &Cancellation::default()
        )
        .unwrap(),
        result["sha256"].as_str().unwrap()
    );
}
struct CompletionMetadata {
    io: Simulator,
    pending: VecDeque<u8>,
    sha256: Option<Value>,
    altered: bool,
}
impl Read for CompletionMetadata {
    fn read(&mut self, bytes: &mut [u8]) -> std::io::Result<usize> {
        if bytes.is_empty() {
            return Ok(0);
        }
        while self.pending.is_empty() {
            let mut wire = Vec::new();
            loop {
                let mut byte = [0];
                self.io.read_exact(&mut byte)?;
                wire.push(byte[0]);
                if byte[0] == 0 {
                    break;
                }
            }
            let mut frame = Frame::decode(&wire[..wire.len() - 1]).unwrap();
            if frame.kind == 2 {
                let mut reply: Value = serde_json::from_slice(&frame.payload).unwrap();
                if reply["result"]["state"] == "complete" {
                    let result = reply["result"].as_object_mut().unwrap();
                    if let Some(sha256) = &self.sha256 {
                        result.insert("sha256".into(), sha256.clone());
                    } else {
                        result.remove("sha256");
                    }
                    self.altered = true;
                    frame.payload = serde_json::to_vec(&reply).unwrap();
                    wire = frame.encode().unwrap();
                }
            }
            self.pending.extend(wire);
        }
        let count = bytes.len().min(self.pending.len());
        for byte in &mut bytes[..count] {
            *byte = self.pending.pop_front().unwrap();
        }
        Ok(count)
    }
}
impl Write for CompletionMetadata {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.io.write(bytes)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.io.flush()
    }
}
#[test]
#[ignore = "requires separately built native simulator"]
fn upload_requires_exact_sha256_in_crc_valid_completion_metadata() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let cancellation = Cancellation::default();
    let digest = storage::digest_file(&path, &cancellation).unwrap();
    for claimed in [
        None,
        Some(Value::Null),
        Some(json!(123)),
        Some(json!("a".repeat(64))),
        Some(json!(digest.to_uppercase())),
        Some(json!(digest)),
    ] {
        let correct = claimed.as_ref().and_then(Value::as_str) == Some(digest.as_str());
        let card = tempfile::tempdir().unwrap();
        fs::write(card.path().join("unrelated.txt"), b"keep").unwrap();
        let mut client = Client::new(CompletionMetadata {
            io: Simulator::spawn(&simulator(), card.path()).unwrap(),
            pending: VecDeque::new(),
            sha256: claimed.clone(),
            altered: false,
        });
        client.hello().unwrap();
        let result = client.upload(&path, &cancellation, None);
        if correct {
            assert_eq!(result.unwrap()["sha256"], digest);
        } else {
            let error = result
                .expect_err("A CRC-valid reply without exact file SHA cannot prove integrity");
            assert_eq!(error.code, "verification_failed", "claimed={claimed:?}");
        }
        assert!(client.into_transport().altered);
        assert_eq!(
            fs::read(card.path().join("unrelated.txt")).unwrap(),
            b"keep"
        );
    }
}
#[test]
#[ignore = "requires separately built native simulator"]
fn chunked_upload_retries_checksums_cancel_and_reboot_resume() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let bytes = fs::read(&path).unwrap();
    let digest = storage::digest_file(&path, &Cancellation::default()).unwrap();
    let card = tempfile::tempdir().unwrap();
    {
        let mut r = Raw::new(card.path());
        let start=r.request(json!({"method":"begin_upload","sha256":digest,"size":bytes.len(),"container_version":1}));
        assert_eq!(start["ok"], true);
        let id = start["result"]["transfer_id"].as_u64().unwrap() as u32;
        for (i, data) in bytes[..65536].chunks(1024).enumerate() {
            let ack = r.chunk(id, (i * 1024) as u32, data);
            assert_eq!(
                u16::from_le_bytes(ack.payload[12..14].try_into().unwrap()),
                0
            );
        }
        let dup = r.chunk(id, 0, &bytes[..1024]);
        assert_eq!(
            u16::from_le_bytes(dup.payload[12..14].try_into().unwrap()),
            1
        );
        let bad = r.chunk(id, 0, &[7; 1024]);
        assert_eq!(
            u16::from_le_bytes(bad.payload[12..14].try_into().unwrap()),
            2
        );
        assert_eq!(
            r.request(json!({"method":"cancel_upload","transfer_id":id}))["ok"],
            true
        );
    }
    let mut c = Client::new(DropAck {
        io: Simulator::spawn(&simulator(), card.path()).unwrap(),
        pending: VecDeque::new(),
        drop: 1,
        cancel: None,
    });
    c.response_timeout = Duration::from_millis(300);
    c.hello().unwrap();
    let completed = c.upload(&path, &Cancellation::default(), None).unwrap();
    assert_eq!(completed["state"], "complete");
    assert_eq!(completed["host_transfer"]["resumed_offset"], 65536);
    assert_eq!(
        completed["host_transfer"]["newly_confirmed_bytes"],
        bytes.len() as u64 - 65536
    );
    assert!(
        completed["host_transfer"]["receiving_seconds"]
            .as_f64()
            .unwrap()
            > 0.0
    );
    assert!(
        completed["host_transfer"]["confirmed_bytes_per_second"]
            .as_f64()
            .unwrap()
            > 0.0
    );
    let diagnostics = c.diagnostics();
    assert!(diagnostics["transport"]["retry_attempts"].as_u64().unwrap() >= 1);
    assert!(
        diagnostics["transport"]["response_timeouts"]
            .as_u64()
            .unwrap()
            >= 1
    );
    assert!(diagnostics["transport"]["received_bytes"].as_u64().unwrap() > 0);
    assert_eq!(diagnostics["invalid_wire_frames"], 0);
    wait_state(&mut c, "playing");
    let final_path = card.path().join(format!("muse-video/{digest}.mvb"));
    assert_eq!(fs::read(final_path).unwrap(), bytes);
    assert!(!card
        .path()
        .join(format!("muse-video/.upload-{digest}.meta"))
        .exists());
    assert_eq!(
        c.upload(&path, &Cancellation::default(), None).unwrap()["state"],
        "complete"
    );
    wait_state(&mut c, "playing");
    assert!(!card
        .path()
        .join(format!("muse-video/.upload-{digest}.part"))
        .exists());
    for rotation in [i32::MAX, i32::MIN, 45] {
        assert_eq!(
            c.request(json!({"method":"set_rotation","rotation_deg":rotation}))
                .unwrap_err()
                .code,
            "invalid_parameter"
        );
    }
}
#[test]
#[ignore = "requires separately built native simulator"]
fn upload_rejects_wrong_digest_and_preserves_previous_media() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let bytes = fs::read(&path).unwrap();
    let card = tempfile::tempdir().unwrap();
    fs::write(card.path().join("unrelated.txt"), b"keep").unwrap();
    let mut r = Raw::new(card.path());
    let start=r.request(json!({"method":"begin_upload","sha256":"a".repeat(64),"size":bytes.len(),"container_version":1}));
    let id = start["result"]["transfer_id"].as_u64().unwrap() as u32;
    for (i, data) in bytes.chunks(1024).enumerate() {
        r.chunk(id, (i * 1024) as u32, data);
    }
    assert_eq!(
        r.request(json!({"method":"finish_upload","transfer_id":id}))["ok"],
        true
    );
    let now = Instant::now();
    loop {
        let s = r.request(json!({"method":"upload_status"}));
        if s["result"]["state"] == "error" {
            assert_eq!(s["result"]["error"], "digest_mismatch");
            break;
        }
        assert!(now.elapsed() < Duration::from_secs(10));
        thread::sleep(Duration::from_millis(10));
    }
    assert!(!card.path().join("muse-video/active.json").exists());
    assert_eq!(
        fs::read(card.path().join("unrelated.txt")).unwrap(),
        b"keep"
    );
    assert_eq!(
        r.request(json!({"method":"discard_upload","transfer_id":id,"confirmed":true}))["ok"],
        true
    );
}
#[cfg(target_os = "linux")]
#[test]
#[ignore = "requires separately built native simulator and Linux PTY"]
fn linux_serialport_transport_to_native_engine_through_pty() {
    use std::{fs::File, os::fd::FromRawFd, process::Stdio};
    let card = tempfile::tempdir().unwrap();
    let (mut master, mut slave) = (0, 0);
    let mut name = [0i8; 128];
    // SAFETY: writable fd/name buffers and default terminal settings. This
    // creates a host pseudo-terminal and never accesses a physical serial port.
    assert_eq!(
        unsafe {
            libc::openpty(
                &mut master,
                &mut slave,
                name.as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
            )
        },
        0
    );
    let master = unsafe { File::from_raw_fd(master) };
    let slave = unsafe { File::from_raw_fd(slave) };
    let port = unsafe { std::ffi::CStr::from_ptr(name.as_ptr()) }
        .to_str()
        .unwrap();
    let mut child = Command::new(simulator())
        .arg(card.path())
        .stdin(Stdio::from(master.try_clone().unwrap()))
        .stdout(Stdio::from(master))
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap();
    let result = (|| {
        let mut c = muse_video_core::serial::open_port(port, 115200)?;
        let hello = c.hello()?;
        assert_eq!(hello["hardware"]["simulator"], true);
        assert_eq!(c.request(json!({"method":"get_status"}))?["state"], "idle");
        Ok::<_, muse_video_core::Error>(())
    })();
    drop(slave);
    let _ = child.kill();
    let _ = child.wait();
    result.unwrap();
}
#[test]
#[ignore = "requires separately built native simulator"]
fn firmware_rejects_corrupt_header_and_jpeg_tables_without_memory_errors() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let original = fs::read(&path).unwrap();
    for table in [false, true] {
        let card = tempfile::tempdir().unwrap();
        fs::create_dir(card.path().join("muse-video")).unwrap();
        let mut bytes = original.clone();
        if table {
            let mut at = 114;
            while bytes[at + 1] != 0xc4 {
                at += 2 + u16::from_be_bytes([bytes[at + 2], bytes[at + 3]]) as usize;
            }
            bytes[at + 4] = 255;
            let length = u32::from_le_bytes(bytes[96..100].try_into().unwrap()) as usize;
            let crc = crc32fast::hash(&bytes[112..112 + length]);
            bytes[104..108].copy_from_slice(&crc.to_le_bytes());
            let index = u64::from_le_bytes(bytes[48..56].try_into().unwrap()) as usize;
            bytes[index + 20..index + 24].copy_from_slice(&crc.to_le_bytes());
            let index_crc = crc32fast::hash(&bytes[index..]);
            bytes[72..76].copy_from_slice(&index_crc.to_le_bytes());
            bytes[76..80].fill(0);
            let hcrc = crc32fast::hash(&bytes[..96]);
            bytes[76..80].copy_from_slice(&hcrc.to_le_bytes());
        } else {
            bytes[48] ^= 128;
        }
        use sha2::Digest;
        let hash = format!("{:x}", sha2::Sha256::digest(&bytes));
        fs::write(card.path().join(format!("muse-video/{hash}.mvb")), bytes).unwrap();
        let mut c = Client::new(Simulator::spawn(&simulator(), card.path()).unwrap());
        c.hello().unwrap();
        let error = wait_state(&mut c, "error");
        assert_eq!(
            error["error"],
            if table {
                "unsupported_jpeg"
            } else {
                "invalid_container"
            }
        );
        assert!(error["frame_index"].is_null());
    }
}

#[test]
#[ignore = "requires separately built native simulator"]
fn upload_blocks_sd_selection_and_conflicting_chunk_sequences() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let bytes = fs::read(&path).unwrap();
    let digest = storage::digest_file(&path, &Cancellation::default()).unwrap();
    let card = tempfile::tempdir().unwrap();
    let mut r = Raw::new(card.path());
    let start = r.request(
        json!({"method":"begin_upload","sha256":digest,"size":bytes.len(),"container_version":1}),
    );
    assert_eq!(start["ok"], true);
    let id = start["result"]["transfer_id"].as_u64().unwrap() as u32;
    let ack = r.chunk(id, 0, &bytes[..1024]);
    assert_eq!(ack.kind, 17);
    // Same envelope sequence with different offset/data must never append data.
    r.sequence -= 1;
    let conflict = r.chunk(id, 1024, &bytes[1024..2048]);
    assert_eq!(conflict.kind, 2);
    assert_eq!(
        serde_json::from_slice::<Value>(&conflict.payload).unwrap()["error"]["code"],
        "sequence_conflict"
    );
    let selected = r.request(json!({"method":"select_video","sha256":digest}));
    assert_eq!(selected["error"]["code"], "busy");
    let status = r.request(json!({"method":"upload_status","transfer_id":id}));
    assert_eq!(status["result"]["accepted_offset"], 1024);
    assert_eq!(status["result"]["state"], "receiving");
}

#[test]
#[ignore = "requires separately built native simulator"]
fn upload_start_reports_journal_failure_without_deleting_it() {
    let card = tempfile::tempdir().unwrap();
    fs::create_dir(card.path().join("muse-video")).unwrap();
    let journal = card.path().join("muse-video/.upload.meta");
    fs::write(&journal, b"invalid checkpoint").unwrap();
    let mut r = Raw::new(card.path());
    let result = r.request(
        json!({"method":"begin_upload","sha256":"a".repeat(64),"size":1024,"container_version":1}),
    );
    assert_eq!(result["error"]["code"], "upload_journal_invalid");
    assert_eq!(fs::read(journal).unwrap(), b"invalid checkpoint");
}

#[test]
#[ignore = "requires separately built native simulator"]
fn cancellation_during_lost_ack_removes_owned_incomplete_files() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let card = tempfile::tempdir().unwrap();
    fs::write(card.path().join("unrelated.txt"), b"keep").unwrap();
    let token = Cancellation::default();
    let mut c = Client::new(DropAck {
        io: Simulator::spawn(&simulator(), card.path()).unwrap(),
        pending: VecDeque::new(),
        drop: 1,
        cancel: Some(token.clone()),
    });
    c.response_timeout = Duration::from_secs(30);
    c.hello().unwrap();
    let started = Instant::now();
    let error = c.upload(&path, &token, None).unwrap_err();
    assert_eq!(error.code, "cancelled");
    assert!(started.elapsed() < Duration::from_secs(2));
    let status = c.request(json!({"method":"upload_status"})).unwrap();
    assert_eq!(status["state"], "none");
    assert_eq!(status["accepted_offset"], 1024);
    assert_eq!(status["checkpointed_offset"], 1024);
    let digest = storage::digest_file(&path, &Cancellation::default()).unwrap();
    assert!(!card
        .path()
        .join(format!("muse-video/.upload-{digest}.part"))
        .exists());
    assert!(!card
        .path()
        .join(format!("muse-video/.upload-{digest}.meta"))
        .exists());
    assert_eq!(
        fs::read(card.path().join("unrelated.txt")).unwrap(),
        b"keep"
    );
    assert!(!card.path().join("muse-video/active.json").exists());
    let finished = c.upload(&path, &Cancellation::default(), None).unwrap();
    assert_eq!(finished["state"], "complete");
    assert_eq!(finished["host_transfer"]["resumed_offset"], 0);
    assert_eq!(
        storage::digest_file(
            &card.path().join(format!("muse-video/{digest}.mvb")),
            &Cancellation::default()
        )
        .unwrap(),
        digest
    );
}

#[test]
#[ignore = "requires separately built native simulator"]
fn rejected_new_identity_cannot_discard_another_transfers_journal() {
    let card = tempfile::tempdir().unwrap();
    let mut r = Raw::new(card.path());
    let first = r.request(
        json!({"method":"begin_upload","sha256":"a".repeat(64),"size":1024,"container_version":1}),
    );
    let id = first["result"]["transfer_id"].as_u64().unwrap();
    assert_eq!(
        r.request(json!({"method":"cancel_upload","transfer_id":id}))["ok"],
        true
    );
    let journal = card
        .path()
        .join(format!("muse-video/.upload-{}.meta", "a".repeat(64)));
    let original = fs::read(&journal).unwrap();
    // This partial has no identifying journal, so it must remain fail-closed.
    let unidentified = card
        .path()
        .join(format!("muse-video/.upload-{}.part", "b".repeat(64)));
    fs::write(&unidentified, b"unidentified partial").unwrap();
    let failed = r.request(
        json!({"method":"begin_upload","sha256":"b".repeat(64),"size":1024,"container_version":1}),
    );
    assert_eq!(failed["error"]["code"], "upload_conflict");
    let failed_id = r.request(json!({"method":"upload_status"}))["result"]["transfer_id"].clone();
    assert_eq!(
        r.request(json!({"method":"discard_upload","transfer_id":failed_id,"confirmed":true}))
            ["ok"],
        false
    );
    assert_eq!(fs::read(journal).unwrap(), original);
    assert_eq!(fs::read(unidentified).unwrap(), b"unidentified partial");
    assert!(card
        .path()
        .join(format!("muse-video/.upload-{}.part", "a".repeat(64)))
        .exists());
}

#[test]
#[ignore = "requires separately built native simulator"]
fn reconnect_before_timeout_can_resume_same_file_on_running_firmware() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let bytes = fs::read(&path).unwrap();
    let digest = storage::digest_file(&path, &Cancellation::default()).unwrap();
    let card = tempfile::tempdir().unwrap();
    let mut r = Raw::new(card.path());
    let first = r.request(
        json!({"method":"begin_upload","sha256":digest,"size":bytes.len(),"container_version":1}),
    );
    let id = first["result"]["transfer_id"].as_u64().unwrap() as u32;
    r.chunk(id, 0, &bytes[..1024]);
    // Firmware stays alive; only the desktop client lifetime changes, within
    // milliseconds of the interrupted transfer instead of after reboot/timeout.
    let mut c = Client::new(r.io);
    c.hello().unwrap();
    let failed = c.request(json!({"method":"begin_upload","sha256":"b".repeat(64),"size":bytes.len(),"container_version":1})).unwrap_err();
    assert_eq!(failed.code, "busy");
    let state = c.request(json!({"method":"upload_status"})).unwrap();
    assert_eq!(state["accepted_offset"], 1024);
    let finished = c.upload(&path, &Cancellation::default(), None).unwrap();
    assert_eq!(finished["state"], "complete");
    assert_eq!(
        fs::read(card.path().join(format!("muse-video/{digest}.mvb"))).unwrap(),
        bytes
    );
}

fn begin_cancelled_prefix(r: &mut Raw, digest: &str, bytes: &[u8], prefix: usize) {
    let start = r.request(
        json!({"method":"begin_upload","sha256":digest,"size":bytes.len(),"container_version":1}),
    );
    assert_eq!(start["ok"], true, "{start}");
    assert_eq!(start["result"]["state"], "receiving");
    let id = start["result"]["transfer_id"].as_u64().unwrap() as u32;
    for (i, data) in bytes[..prefix].chunks(1024).enumerate() {
        let ack = r.chunk(id, (i * 1024) as u32, data);
        assert_eq!(ack.kind, 17);
        assert_eq!(
            u16::from_le_bytes(ack.payload[12..14].try_into().unwrap()),
            0
        );
    }
    let cancelled = r.request(json!({"method":"cancel_upload","transfer_id":id}));
    assert_eq!(cancelled["ok"], true);
    assert_eq!(cancelled["result"]["checkpointed_offset"], prefix);
}

#[test]
#[ignore = "requires separately built native simulator"]
fn foreign_legacy_recovery_survives_different_video_upload_and_playback() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let bytes = fs::read(&path).unwrap();
    let digest = storage::digest_file(&path, &Cancellation::default()).unwrap();
    let card = tempfile::tempdir().unwrap();
    fs::write(card.path().join("unrelated.txt"), b"keep").unwrap();
    let foreign = "a".repeat(64);
    {
        let mut r = Raw::new(card.path());
        begin_cancelled_prefix(&mut r, &foreign, &[7; 2048], 1024);
    }
    let legacy = card.path().join("muse-video/.upload.meta");
    fs::rename(
        card.path()
            .join(format!("muse-video/.upload-{foreign}.meta")),
        &legacy,
    )
    .unwrap();
    let partial = card
        .path()
        .join(format!("muse-video/.upload-{foreign}.part"));
    let original_journal = fs::read(&legacy).unwrap();
    let original_partial = fs::read(&partial).unwrap();
    let mut client = Client::new(Simulator::spawn(&simulator(), card.path()).unwrap());
    client.hello().unwrap();
    let complete = client
        .upload(&path, &Cancellation::default(), None)
        .unwrap();
    assert_eq!(complete["state"], "complete");
    assert_eq!(complete["host_transfer"]["resumed_offset"], 0);
    wait_state(&mut client, "playing");
    assert_eq!(
        fs::read(card.path().join(format!("muse-video/{digest}.mvb"))).unwrap(),
        bytes
    );
    assert_eq!(fs::read(legacy).unwrap(), original_journal);
    assert_eq!(fs::read(partial).unwrap(), original_partial);
    assert_eq!(
        fs::read(card.path().join("unrelated.txt")).unwrap(),
        b"keep"
    );
    assert!(!card
        .path()
        .join(format!("muse-video/.upload-{digest}.meta"))
        .exists());
}

#[test]
#[ignore = "requires separately built native simulator"]
fn multiple_cancelled_videos_resume_after_switching_and_reboot_without_cross_cleanup() {
    let source = tempfile::tempdir().unwrap();
    let first_path = video(source.path());
    media::convert(
        &source.path().join("input.mp4"),
        &source.path().join("second-job"),
        &Options {
            qscale: 12,
            ..Options::default()
        },
        &Tools::default(),
        &Cancellation::default(),
        None,
    )
    .unwrap();
    let second_path = source.path().join("second-job/video.mvb");
    let first_bytes = fs::read(&first_path).unwrap();
    let second_bytes = fs::read(&second_path).unwrap();
    let first_digest = storage::digest_file(&first_path, &Cancellation::default()).unwrap();
    let second_digest = storage::digest_file(&second_path, &Cancellation::default()).unwrap();
    assert_ne!(first_digest, second_digest);
    let card = tempfile::tempdir().unwrap();
    {
        let mut r = Raw::new(card.path());
        begin_cancelled_prefix(&mut r, &first_digest, &first_bytes, 1024);
        begin_cancelled_prefix(&mut r, &second_digest, &second_bytes, 2048);
    }
    let second_journal = card
        .path()
        .join(format!("muse-video/.upload-{second_digest}.meta"));
    let second_partial = card
        .path()
        .join(format!("muse-video/.upload-{second_digest}.part"));
    let saved_journal = fs::read(&second_journal).unwrap();
    let saved_partial = fs::read(&second_partial).unwrap();
    {
        let mut client = Client::new(Simulator::spawn(&simulator(), card.path()).unwrap());
        client.hello().unwrap();
        let complete = client
            .upload(&first_path, &Cancellation::default(), None)
            .unwrap();
        assert_eq!(complete["host_transfer"]["resumed_offset"], 1024);
        wait_state(&mut client, "playing");
    }
    assert_eq!(fs::read(&second_journal).unwrap(), saved_journal);
    assert_eq!(fs::read(&second_partial).unwrap(), saved_partial);
    {
        let mut client = Client::new(Simulator::spawn(&simulator(), card.path()).unwrap());
        client.hello().unwrap();
        let complete = client
            .upload(&second_path, &Cancellation::default(), None)
            .unwrap();
        assert_eq!(complete["host_transfer"]["resumed_offset"], 2048);
        wait_state(&mut client, "playing");
    }
    assert_eq!(
        fs::read(card.path().join(format!("muse-video/{first_digest}.mvb"))).unwrap(),
        first_bytes
    );
    assert_eq!(
        fs::read(card.path().join(format!("muse-video/{second_digest}.mvb"))).unwrap(),
        second_bytes
    );
    for digest in [first_digest, second_digest] {
        for suffix in ["part", "meta"] {
            assert!(!card
                .path()
                .join(format!("muse-video/.upload-{digest}.{suffix}"))
                .exists());
        }
    }
}

#[test]
#[ignore = "requires separately built native simulator"]
fn discard_only_removes_current_digest_recovery_files() {
    let card = tempfile::tempdir().unwrap();
    let first = "a".repeat(64);
    let second = "b".repeat(64);
    let mut r = Raw::new(card.path());
    begin_cancelled_prefix(&mut r, &first, &[1; 2048], 1024);
    begin_cancelled_prefix(&mut r, &second, &[2; 2048], 1024);
    let preserved = ["part", "meta"].map(|suffix| {
        let path = card
            .path()
            .join(format!("muse-video/.upload-{first}.{suffix}"));
        let bytes = fs::read(&path).unwrap();
        (path, bytes)
    });
    let id = r.request(json!({"method":"upload_status"}))["result"]["transfer_id"].clone();
    assert_eq!(
        r.request(json!({"method":"discard_upload","transfer_id":id,"confirmed":true}))["ok"],
        true
    );
    for (path, bytes) in preserved {
        assert_eq!(fs::read(path).unwrap(), bytes);
    }
    for suffix in ["part", "meta"] {
        assert!(!card
            .path()
            .join(format!("muse-video/.upload-{second}.{suffix}"))
            .exists());
    }
}

#[test]
#[ignore = "requires separately built native simulator"]
fn matching_legacy_recovery_resumes_and_own_journal_takes_precedence() {
    let source = tempfile::tempdir().unwrap();
    let path = video(source.path());
    let bytes = fs::read(&path).unwrap();
    let digest = storage::digest_file(&path, &Cancellation::default()).unwrap();
    for prefer_own in [false, true] {
        let card = tempfile::tempdir().unwrap();
        {
            let mut r = Raw::new(card.path());
            begin_cancelled_prefix(&mut r, &digest, &bytes, 2048);
        }
        let own = card
            .path()
            .join(format!("muse-video/.upload-{digest}.meta"));
        let legacy = card.path().join("muse-video/.upload.meta");
        if prefer_own {
            // A preexisting identified journal wins even if global recovery is
            // damaged. The damaged legacy file is preserved for inspection.
            fs::write(&legacy, b"invalid foreign legacy journal").unwrap();
        } else {
            fs::rename(&own, &legacy).unwrap();
        }
        let saved = fs::read(&legacy).unwrap();
        let mut client = Client::new(Simulator::spawn(&simulator(), card.path()).unwrap());
        client.hello().unwrap();
        let complete = client
            .upload(&path, &Cancellation::default(), None)
            .unwrap();
        assert_eq!(complete["host_transfer"]["resumed_offset"], 2048);
        wait_state(&mut client, "playing");
        assert_eq!(
            fs::read(card.path().join(format!("muse-video/{digest}.mvb"))).unwrap(),
            bytes
        );
        assert!(!own.exists());
        if prefer_own {
            assert_eq!(fs::read(legacy).unwrap(), saved);
        } else {
            assert!(!legacy.exists());
        }
    }
}

#[cfg(target_os = "linux")]
mod baud_discovery_pty {
    use super::*;
    use std::{
        fs::File,
        os::fd::{AsRawFd, FromRawFd},
    };

    fn pair() -> (File, File, String) {
        let (mut master, mut slave) = (0, 0);
        let mut name = [0i8; 128];
        // SAFETY: valid writable fd/name buffers; this creates only a host PTY.
        assert_eq!(
            unsafe {
                libc::openpty(
                    &mut master,
                    &mut slave,
                    name.as_mut_ptr(),
                    std::ptr::null(),
                    std::ptr::null(),
                )
            },
            0
        );
        let name = unsafe { std::ffi::CStr::from_ptr(name.as_ptr()) }
            .to_str()
            .unwrap()
            .to_owned();
        (
            unsafe { File::from_raw_fd(master) },
            unsafe { File::from_raw_fd(slave) },
            name,
        )
    }

    fn receive(master: &mut File) -> Frame {
        let deadline = Instant::now() + Duration::from_secs(6);
        let mut wire = Vec::new();
        loop {
            assert!(
                Instant::now() < deadline,
                "Host PTY fixture did not receive a frame"
            );
            let mut poll = libc::pollfd {
                fd: master.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            // SAFETY: one valid writable pollfd, with a bounded host wait.
            let ready = unsafe { libc::poll(&mut poll, 1, 100) };
            assert!(ready >= 0);
            if ready == 0 {
                continue;
            }
            let mut byte = [0];
            assert_eq!(master.read(&mut byte).unwrap(), 1);
            if byte[0] == 0 {
                if let Ok(frame) = Frame::decode(&wire) {
                    return frame;
                }
                wire.clear();
            } else {
                wire.push(byte[0]);
            }
        }
    }

    #[test]
    fn selected_port_falls_back_after_silent_first_baud_and_keeps_hello_compatible() {
        let (mut master, slave, path) = pair();
        let (done, keep_open) = std::sync::mpsc::channel();
        let peer =
            thread::spawn(move || {
                let first = receive(&mut master);
                let retry = receive(&mut master);
                assert_eq!(
                    first, retry,
                    "Same-baud HELLO retry must retain nonce and sequence"
                );
                assert_eq!(receive(&mut master), first);
                let second = receive(&mut master);
                assert_eq!(first.session, 0);
                assert_eq!(second.session, 0);
                assert_eq!(second.sequence, first.sequence + 1);
                let first_json: Value = serde_json::from_slice(&first.payload).unwrap();
                let second_json: Value = serde_json::from_slice(&second.payload).unwrap();
                assert_eq!(
                    first_json, second_json,
                    "Baud discovery must preserve client identity"
                );
                assert_eq!(second_json["method"], "hello");
                // Wrong-baud/CRC-invalid input must be counted and resynchronized;
                // an old sequence must not satisfy the next baud's HELLO.
                master.write_all(&[5, 1, 2, 0]).unwrap();
                for sequence in [first.sequence, second.sequence] {
                    master.write_all(&Frame::json(2, 42, sequence,
                    &json!({"ok":true,"result":{"host_pty_only":true,"sequence":sequence}}))
                    .unwrap().encode().unwrap()).unwrap();
                }
                let repeated_hello = receive(&mut master);
                assert_eq!(repeated_hello.session, 0);
                assert_eq!(repeated_hello.sequence, second.sequence + 1);
                assert_eq!(
                    serde_json::from_slice::<Value>(&repeated_hello.payload).unwrap(),
                    first_json
                );
                master
                    .write_all(
                        &Frame::json(
                            2,
                            42,
                            repeated_hello.sequence,
                            &json!({"ok":true,"result":{"host_pty_only":true}}),
                        )
                        .unwrap()
                        .encode()
                        .unwrap(),
                    )
                    .unwrap();
                let ping = receive(&mut master);
                assert_eq!(ping.session, 42);
                master
                    .write_all(
                        &Frame::json(
                            2,
                            42,
                            ping.sequence,
                            &json!({"ok":true,"result":{"pong":true}}),
                        )
                        .unwrap()
                        .encode()
                        .unwrap(),
                    )
                    .unwrap();
                keep_open.recv_timeout(Duration::from_secs(6)).unwrap();
            });
        let client = muse_video_core::serial::open_port(&path, 115200).unwrap();
        assert_eq!(client.session, 42);
        assert_eq!(client.retries, 3);
        assert_eq!(client.response_timeout, Duration::from_secs(3));
        let diagnostics = client.diagnostics();
        assert_eq!(diagnostics["configured_uart_baud"], 460800);
        assert_eq!(
            diagnostics["baud_discovery_attempts"],
            json!([115200, 460800])
        );
        assert_eq!(diagnostics["transport"]["response_timeouts"], 3);
        assert_eq!(diagnostics["transport"]["retry_attempts"], 2);
        assert_eq!(diagnostics["invalid_wire_frames"], 1);
        let attempts = diagnostics["baud_discovery_results"].as_array().unwrap();
        assert_eq!(attempts.len(), 2);
        assert_eq!(attempts[0]["baud"], 115200);
        assert_eq!(attempts[0]["ack_timeout_ms"], 3000);
        assert_eq!(attempts[0]["max_attempts"], 3);
        assert_eq!(attempts[0]["diagnostics"]["response_timeouts"], 3);
        assert_eq!(attempts[0]["diagnostics"]["received_bytes"], 0);
        assert_eq!(attempts[0]["last_error"]["code"], "timeout");
        assert!(attempts[0]["elapsed_ms"].as_f64().unwrap() >= 9000.0);
        assert_eq!(attempts[1]["baud"], 460800);
        assert_eq!(attempts[1]["ack_timeout_ms"], 1500);
        assert_eq!(attempts[1]["max_attempts"], 2);
        assert_eq!(attempts[1]["diagnostics"]["response_timeouts"], 0);
        assert_eq!(attempts[1]["diagnostics"]["invalid_wire_frames"], 1);
        assert_eq!(attempts[1]["connected"], true);
        assert!(attempts[1]["last_error"].is_null());
        let mut client = client.map_transport(Box::new);
        assert_eq!(client.diagnostics(), diagnostics);
        assert_eq!(client.session, 42);
        assert_eq!(client.retries, 3);
        assert_eq!(client.response_timeout, Duration::from_secs(3));
        assert_eq!(client.hello().unwrap()["host_pty_only"], true);
        assert_eq!(
            client.request(json!({"method":"ping"})).unwrap()["pong"],
            true
        );
        done.send(()).unwrap();
        peer.join().unwrap();
        drop(slave);
    }

    #[test]
    fn selected_port_can_reach_third_baud_after_two_silent_candidates() {
        let (mut master, slave, path) = pair();
        let (done, keep_open) = std::sync::mpsc::channel();
        let peer = thread::spawn(move || {
            let first = receive(&mut master);
            assert_eq!(receive(&mut master), first);
            assert_eq!(receive(&mut master), first);
            let second = receive(&mut master);
            assert_eq!(second.sequence, first.sequence + 1);
            assert_eq!(second.payload, first.payload);
            assert_eq!(receive(&mut master), second);
            let third = receive(&mut master);
            assert_eq!(third.sequence, second.sequence + 1);
            assert_eq!(third.payload, first.payload);
            let reply = Frame::json(
                2,
                42,
                third.sequence,
                &json!({"ok":true,"result":{"host_pty_only":true}}),
            )
            .unwrap();
            master.write_all(&reply.encode().unwrap()).unwrap();
            keep_open.recv_timeout(Duration::from_secs(6)).unwrap();
        });
        let client = muse_video_core::serial::open_port(&path, 115200).unwrap();
        let diagnostics = client.diagnostics();
        assert_eq!(diagnostics["configured_uart_baud"], 921600);
        assert_eq!(
            diagnostics["baud_discovery_attempts"],
            json!([115200, 460800, 921600])
        );
        assert_eq!(diagnostics["transport"]["response_timeouts"], 5);
        assert_eq!(diagnostics["transport"]["retry_attempts"], 3);
        assert_eq!(client.session, 42);
        assert_eq!(client.response_timeout, Duration::from_secs(3));
        assert_eq!(client.retries, 3);
        done.send(()).unwrap();
        peer.join().unwrap();
        drop(slave);
    }

    #[test]
    fn selected_port_reaches_highest_candidate_and_preserves_each_attempts_diagnostics() {
        let (mut master, slave, path) = pair();
        let (done, keep_open) = std::sync::mpsc::channel();
        let peer = thread::spawn(move || {
            let mut initial: Option<Frame> = None;
            let mut previous = None;
            for index in 0..4 {
                let hello = receive(&mut master);
                if let Some(first) = &initial {
                    assert_eq!(hello.sequence, first.sequence + index);
                    assert_eq!(hello.payload, first.payload);
                } else {
                    initial = Some(hello.clone());
                }
                assert_eq!(hello.session, 0);
                if index == 3 {
                    // Invalid input at 2 Mbaud remains attributable to that
                    // candidate after the same handle moves to 3 Mbaud.
                    master.write_all(&[5, 1, 2, 0]).unwrap();
                }
                for _ in 1..if index == 0 { 3 } else { 2 } {
                    assert_eq!(receive(&mut master), hello);
                }
                previous = Some(hello);
            }
            let highest = receive(&mut master);
            let previous = previous.unwrap();
            assert_eq!(highest.sequence, previous.sequence + 1);
            assert_eq!(highest.payload, previous.payload);
            master.write_all(&[5, 1, 2, 0]).unwrap();
            // A valid but stale previous candidate's reply cannot satisfy the
            // highest candidate; the current sequence is still required.
            for sequence in [previous.sequence, highest.sequence] {
                master
                    .write_all(
                        &Frame::json(
                            2,
                            42,
                            sequence,
                            &json!({"ok":true,"result":{"host_pty_only":true}}),
                        )
                        .unwrap()
                        .encode()
                        .unwrap(),
                    )
                    .unwrap();
            }
            let ping = receive(&mut master);
            assert_eq!(ping.session, 42);
            assert_eq!(ping.sequence, highest.sequence + 1);
            master
                .write_all(
                    &Frame::json(
                        2,
                        42,
                        ping.sequence,
                        &json!({"ok":true,"result":{"pong":true}}),
                    )
                    .unwrap()
                    .encode()
                    .unwrap(),
                )
                .unwrap();
            keep_open.recv_timeout(Duration::from_secs(6)).unwrap();
        });
        let client = muse_video_core::serial::open_port(&path, 115200).unwrap();
        let diagnostics = client.diagnostics();
        let rates = [115200, 460800, 921600, 2000000, 3000000];
        assert_eq!(diagnostics["configured_uart_baud"], 3000000);
        assert_eq!(diagnostics["baud_discovery_attempts"], json!(rates));
        assert_eq!(diagnostics["transport"]["response_timeouts"], 9);
        assert_eq!(diagnostics["transport"]["retry_attempts"], 5);
        assert_eq!(diagnostics["invalid_wire_frames"], 2);
        let attempts = diagnostics["baud_discovery_results"].as_array().unwrap();
        assert_eq!(attempts.len(), rates.len());
        for (index, attempt) in attempts.iter().enumerate() {
            assert_eq!(attempt["baud"], rates[index]);
            assert_eq!(
                attempt["ack_timeout_ms"],
                if index == 0 { 3000 } else { 1500 }
            );
            assert_eq!(attempt["max_attempts"], if index == 0 { 3 } else { 2 });
            assert_eq!(attempt["diagnostics"]["scope"], "this_baud_candidate");
            assert_eq!(attempt["connected"], index == 4);
            assert_eq!(
                attempt["diagnostics"]["response_timeouts"],
                if index == 0 {
                    3
                } else if index == 4 {
                    0
                } else {
                    2
                }
            );
            assert_eq!(
                attempt["diagnostics"]["invalid_wire_frames"],
                if index >= 3 { 1 } else { 0 }
            );
        }
        assert!(attempts[0]["elapsed_ms"].as_f64().unwrap() >= 9000.0);
        let mut client = client.map_transport(Box::new);
        assert_eq!(client.diagnostics(), diagnostics);
        assert_eq!(client.response_timeout, Duration::from_secs(3));
        assert_eq!(client.retries, 3);
        assert_eq!(
            client.request(json!({"method":"ping"})).unwrap()["pong"],
            true
        );
        done.send(()).unwrap();
        peer.join().unwrap();
        drop(slave);
    }

    #[test]
    fn silent_discovery_is_bounded_to_the_selected_ports_five_candidates() {
        let (mut master, slave, path) = pair();
        let (done, keep_open) = std::sync::mpsc::channel();
        let peer = thread::spawn(move || {
            let first = receive(&mut master);
            for index in 0..5 {
                let hello = if index == 0 {
                    first.clone()
                } else {
                    receive(&mut master)
                };
                assert_eq!(hello.session, 0);
                assert_eq!(hello.sequence, first.sequence + index);
                assert_eq!(hello.payload, first.payload);
                for _ in 1..if index == 0 { 3 } else { 2 } {
                    assert_eq!(receive(&mut master), hello);
                }
            }
            keep_open.recv_timeout(Duration::from_secs(6)).unwrap();
        });
        let started = Instant::now();
        let error = muse_video_core::serial::open_port(&path, 115200)
            .err()
            .unwrap();
        assert_eq!(error.code, "timeout");
        let elapsed = started.elapsed();
        assert!(elapsed >= Duration::from_secs(21));
        assert!(elapsed < Duration::from_secs(30));
        let diagnostics: Value =
            serde_json::from_str(error.details.rsplit_once("; host diagnostics ").unwrap().1)
                .unwrap();
        assert_eq!(diagnostics["configured_uart_baud"], 3000000);
        assert_eq!(
            diagnostics["baud_discovery_attempts"],
            json!([115200, 460800, 921600, 2000000, 3000000])
        );
        assert_eq!(diagnostics["transport"]["response_timeouts"], 11);
        assert_eq!(diagnostics["transport"]["retry_attempts"], 6);
        let attempts = diagnostics["baud_discovery_results"].as_array().unwrap();
        assert_eq!(attempts.len(), 5);
        for (index, attempt) in attempts.iter().enumerate() {
            assert_eq!(attempt["connected"], false);
            assert_eq!(attempt["last_error"]["code"], "timeout");
            assert_eq!(
                attempt["ack_timeout_ms"],
                if index == 0 { 3000 } else { 1500 }
            );
            assert_eq!(attempt["max_attempts"], if index == 0 { 3 } else { 2 });
            assert_eq!(
                attempt["diagnostics"]["response_timeouts"],
                if index == 0 { 3 } else { 2 }
            );
        }
        done.send(()).unwrap();
        peer.join().unwrap();
        drop(slave);
    }

    #[test]
    fn explicit_high_rates_connect_without_discovery_and_keep_full_startup_margin() {
        for baud in [2000000, 3000000] {
            let (mut master, slave, path) = pair();
            let (done, keep_open) = std::sync::mpsc::channel();
            let peer = thread::spawn(move || {
                let hello = receive(&mut master);
                master
                    .write_all(
                        &Frame::json(
                            2,
                            42,
                            hello.sequence,
                            &json!({"ok":true,"result":{"host_pty_only":true}}),
                        )
                        .unwrap()
                        .encode()
                        .unwrap(),
                    )
                    .unwrap();
                keep_open.recv_timeout(Duration::from_secs(6)).unwrap();
            });
            let client = muse_video_core::serial::open_port(&path, baud).unwrap();
            let diagnostics = client.diagnostics();
            assert_eq!(diagnostics["configured_uart_baud"], baud);
            assert_eq!(diagnostics["baud_discovery_attempts"], json!([baud]));
            let attempts = diagnostics["baud_discovery_results"].as_array().unwrap();
            assert_eq!(attempts.len(), 1);
            assert_eq!(attempts[0]["ack_timeout_ms"], 3000);
            assert_eq!(attempts[0]["max_attempts"], 3);
            assert_eq!(attempts[0]["connected"], true);
            assert_eq!(diagnostics["transport"]["response_timeouts"], 0);
            assert_eq!(client.response_timeout, Duration::from_secs(3));
            assert_eq!(client.retries, 3);
            done.send(()).unwrap();
            peer.join().unwrap();
            drop(slave);
        }
    }

    #[test]
    fn invalid_baud_is_rejected_before_opening_any_port() {
        for baud in [
            0,
            9600,
            115201,
            1999999,
            2000001,
            2999999,
            3000001,
            u32::MAX,
        ] {
            let error = muse_video_core::serial::open_port("/unopened-baud-fixture", baud)
                .err()
                .unwrap();
            assert_eq!(error.code, "invalid_parameter");
            assert_eq!(error.details, "Unsupported UART baud");
        }
    }

    #[test]
    fn discovery_preserves_valid_device_errors_and_explicit_faster_rate() {
        for baud in [115200, 460800, 921600, 2000000, 3000000] {
            let (mut master, slave, path) = pair();
            let peer = thread::spawn(move || {
                let hello = receive(&mut master);
                master.write_all(&Frame::json(2, 7, hello.sequence,
                    &json!({"ok":false,"error":{"code":"unsupported_firmware","details":"Host PTY rejection fixture"}}))
                    .unwrap().encode().unwrap()).unwrap();
                // Keep the master open until the reply has been consumed.
                thread::sleep(Duration::from_millis(100));
            });
            let started = Instant::now();
            let error = muse_video_core::serial::open_port(&path, baud)
                .err()
                .unwrap();
            assert_eq!(error.code, "unsupported_firmware");
            assert!(error.details.contains("Host PTY rejection fixture"));
            let diagnostics: Value = serde_json::from_str(
                error
                    .details
                    .rsplit_once("; baud discovery diagnostics ")
                    .unwrap()
                    .1,
            )
            .unwrap();
            assert_eq!(diagnostics["baud_discovery_attempts"], json!([baud]));
            assert_eq!(diagnostics["baud_discovery_results"][0]["max_attempts"], 3);
            assert_eq!(
                diagnostics["baud_discovery_results"][0]["ack_timeout_ms"],
                3000
            );
            assert_eq!(
                diagnostics["baud_discovery_results"][0]["last_error"]["code"],
                "unsupported_firmware"
            );
            assert!(started.elapsed() < Duration::from_secs(1));
            peer.join().unwrap();
            drop(slave);
        }
    }
}
