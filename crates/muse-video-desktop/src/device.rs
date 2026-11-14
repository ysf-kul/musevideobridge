use muse_video_core::{
    media::ProgressSink,
    serial::{self, Client, Simulator},
    Cancellation, Error, Result,
};
use serde_json::{json, Value};
use std::{
    io::{Read, Write},
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, SyncSender},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
pub type Emitter = Arc<dyn Fn(&str, Value) + Send + Sync>;
trait Transport: Read + Write + Send {}
impl<T: Read + Write + Send> Transport for T {}
pub enum Message {
    Connect(String),
    Disconnect,
    Command(Value, SyncSender<Result<Value>>),
    Upload(
        PathBuf,
        Cancellation,
        ProgressSink,
        SyncSender<Result<Value>>,
    ),
    Shutdown,
}
#[derive(Clone)]
pub struct Device {
    sender: SyncSender<Message>,
    stopping: Arc<AtomicBool>,
    transferring: Arc<AtomicBool>,
    worker: Arc<Mutex<Option<thread::JoinHandle<()>>>>,
}
impl Device {
    pub fn start(emit: Emitter, simulator: Option<(PathBuf, PathBuf)>) -> Self {
        let (tx, rx) = mpsc::sync_channel(16);
        let stopping = Arc::new(AtomicBool::new(false));
        let stopped = stopping.clone();
        let transferring = Arc::new(AtomicBool::new(false));
        let active_transfer = transferring.clone();
        let worker = thread::spawn(move || {
            let mut client: Option<Client<Box<dyn Transport>>> = None;
            let mut next_status = Instant::now();
            let disconnected = |code: &str, error: Option<Error>| {
                emit(
                    "device",
                    json!({"connection":code,"error":error,"received_at_ms":now_ms()}),
                );
            };
            while !stopped.load(Ordering::Acquire) {
                let message = rx.recv_timeout(if client.is_some() {
                    Duration::from_millis(1)
                } else {
                    Duration::from_millis(100)
                });
                match message {
                    Ok(Message::Shutdown) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
                    Ok(Message::Disconnect) => {
                        client = None;
                        disconnected("disconnected", None);
                    }
                    Ok(Message::Connect(port)) => {
                        client = None;
                        emit("device", json!({"connection":"connecting","port":port}));
                        let opened: Result<Client<Box<dyn Transport>>> = if let Some((exe, sd)) =
                            &simulator
                        {
                            Simulator::spawn(exe, sd)
                                .map(|s| Client::new(Box::new(s) as Box<dyn Transport>))
                        } else {
                            serial::open_port(&port, 115200)
                                .map(|c| c.map_transport(|io| Box::new(io) as Box<dyn Transport>))
                        };
                        let result=opened.and_then(|mut c|{let hello=c.hello()?;let status=c.request(json!({"method":"get_telemetry"}))?;emit("device",json!({"connection":"connected","port":port,"hello":hello,"status":status,"session":c.session,"host_diagnostics":c.diagnostics(),"received_at_ms":now_ms()}));client=Some(c);next_status=Instant::now()+Duration::from_secs(1);Ok(())});
                        if let Err(e) = result {
                            disconnected("disconnected", Some(e));
                        }
                    }
                    Ok(Message::Command(cmd, reply)) => {
                        let result = client
                            .as_mut()
                            .ok_or_else(|| Error::new("disconnected", "No selected connection"))
                            .and_then(|c| c.request(cmd));
                        let _ = reply.send(result);
                        next_status = Instant::now();
                    }
                    Ok(Message::Upload(path, cancel, progress, reply)) => {
                        let result = client
                            .as_mut()
                            .ok_or_else(|| Error::new("disconnected", "No selected connection"))
                            .and_then(|c| c.upload(&path, &cancel, progress));
                        active_transfer.store(false, Ordering::Release);
                        let _ = reply.send(result);
                        next_status = Instant::now();
                    }
                    Err(mpsc::RecvTimeoutError::Timeout) => {}
                }
                if let Some(c) = client.as_mut() {
                    let result = (|| {
                        if Instant::now() >= next_status {
                            let status = c.request(json!({"method":"get_telemetry"}))?;
                            emit(
                                "device",
                                json!({"connection":"connected","status":status,"session":c.session,"received_at_ms":now_ms()}),
                            );
                            next_status = Instant::now() + Duration::from_secs(1);
                        }
                        c.poll_events(Duration::from_millis(15))?;
                        while let Some(e) = c.events.pop_front() {
                            match e["event"].as_str() {
                                Some("frame") => emit(
                                    "frame",
                                    json!({"frame":e,"session":c.session,"received_at_ms":now_ms()}),
                                ),
                                Some("telemetry") => {}
                                Some("error") => emit("device_error", e),
                                _ => {}
                            }
                        }
                        Ok::<_, Error>(())
                    })();
                    if let Err(e) = result {
                        client = None;
                        disconnected(
                            if e.code == "timeout" || e.code == "stale_session" {
                                "unresponsive"
                            } else {
                                "disconnected"
                            },
                            Some(e),
                        );
                    }
                }
            }
        });
        Self {
            sender: tx,
            stopping,
            transferring,
            worker: Arc::new(Mutex::new(Some(worker))),
        }
    }
    pub fn connect(&self, port: String) -> Result<()> {
        self.check_transfer()?;
        self.send(Message::Connect(port))
    }
    pub fn disconnect(&self) -> Result<()> {
        self.check_transfer()?;
        self.send(Message::Disconnect)
    }
    fn send(&self, m: Message) -> Result<()> {
        self.sender
            .try_send(m)
            .map_err(|_| Error::new("busy", "Serial command queue full or closed"))
    }
    pub fn command(&self, value: Value) -> Result<Value> {
        self.check_transfer()?;
        let (tx, rx) = mpsc::sync_channel(1);
        self.send(Message::Command(value, tx))?;
        rx.recv_timeout(Duration::from_secs(10))
            .map_err(|_| Error::new("timeout", "Serial actor response"))?
    }
    pub fn upload(&self, path: PathBuf, cancel: Cancellation, sink: ProgressSink) -> Result<Value> {
        if self.transferring.swap(true, Ordering::AcqRel) {
            return Err(Error::new(
                "busy",
                "USB transfer already owns the connection",
            ));
        }
        let (tx, rx) = mpsc::sync_channel(1);
        if let Err(error) = self.send(Message::Upload(path, cancel, sink, tx)) {
            self.transferring.store(false, Ordering::Release);
            return Err(error);
        }
        rx.recv()
            .map_err(|_| Error::new("disconnected", "Serial upload actor"))?
    }
    fn check_transfer(&self) -> Result<()> {
        if self.transferring.load(Ordering::Acquire) {
            return Err(Error::new("busy", "USB transfer owns the connection; cancel the transfer before changing devices or sending controls"));
        }
        Ok(())
    }
    pub fn shutdown(&self) {
        self.stopping.store(true, Ordering::Release);
        let _ = self.sender.try_send(Message::Shutdown);
        if let Some(worker) = self.worker.lock().unwrap().take() {
            let _ = worker.join();
        }
    }
}
pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
