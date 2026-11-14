//! Explicit host-only verification bridge: real App/FFmpeg/storage/native engine.
//! Not compiled into the desktop application. No physical port or SD access.
use muse_video_core::{media::Options, Error, Result};
use muse_video_desktop::App;
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    sync::{Arc, Mutex},
    thread,
};
fn main() {
    let root = PathBuf::from(std::env::var("MVB_TEST_ROOT").expect("isolated test root required"));
    std::fs::create_dir_all(root.join("card")).unwrap();
    let events = Arc::new(Mutex::new(VecDeque::<Value>::new()));
    let sink = events.clone();
    let app = App::new(
        root.join("config"),
        root.join("cache"),
        Arc::new(move |name, payload| {
            let mut q = sink.lock().unwrap();
            if q.len() >= 64 {
                q.pop_front();
            }
            q.push_back(json!({"name":name,"payload":payload}));
        }),
        Some((
            std::env::var("MVB_SIMULATOR")
                .expect("native simulator required")
                .into(),
            root.join("card"),
        )),
    )
    .unwrap();
    let listener = TcpListener::bind("127.0.0.1:1431").unwrap();
    println!("Host-only desktop test bridge on localhost:1431");
    for socket in listener.incoming() {
        let app = app.clone();
        let root = root.clone();
        let events = events.clone();
        thread::spawn(move || {
            if let Ok(socket) = socket {
                let _ = serve(socket, app, root, events);
            }
        });
    }
}
fn serve(
    mut socket: TcpStream,
    app: App,
    root: PathBuf,
    events: Arc<Mutex<VecDeque<Value>>>,
) -> Result<()> {
    socket.set_read_timeout(Some(std::time::Duration::from_secs(3)))?;
    let mut data = Vec::new();
    let mut buf = [0; 4096];
    let (header_end, length) = loop {
        let n = socket.read(&mut buf)?;
        if n == 0 {
            return Ok(());
        }
        data.extend(&buf[..n]);
        if data.len() > 65536 {
            return Err(Error::new("invalid_parameter", "request size"));
        }
        if let Some(pos) = data.windows(4).position(|b| b == b"\r\n\r\n") {
            let header = String::from_utf8_lossy(&data[..pos]);
            let length = header
                .lines()
                .find_map(|s| {
                    s.to_lowercase()
                        .strip_prefix("content-length:")
                        .and_then(|s| s.trim().parse::<usize>().ok())
                })
                .unwrap_or(0);
            break (pos + 4, length);
        }
    };
    while data.len() < header_end + length {
        let n = socket.read(&mut buf)?;
        if n == 0 {
            break;
        }
        data.extend(&buf[..n]);
    }
    let header = String::from_utf8_lossy(&data[..header_end]);
    let result = if header.starts_with("OPTIONS ") {
        Ok(Value::Null)
    } else if header.starts_with("GET /events ") {
        Ok(json!(events.lock().unwrap().drain(..).collect::<Vec<_>>()))
    } else {
        let request: Value = serde_json::from_slice(&data[header_end..header_end + length])?;
        let args = &request["args"];
        match request["command"].as_str().unwrap_or("") {
            "snapshot" => Ok(serde_json::to_value(app.snapshot())?),
            "save_settings" => app
                .settings(serde_json::from_value(args["settings"].clone())?)
                .and_then(|s| Ok(serde_json::to_value(s)?)),
            "select_video" => app
                .select(&root.join("input.mp4"))
                .and_then(|s| Ok(serde_json::to_value(s)?)),
            "source_preview" => app.source_preview().map(|b| json!(b)),
            "frame" => app
                .frame(
                    args["index"].as_u64().unwrap_or(0) as u32,
                    args["digest"].as_str().unwrap_or(""),
                )
                .map(|b| json!(b)),
            "ports" => Ok(json!(["host-test-simulator"])),
            "connect" => app
                .device
                .connect("host-test-simulator".into())
                .map(|_| Value::Null),
            "disconnect" => app.device.disconnect().map(|_| Value::Null),
            "command" => app.command(args["value"].clone()),
            "estimate" => app
                .estimate(&serde_json::from_value::<Options>(args["options"].clone())?)
                .and_then(|s| Ok(serde_json::to_value(s)?)),
            "convert" => app
                .convert(serde_json::from_value(args["options"].clone())?)
                .map(|_| Value::Null),
            "select_card" => app.destination(&root.join("card")).map(|mut d| {
                d.fat32_compatible = true;
                json!([root.join("card").to_string_lossy(), d])
            }),
            "export" => app.export(root.join("card"), true).map(|_| Value::Null),
            "upload" => app
                .upload_video(args["videoId"].as_str().map(str::to_owned))
                .map(|_| Value::Null),
            "cancel" => {
                app.cancel();
                Ok(Value::Null)
            }
            _ => Err(Error::new("unsupported_command", "Test bridge")),
        }
    };
    let body = serde_json::to_vec(&match result {
        Ok(result) => json!({"ok":true,"result":result}),
        Err(error) => json!({"ok":false,"error":error}),
    })?;
    write!(socket,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: http://127.0.0.1:1420\r\nAccess-Control-Allow-Headers: content-type\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",body.len())?;
    socket.write_all(&body)?;
    Ok(())
}
