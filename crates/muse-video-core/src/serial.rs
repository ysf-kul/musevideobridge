use crate::{
    container::{u32le, Video},
    media::{progress, ProgressSink},
    protocol::{Frame, MAX_WIRE},
    storage::digest_file,
    Cancellation, Error, Result,
};
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    fs::File,
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Default, serde::Serialize)]
struct TransportCounters {
    read_calls: u64,
    received_bytes: u64,
    write_calls: u64,
    written_bytes: u64,
    retry_attempts: u64,
    response_timeouts: u64,
}

pub struct Client<T: Read + Write> {
    io: T,
    pub session: u32,
    sequence: u32,
    client_nonce: String,
    incoming: Vec<u8>,
    receive: [u8; 512],
    receive_at: usize,
    receive_len: usize,
    oversized: bool,
    invalid_frames: u64,
    counters: TransportCounters,
    configured_uart_baud: Option<u32>,
    baud_discovery_attempts: Vec<u32>,
    baud_discovery_results: Vec<Value>,
    pub events: VecDeque<Value>,
    pub retries: u8,
    pub response_timeout: Duration,
}
impl<T: Read + Write> Client<T> {
    pub fn new(io: T) -> Self {
        Self {
            io,
            session: 0,
            sequence: 0,
            client_nonce: crate::storage::unique(),
            incoming: Vec::with_capacity(MAX_WIRE),
            receive: [0; 512],
            receive_at: 0,
            receive_len: 0,
            oversized: false,
            invalid_frames: 0,
            counters: TransportCounters::default(),
            configured_uart_baud: None,
            baud_discovery_attempts: Vec::new(),
            baud_discovery_results: Vec::new(),
            events: VecDeque::new(),
            retries: 3,
            response_timeout: Duration::from_secs(3),
        }
    }
    pub fn diagnostics(&self) -> Value {
        json!({"scope":"connection_lifetime", "transport":self.counters,
               "invalid_wire_frames":self.invalid_frames,
               "configured_uart_baud":self.configured_uart_baud,
               "baud_discovery_attempts":self.baud_discovery_attempts,
               "baud_discovery_results":self.baud_discovery_results,
               "queued_events":self.events.len(),
               "buffered_receive_bytes":self.receive_len-self.receive_at})
    }
    fn next(&mut self) -> Result<u32> {
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or_else(|| Error::new("stale_session", "Sequence exhausted; reconnect"))?;
        Ok(self.sequence)
    }
    fn exchange(&mut self, frame: Frame, expected: u8) -> Result<Frame> {
        self.exchange_cancellable(frame, expected, None)
    }
    fn write_frame(&mut self, wire: &[u8], cancel: Option<&Cancellation>) -> Result<()> {
        let deadline = Instant::now() + Duration::from_secs(3);
        let mut written = 0;
        loop {
            if let Some(c) = cancel {
                c.check()?;
            }
            if Instant::now() >= deadline {
                return Err(Error::new("timeout", format!("Serial write/flush timed out: {written}/{} framed bytes accepted by transport", wire.len())));
            }
            let result = if written < wire.len() {
                self.counters.write_calls = self.counters.write_calls.saturating_add(1);
                match self.io.write(&wire[written..]) {
                    Ok(0) => {
                        return Err(Error::new(
                            "disconnected",
                            "Serial write returned zero bytes",
                        ))
                    }
                    Ok(n) => {
                        written += n;
                        self.counters.written_bytes =
                            self.counters.written_bytes.saturating_add(n as u64);
                        Ok(false)
                    }
                    Err(e) => Err(e),
                }
            } else {
                self.io.flush().map(|_| true)
            };
            match result {
                Ok(true) => return Ok(()),
                Ok(false) => {}
                Err(e)
                    if matches!(
                        e.kind(),
                        std::io::ErrorKind::TimedOut
                            | std::io::ErrorKind::WouldBlock
                            | std::io::ErrorKind::Interrupted
                    ) =>
                {
                    thread::sleep(Duration::from_millis(1));
                }
                Err(e) => {
                    return Err(Error::new(
                        "io_error",
                        format!(
                            "Serial write/flush after {written}/{} bytes: {e}",
                            wire.len()
                        ),
                    ))
                }
            }
        }
    }
    fn exchange_cancellable(
        &mut self,
        frame: Frame,
        expected: u8,
        cancel: Option<&Cancellation>,
    ) -> Result<Frame> {
        // A delimiter also abandons a partially written previous frame safely.
        let mut wire = vec![0];
        wire.extend(frame.encode()?);
        for attempt in 0..self.retries {
            if attempt != 0 {
                self.counters.retry_attempts = self.counters.retry_attempts.saturating_add(1);
            }
            self.write_frame(&wire, cancel)?;
            let deadline = Instant::now() + self.response_timeout;
            while Instant::now() < deadline {
                if let Some(c) = cancel {
                    c.check()?;
                }
                if let Some(reply) = self.read_frame()? {
                    if reply.kind == 3 && reply.session == self.session {
                        self.event(reply);
                    } else if reply.sequence == frame.sequence
                        && (frame.session == 0 || reply.session == frame.session || reply.kind == 2)
                    {
                        let same_session = frame.session == 0 || reply.session == frame.session;
                        if reply.kind == expected && same_session {
                            return Ok(reply);
                        }
                        if reply.kind == 2 {
                            let value: Value = serde_json::from_slice(&reply.payload)?;
                            if value["ok"] == false
                                && (same_session || value["error"]["code"] == "stale_session")
                            {
                                return Err(Error::new(
                                    value["error"]["code"].as_str().unwrap_or("device_error"),
                                    format!(
                                        "{} (sequence {}, frame kind {})",
                                        value["error"]["details"]
                                            .as_str()
                                            .unwrap_or("Device rejected frame"),
                                        frame.sequence,
                                        frame.kind
                                    ),
                                ));
                            }
                        }
                    }
                }
            }
            self.counters.response_timeouts = self.counters.response_timeouts.saturating_add(1);
        }
        Err(Error::new(
            "timeout",
            format!("No matching reply: session {}, sequence {}, sent kind {}, expected kind {}, {} attempts, {} ms per ACK; {} invalid/oversized received frames; host diagnostics {}", frame.session, frame.sequence, frame.kind, expected, self.retries, self.response_timeout.as_millis(), self.invalid_frames, self.diagnostics()),
        ))
    }
    fn event(&mut self, frame: Frame) {
        if let Ok(value) = serde_json::from_slice(&frame.payload) {
            if self.events.len() >= 32 {
                self.events.pop_front();
            }
            self.events.push_back(value);
        }
    }
    fn read_frame(&mut self) -> Result<Option<Frame>> {
        if self.receive_at == self.receive_len {
            self.counters.read_calls = self.counters.read_calls.saturating_add(1);
            match self.io.read(&mut self.receive) {
                Ok(0) => return Err(Error::new("disconnected", "Serial stream closed")),
                Ok(n) => {
                    self.receive_at = 0;
                    self.receive_len = n;
                    self.counters.received_bytes =
                        self.counters.received_bytes.saturating_add(n as u64);
                }
                Err(e)
                    if matches!(
                        e.kind(),
                        std::io::ErrorKind::TimedOut
                            | std::io::ErrorKind::WouldBlock
                            | std::io::ErrorKind::Interrupted
                    ) =>
                {
                    return Ok(None)
                }
                Err(e) => return Err(e.into()),
            }
        }
        // Consume one bounded read batch. Preserve its suffix when a frame is
        // returned: USB drivers can coalesce ACKs, responses and events in a read.
        while self.receive_at < self.receive_len {
            let byte = self.receive[self.receive_at];
            self.receive_at += 1;
            if byte != 0 {
                if self.incoming.len() + 1 >= MAX_WIRE {
                    self.oversized = true;
                } else if !self.oversized {
                    self.incoming.push(byte);
                }
                continue;
            }
            let result = if self.oversized {
                None
            } else {
                Frame::decode(&self.incoming).ok()
            };
            if result.is_none() && (!self.incoming.is_empty() || self.oversized) {
                self.invalid_frames = self.invalid_frames.saturating_add(1);
            }
            self.incoming.clear();
            self.oversized = false;
            if result.is_some() {
                return Ok(result);
            }
        }
        Ok(None)
    }
    pub fn into_transport(self) -> T {
        self.io
    }
    /// Wrap the same transport without losing its handshake, buffered frames or
    /// diagnostics. Used by the desktop actor's selected-port abstraction.
    pub fn map_transport<U: Read + Write>(self, map: impl FnOnce(T) -> U) -> Client<U> {
        Client {
            io: map(self.io),
            session: self.session,
            sequence: self.sequence,
            client_nonce: self.client_nonce,
            incoming: self.incoming,
            receive: self.receive,
            receive_at: self.receive_at,
            receive_len: self.receive_len,
            oversized: self.oversized,
            invalid_frames: self.invalid_frames,
            counters: self.counters,
            configured_uart_baud: self.configured_uart_baud,
            baud_discovery_attempts: self.baud_discovery_attempts,
            baud_discovery_results: self.baud_discovery_results,
            events: self.events,
            retries: self.retries,
            response_timeout: self.response_timeout,
        }
    }
    pub fn poll_events(&mut self, budget: Duration) -> Result<()> {
        let deadline = Instant::now() + budget;
        while Instant::now() < deadline {
            if let Some(f) = self.read_frame()? {
                if f.kind == 3 && f.session == self.session {
                    self.event(f);
                }
            }
        }
        Ok(())
    }
    pub fn request(&mut self, command: Value) -> Result<Value> {
        let sequence = self.next()?;
        let reply = self.exchange(Frame::json(1, self.session, sequence, &command)?, 2)?;
        let value: Value = serde_json::from_slice(&reply.payload)?;
        if value["ok"] != true {
            return Err(Error::new(
                value["error"]["code"].as_str().unwrap_or("device_error"),
                value["error"]["details"]
                    .as_str()
                    .unwrap_or("Device rejected command"),
            ));
        }
        if command["method"] == "hello" || command["method"] == "HELLO" {
            if reply.session == 0 {
                return Err(Error::new("unsupported_firmware", "No session ID"));
            }
            self.session = reply.session;
        }
        Ok(value["result"].clone())
    }
    pub fn hello(&mut self) -> Result<Value> {
        self.session = 0;
        self.request(json!({"method":"hello","protocol_min":1,"protocol_max":1,"client_nonce":self.client_nonce}))
    }
    /// Recover bounded ACK/session failures using the real verified SD checkpoint.
    /// No new serial device is opened, and storage/integrity failures are returned.
    pub fn upload_resumable(
        &mut self,
        path: &Path,
        cancel: &Cancellation,
        sink: ProgressSink,
    ) -> Result<Value> {
        let mut attempts = 0;
        loop {
            match self.upload(path, cancel, sink.clone()) {
                Err(ref error)
                    if !cancel.is_cancelled()
                        && attempts < 2
                        && ["timeout", "stale_session"].contains(&error.code.as_str()) =>
                {
                    attempts += 1;
                    progress(&sink, "retrying_upload", 0, None);
                    self.hello()?;
                }
                Ok(mut result) => {
                    result["host_transfer"]["recovery_attempts"] = json!(attempts);
                    return Ok(result);
                }
                other => return other,
            }
        }
    }
    pub fn upload(
        &mut self,
        path: &Path,
        cancel: &Cancellation,
        sink: ProgressSink,
    ) -> Result<Value> {
        let mut video = Video::open(File::open(path)?, cancel)?;
        video.verify(cancel)?;
        let size = video.header.file_bytes;
        let digest = digest_file(path, cancel)?;
        cancel.check()?;
        let upload_started = Instant::now();
        let mut start = self.request(
            json!({"method":"begin_upload","sha256":digest,"size":size,"container_version":1}),
        )?;
        let id = start["transfer_id"]
            .as_u64()
            .filter(|n| *n != 0 && *n <= u64::from(u32::MAX))
            .ok_or_else(|| Error::new("invalid_frame", "No transfer ID"))? as u32;
        let result = (|| {
            let deadline = Instant::now() + Duration::from_secs(24 * 3600);
            while start["state"] == "resuming" {
                if cancel.is_cancelled() {
                    return Err(Error::new("cancelled", "Upload resume cancelled"));
                }
                if Instant::now() > deadline {
                    return Err(Error::new("timeout", "Resume verification"));
                }
                progress(
                    &sink,
                    "resuming_upload",
                    start["verified_bytes"].as_u64().unwrap_or(0),
                    Some(size),
                );
                thread::sleep(Duration::from_millis(100));
                start = self.request(json!({"method":"upload_status","transfer_id":id}))?;
            }
            let already_verifying = start["state"] == "verifying";
            if start["state"] != "receiving" && !already_verifying {
                return Err(Error::new(
                    start["error"].as_str().unwrap_or("verification_failed"),
                    format!("Device could not resume transfer {id}: {start}"),
                ));
            }
            let mut offset = start["accepted_offset"]
                .as_u64()
                .filter(|n| *n <= size)
                .ok_or_else(|| Error::new("invalid_frame", "Bad resume offset"))?;
            let initial_offset = offset;
            if already_verifying && offset != size {
                return Err(Error::new(
                    "incomplete_transfer",
                    "Device is verifying an incomplete file",
                ));
            }
            let receive_started = Instant::now();
            let mut file = File::open(path)?;
            file.seek(SeekFrom::Start(offset))?;
            let mut data = [0; 1024];
            let mut last_progress = Instant::now() - Duration::from_secs(1);
            // Firmware may advertise a smaller tested limit; never exceed v1's cap.
            let chunk_bytes = start["chunk_bytes"].as_u64().unwrap_or(1024).min(1024) as usize;
            if chunk_bytes == 0 {
                return Err(Error::new(
                    "invalid_frame",
                    "Device advertised zero chunk bytes",
                ));
            }
            while offset < size {
                cancel.check()?;
                let count = ((size - offset) as usize).min(chunk_bytes);
                file.read_exact(&mut data[..count])?;
                let mut payload = Vec::with_capacity(12 + count);
                payload.extend_from_slice(&id.to_le_bytes());
                payload.extend_from_slice(&(offset as u32).to_le_bytes());
                payload.extend_from_slice(&(count as u16).to_le_bytes());
                payload.extend_from_slice(&[0; 2]);
                payload.extend_from_slice(&data[..count]);
                let sequence = self.next()?;
                let reply = self.exchange_cancellable(
                    Frame {
                        kind: 16,
                        session: self.session,
                        sequence,
                        payload,
                    },
                    17,
                    Some(cancel),
                )?;
                if reply.payload.len() != 16 {
                    return Err(Error::new(
                        "invalid_frame",
                        format!(
                            "Transfer {id}, sequence {sequence}: ACK length {} instead of 16",
                            reply.payload.len()
                        ),
                    ));
                }
                let ack_status = crate::container::u16le(&reply.payload, 12);
                if ack_status == 3 {
                    let status = match self.request(json!({"method":"upload_status","transfer_id":id})) {
                        Ok(status) => status,
                        Err(diagnostic) => return Err(Error::new("sd_write_failed", format!("Device storage NACK (status 3) for transfer {id}, sequence {sequence}, offset {offset}; diagnostic query failed: {}: {}", diagnostic.code, diagnostic.details))),
                    };
                    return Err(Error::new(status["error"].as_str().unwrap_or("sd_write_failed"), format!("SD write/checkpoint rejected transfer {id}, sequence {sequence}, file offset {offset}: {status}")));
                }
                if u32le(&reply.payload, 0) != id
                    || u32le(&reply.payload, 4) as u64 != offset + count as u64
                    || u32le(&reply.payload, 8) > u32le(&reply.payload, 4)
                    || crate::container::u16le(&reply.payload, 14) != 0
                    || ack_status > 1
                {
                    return Err(Error::new(
                    "offset_mismatch",
                    format!("Transfer {id}, sequence {sequence}, ACK status {ack_status}: expected offset {}, device accepted {}, checkpointed {}, device transfer {}", offset + count as u64, u32le(&reply.payload, 4), u32le(&reply.payload, 8), u32le(&reply.payload, 0)),
                ));
                }
                offset += count as u64;
                if last_progress.elapsed() >= Duration::from_millis(100) || offset == size {
                    let seconds = receive_started.elapsed().as_secs_f64();
                    let rate = (offset - initial_offset) as f64 / seconds.max(0.001);
                    if let Some(sender) = &sink {
                        let _ = sender.try_send(crate::media::Progress {
                            stage: "uploading".into(),
                            completed: offset,
                            total: Some(size),
                            bytes_per_second: Some(rate),
                            remaining_seconds: Some((size - offset) as f64 / rate.max(1.0)),
                            item_index: None,
                            items_total: None,
                            filename: None,
                        });
                    }
                    last_progress = Instant::now();
                }
            }
            if cancel.is_cancelled() {
                return Err(Error::new("cancelled", "Upload cancelled"));
            }
            let receiving_seconds = receive_started.elapsed().as_secs_f64();
            let verification_started = Instant::now();
            if !already_verifying {
                self.request(json!({"method":"finish_upload","transfer_id":id}))?;
            }
            let deadline = Instant::now() + Duration::from_secs(24 * 3600);
            loop {
                if cancel.is_cancelled() {
                    return Err(Error::new("cancelled", "Upload validation cancelled"));
                }
                let mut status =
                    self.request(json!({"method":"upload_status","transfer_id":id}))?;
                let preparation = &status["preparation"];
                let stage = preparation["stage"].as_str().unwrap_or("verifying_upload");
                // Only the SHA pass has a byte denominator. Frame/index/codec
                // checks continue after all bytes have been hashed.
                progress(
                    &sink,
                    stage,
                    preparation["percent"].as_u64().unwrap_or(0),
                    preparation["percent"].as_u64().map(|_| 100),
                );
                match status["state"].as_str() {
                    Some("complete") => {
                        if status["transfer_id"].as_u64() != Some(u64::from(id))
                            || status["size"].as_u64() != Some(size)
                            || status["accepted_offset"].as_u64() != Some(size)
                            || status["checkpointed_offset"].as_u64() != Some(size)
                            || status["verified_bytes"].as_u64() != Some(size)
                            || status["sha256"].as_str() != Some(digest.as_str())
                        {
                            return Err(Error::new("verification_failed", format!("Transfer completion metadata did not match {digest}, {size} bytes: {status}")));
                        }
                        // Publication is not playback readiness. Keep the job
                        // alive through the device's real loading pass.
                        loop {
                            cancel.check()?;
                            let playback = self.request(json!({"method":"get_status"}))?;
                            let preparation = &playback["preparation"];
                            let stage = preparation["stage"]
                                .as_str()
                                .unwrap_or("preparing_playback");
                            progress(
                                &sink,
                                stage,
                                preparation["percent"].as_u64().unwrap_or(0),
                                preparation["percent"].as_u64().map(|_| 100),
                            );
                            if playback["content_sha256"] == digest
                                && ["playing", "paused", "stopped", "ended"]
                                    .contains(&playback["state"].as_str().unwrap_or(""))
                            {
                                status["playback"] = playback;
                                break;
                            }
                            if playback["state"] == "error" {
                                return Err(Error::new(
                                    playback["error"].as_str().unwrap_or("verification_failed"),
                                    "Playback preparation failed",
                                ));
                            }
                            if Instant::now() > deadline {
                                return Err(Error::new("timeout", "Playback preparation"));
                            }
                            thread::sleep(Duration::from_millis(100));
                        }
                        let newly_confirmed_bytes = size - initial_offset;
                        status["host_transfer"] = json!({
                            "resumed_offset":initial_offset,
                            "newly_confirmed_bytes":newly_confirmed_bytes,
                            "receiving_seconds":receiving_seconds,
                            "confirmed_bytes_per_second":if newly_confirmed_bytes > 0 && receiving_seconds > 0.0 { Some(newly_confirmed_bytes as f64 / receiving_seconds) } else { None },
                            "verification_seconds":verification_started.elapsed().as_secs_f64(),
                            "end_to_end_seconds":upload_started.elapsed().as_secs_f64(),
                            "host_diagnostics":self.diagnostics()
                        });
                        return Ok(status);
                    }
                    Some("error") => {
                        return Err(Error::new(
                            status["error"].as_str().unwrap_or("verification_failed"),
                            format!("Device upload validation failed; partial retained: {status}"),
                        ))
                    }
                    Some("verifying") => {}
                    _ => {
                        return Err(Error::new(
                            "incomplete_transfer",
                            format!("Unexpected transfer state during verification: {status}"),
                        ))
                    }
                }
                if Instant::now() > deadline {
                    return Err(Error::new("timeout", "Upload verification"));
                }
                thread::sleep(Duration::from_millis(100));
            }
        })();
        result.map_err(|mut error: Error| {
            // Stop the SD owner on every failed/cancelled active transfer. Preserve
            // the original failure even when a disconnected device cannot reply.
            match self.request(json!({"method":"cancel_upload","transfer_id":id})) {
                Ok(stopped) if error.code == "cancelled" => {
                    // Deliberate user cancellation removes only this transfer's
                    // owned incomplete files. Transport failures retain their
                    // recovery checkpoint so a retry can resume safely.
                    if stopped["state"] == "cancelled"
                        && stopped["transfer_id"].as_u64() == Some(u64::from(id))
                    {
                        if let Err(cleanup) = self.request(json!({"method":"discard_upload","transfer_id":id,"confirmed":true})) {
                            error.details.push_str(&format!("; incomplete-file cleanup unconfirmed: {}: {}. Recovery checkpoint retained", cleanup.code, cleanup.details));
                        }
                    } else {
                        error.details.push_str("; incomplete-file cleanup not attempted: cancellation metadata did not match");
                    }
                }
                Ok(_) => {}
                Err(cleanup) => {
                    error.details.push_str(&format!("; cancellation unconfirmed: {}: {}. Device inactivity timeout retains the recovery checkpoint", cleanup.code, cleanup.details));
                }
            }
            error
        })
    }
}
pub struct PortTransport {
    port: Box<dyn serialport::SerialPort>,
}
impl Read for PortTransport {
    fn read(&mut self, bytes: &mut [u8]) -> std::io::Result<usize> {
        self.port.read(bytes)
    }
}
impl Write for PortTransport {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.port.write(bytes)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        // This adapter has no userspace write buffer. Driver drain operations
        // (tcdrain/FlushFileBuffers) may block beyond configured timeouts.
        // Only a matching, checked device ACK confirms actual delivery.
        Ok(())
    }
}
pub fn open_port(path: &str, baud: u32) -> Result<Client<PortTransport>> {
    // Higher rates are candidates for qualification, not a promise that a USB
    // bridge, driver or firmware build supports them reliably.
    const CANDIDATES: [u32; 5] = [115200, 460800, 921600, 2000000, 3000000];
    if !CANDIDATES.contains(&baud) {
        return Err(Error::new("invalid_parameter", "Unsupported UART baud"));
    }
    let mut port = serialport::new(path, baud)
        // 1024 bytes already take ~92 ms on the wire; a shared 100 ms
        // read/write timeout leaves almost no room for driver scheduling/flush.
        .timeout(Duration::from_millis(250))
        .data_bits(serialport::DataBits::Eight)
        .parity(serialport::Parity::None)
        .stop_bits(serialport::StopBits::One)
        .flow_control(serialport::FlowControl::None)
        .dtr_on_open(false)
        .open()
        .map_err(|e| Error::new("connection_failed", e))?;
    // CYD's USB-UART RTS/DTR lines feed the automatic boot/reset circuitry.
    // Opening with DTR disabled alone leaves a driver's RTS default in effect.
    // Release both lines explicitly before sending application protocol bytes.
    let test_pty = is_linux_pty(path);
    for (name, result) in [
        ("RTS", port.write_request_to_send(false)),
        ("DTR", port.write_data_terminal_ready(false)),
    ] {
        if let Err(error) = result {
            if !test_pty {
                return Err(Error::new(
                    "connection_failed",
                    format!("Could not release USB-UART {name} reset line: {error}"),
                ));
            }
        }
    }
    let mut client = Client::new(PortTransport { port });
    let candidates: &[u32] = if baud == CANDIDATES[0] {
        &CANDIDATES
    } else {
        // An explicit faster rate is intentional, rather than a request to
        // discover other rates or contact other serial devices.
        std::slice::from_ref(&baud)
    };
    let mut last_timeout = None;
    for (index, &candidate) in candidates.iter().enumerate() {
        if index != 0 {
            client.response_timeout = Duration::from_millis(1500);
            client.retries = 2;
            // Keep the same selected handle and released reset lines. Only UART
            // speed changes; no runtime firmware handover or port scan occurs.
            client.io.port.set_baud_rate(candidate).map_err(|e| {
                Error::new(
                    "connection_failed",
                    format!("Could not configure UART {candidate} baud: {e}"),
                )
            })?;
            client
                .io
                .port
                .clear(serialport::ClearBuffer::Input)
                .map_err(|e| {
                    Error::new(
                        "connection_failed",
                        format!("Could not clear input after UART baud change: {e}"),
                    )
                })?;
            client.incoming.clear();
            client.receive_at = 0;
            client.receive_len = 0;
            client.oversized = false;
        }
        client.configured_uart_baud = Some(candidate);
        client.baud_discovery_attempts.push(candidate);
        // The first selected rate retains the previously proven 3 s x 3
        // handshake margin. Shorter fallback deadlines apply only afterward.
        let before = client.counters;
        let invalid_before = client.invalid_frames;
        let started = Instant::now();
        let result = client.hello();
        let last_error = result.as_ref().err().map(|error| json!({
            "code":error.code,
            // Timeout details already append diagnostics. Avoid recursively
            // embedding earlier candidate records inside later errors.
            "details":error.details.split("; host diagnostics ").next().unwrap_or(&error.details)
        }));
        client.baud_discovery_results.push(json!({
            "baud":candidate,
            "elapsed_ms":started.elapsed().as_secs_f64() * 1000.0,
            "ack_timeout_ms":client.response_timeout.as_millis(),
            "max_attempts":client.retries,
            "connected":result.is_ok(),
            "last_error":last_error,
            "diagnostics":{
                "scope":"this_baud_candidate",
                "read_calls":client.counters.read_calls-before.read_calls,
                "received_bytes":client.counters.received_bytes-before.received_bytes,
                "write_calls":client.counters.write_calls-before.write_calls,
                "written_bytes":client.counters.written_bytes-before.written_bytes,
                "retry_attempts":client.counters.retry_attempts-before.retry_attempts,
                "response_timeouts":client.counters.response_timeouts-before.response_timeouts,
                "invalid_wire_frames":client.invalid_frames-invalid_before,
                "buffered_receive_bytes":client.receive_len-client.receive_at
            }
        }));
        match result {
            Ok(_) => {
                // A repeated caller HELLO remains valid: the nonce and selected
                // port are retained, and the next request gets a new sequence.
                client.response_timeout = Duration::from_secs(3);
                client.retries = 3;
                return Ok(client);
            }
            Err(error)
                if error.code == "timeout"
                    && client.counters.response_timeouts - before.response_timeouts
                        == u64::from(client.retries) =>
            {
                // Wrong-baud garbage is resynchronized and counted. Only a
                // completed no-reply timeout permits discovery to continue;
                // write failures and valid device/protocol errors propagate.
                last_timeout = Some(error);
            }
            Err(mut error) => {
                error.details.push_str(&format!(
                    "; baud discovery diagnostics {}",
                    client.diagnostics()
                ));
                return Err(error);
            }
        }
    }
    let mut error = last_timeout.expect("Every supported baud candidate was attempted");
    let summary = error
        .details
        .split("; host diagnostics ")
        .next()
        .unwrap_or(&error.details);
    error.details = format!("HELLO discovery received no matching reply on selected port {path}, baud candidates {:?}; {summary}; host diagnostics {}", client.baud_discovery_attempts, client.diagnostics());
    Err(error)
}
fn is_linux_pty(path: &str) -> bool {
    #[cfg(target_os = "linux")]
    {
        // Native transport integration uses actual devpts endpoints. Those have
        // no modem-control lines; this exemption never covers a USB UART node.
        std::fs::canonicalize(path).is_ok_and(|canonical| {
            canonical.parent() == Some(Path::new("/dev/pts"))
                && canonical
                    .file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.parse::<u32>().is_ok())
        })
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = path;
        false
    }
}
pub fn port_names() -> Result<Vec<String>> {
    Ok(serialport::available_ports()
        .map_err(|e| Error::new("connection_failed", e))?
        .into_iter()
        .map(|p| p.port_name)
        .collect())
}

/// Explicit host simulator transport. This never opens an ESP32 serial port.
pub struct Simulator {
    child: Child,
    stdin: ChildStdin,
    receiver: Option<Receiver<std::io::Result<Vec<u8>>>>,
    pending: VecDeque<u8>,
    reader: Option<JoinHandle<()>>,
}
impl Simulator {
    pub fn spawn(executable: &Path, sd: &Path) -> Result<Self> {
        let mut child = Command::new(executable)
            .arg(sd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| Error::new("simulation_failed", "No stdin"))?;
        let mut stdout = child
            .stdout
            .take()
            .ok_or_else(|| Error::new("simulation_failed", "No stdout"))?;
        let (tx, rx) = mpsc::sync_channel(16);
        let reader = thread::spawn(move || {
            let mut b = [0; 4096];
            loop {
                match stdout.read(&mut b) {
                    Ok(0) => {
                        let _ = tx.send(Ok(Vec::new()));
                        break;
                    }
                    Ok(n) => {
                        if tx.send(Ok(b[..n].to_vec())).is_err() {
                            break;
                        }
                    }
                    Err(e) => {
                        let _ = tx.send(Err(e));
                        break;
                    }
                }
            }
        });
        Ok(Self {
            child,
            stdin,
            receiver: Some(rx),
            pending: VecDeque::new(),
            reader: Some(reader),
        })
    }
}
impl Read for Simulator {
    fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
        if b.is_empty() {
            return Ok(0);
        }
        if self.pending.is_empty() {
            let receiver = self
                .receiver
                .as_ref()
                .ok_or_else(|| std::io::Error::other("Closed simulator"))?;
            match receiver.recv_timeout(Duration::from_millis(100)) {
                Ok(Ok(data)) if data.is_empty() => return Ok(0),
                Ok(Ok(data)) => self.pending.extend(data),
                Ok(Err(e)) => return Err(e),
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    return Err(std::io::Error::from(std::io::ErrorKind::TimedOut))
                }
                Err(_) => return Ok(0),
            }
        }
        let n = b.len().min(self.pending.len());
        for x in &mut b[..n] {
            *x = self.pending.pop_front().unwrap();
        }
        Ok(n)
    }
}
impl Write for Simulator {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
        self.stdin.write(b)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.stdin.flush()
    }
}
impl Drop for Simulator {
    fn drop(&mut self) {
        self.receiver.take();
        let _ = self.child.kill();
        let _ = self.child.wait();
        if let Some(h) = self.reader.take() {
            let _ = h.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fragmented {
        input: Vec<u8>,
        reply: VecDeque<u8>,
        pause_write: bool,
        pause_flush: bool,
        completed: usize,
        reject: bool,
    }
    impl Read for Fragmented {
        fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
            if let Some(value) = self.reply.pop_front() {
                b[0] = value;
                Ok(1)
            } else {
                Err(std::io::ErrorKind::TimedOut.into())
            }
        }
    }
    impl Write for Fragmented {
        fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
            self.pause_write = !self.pause_write;
            if self.pause_write {
                return Err(std::io::ErrorKind::TimedOut.into());
            }
            let n = b.len().min(7);
            for &value in &b[..n] {
                if value != 0 {
                    self.input.push(value);
                } else if !self.input.is_empty() {
                    let request = Frame::decode(&self.input).unwrap();
                    self.input.clear();
                    self.completed += 1;
                    self.reply.extend(Frame::json(2, if self.reject { 99 } else { 42 }, request.sequence,
                        &if self.reject { json!({"ok":false,"error":{"code":"stale_session","details":"Device restarted"}}) }
                        else { json!({"ok":true,"result":{"received":request.payload.len()}}) }).unwrap().encode().unwrap());
                }
            }
            Ok(n)
        }
        fn flush(&mut self) -> std::io::Result<()> {
            if self.pause_flush {
                self.pause_flush = false;
                Err(std::io::ErrorKind::TimedOut.into())
            } else {
                Ok(())
            }
        }
    }
    fn fragmented(reject: bool) -> Fragmented {
        Fragmented {
            input: Vec::new(),
            reply: VecDeque::new(),
            pause_write: false,
            pause_flush: true,
            completed: 0,
            reject,
        }
    }
    #[test]
    fn partial_writes_and_temporary_timeouts_do_not_restart_a_frame() {
        let mut c = Client::new(fragmented(false));
        c.session = 42;
        let result = c
            .request(json!({"method":"ping","padding":"x".repeat(1024)}))
            .unwrap();
        assert!(result["received"].as_u64().unwrap() > 1024);
        assert_eq!(c.diagnostics()["transport"]["retry_attempts"], 0);
        assert!(
            c.diagnostics()["transport"]["write_calls"]
                .as_u64()
                .unwrap()
                > 1
        );
        assert_eq!(c.into_transport().completed, 1);
    }
    #[test]
    fn chunk_exchange_preserves_json_device_rejection() {
        let mut c = Client::new(fragmented(true));
        c.session = 42;
        let error = c
            .exchange(
                Frame {
                    kind: 16,
                    session: 42,
                    sequence: 1,
                    payload: vec![1; 32],
                },
                17,
            )
            .unwrap_err();
        assert_eq!(error.code, "stale_session");
        assert!(error.details.contains("Device restarted"));
        assert_eq!(c.into_transport().completed, 1);
    }
    struct Waiting;
    impl Read for Waiting {
        fn read(&mut self, _: &mut [u8]) -> std::io::Result<usize> {
            thread::sleep(Duration::from_millis(10));
            Err(std::io::ErrorKind::TimedOut.into())
        }
    }
    impl Write for Waiting {
        fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
            Ok(b.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    #[test]
    fn cancellation_interrupts_ack_wait_instead_of_waiting_for_all_retries() {
        let token = Cancellation::default();
        let cancel = token.clone();
        let worker = thread::spawn(move || {
            thread::sleep(Duration::from_millis(50));
            cancel.cancel();
        });
        let mut c = Client::new(Waiting);
        c.response_timeout = Duration::from_secs(30);
        let start = Instant::now();
        let error = c
            .exchange_cancellable(
                Frame {
                    kind: 16,
                    session: 42,
                    sequence: 1,
                    payload: vec![1; 32],
                },
                17,
                Some(&token),
            )
            .unwrap_err();
        worker.join().unwrap();
        assert_eq!(error.code, "cancelled");
        assert!(start.elapsed() < Duration::from_secs(1));
    }

    struct Burst {
        bytes: VecDeque<u8>,
        fragment: usize,
        reads: usize,
    }
    impl Read for Burst {
        fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
            self.reads += 1;
            if self.bytes.is_empty() {
                return Err(std::io::ErrorKind::TimedOut.into());
            }
            let n = b.len().min(self.fragment).min(self.bytes.len());
            for value in &mut b[..n] {
                *value = self.bytes.pop_front().unwrap();
            }
            Ok(n)
        }
    }
    impl Write for Burst {
        fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
            Ok(b.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    #[test]
    fn receive_preserves_split_coalesced_frames_and_resynchronizes_after_corruption() {
        let frames = [
            Frame::json(3, 42, 0, &json!({"event":"frame","frame_index":4})).unwrap(),
            Frame::json(
                2,
                42,
                1,
                &json!({"ok":true,"result":{"padding":"x".repeat(1700)}}),
            )
            .unwrap(),
            Frame::json(3, 42, 0, &json!({"event":"frame","frame_index":5})).unwrap(),
        ];
        let mut wire = vec![0];
        wire.extend(frames[0].encode().unwrap());
        wire.extend([1, 2, 3, 0]);
        wire.extend(vec![1; MAX_WIRE + 20]);
        wire.push(0);
        wire.extend(frames[1].encode().unwrap());
        wire.extend(frames[2].encode().unwrap());
        for fragment in [1, 2, 7, 64, 512, 2048] {
            let mut client = Client::new(Burst {
                bytes: wire.clone().into(),
                fragment,
                reads: 0,
            });
            let deadline = Instant::now() + Duration::from_secs(1);
            for expected in &frames {
                loop {
                    assert!(Instant::now() < deadline, "fragment {fragment}");
                    if let Some(frame) = client.read_frame().unwrap() {
                        assert_eq!(frame.kind, expected.kind);
                        assert_eq!(frame.sequence, expected.sequence);
                        assert_eq!(frame.payload, expected.payload);
                        break;
                    }
                }
            }
            assert_eq!(client.invalid_frames, 2);
        }
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn host_pipe_receive_measurement() {
        use std::os::fd::FromRawFd;
        struct Pipe {
            reader: File,
            writer: File,
            input: Vec<u8>,
            reads: usize,
            wire_bytes: usize,
        }
        impl Read for Pipe {
            fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
                self.reads += 1;
                self.reader.read(b)
            }
        }
        impl Write for Pipe {
            fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
                for &byte in b {
                    if byte != 0 {
                        self.input.push(byte);
                    } else if !self.input.is_empty() {
                        let request = Frame::decode(&self.input).unwrap();
                        self.input.clear();
                        let reply = Frame::json(
                            2,
                            42,
                            request.sequence,
                            &json!({"ok":true,"result":{"padding":"x".repeat(1700)}}),
                        )
                        .unwrap()
                        .encode()
                        .unwrap();
                        self.wire_bytes += reply.len();
                        self.writer.write_all(&reply)?;
                    }
                }
                Ok(b.len())
            }
            fn flush(&mut self) -> std::io::Result<()> {
                Ok(())
            }
        }
        let mut fds = [0; 2];
        // SAFETY: pipe initializes two owned descriptors in a writable array.
        assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0);
        // SAFETY: these descriptors are new, valid and owned exactly once.
        let (reader, writer) = unsafe { (File::from_raw_fd(fds[0]), File::from_raw_fd(fds[1])) };
        let mut client = Client::new(Pipe {
            reader,
            writer,
            input: Vec::new(),
            reads: 0,
            wire_bytes: 0,
        });
        client.session = 42;
        let start = Instant::now();
        for _ in 0..200 {
            assert_eq!(
                client.request(json!({"method":"get_telemetry"})).unwrap()["padding"]
                    .as_str()
                    .unwrap()
                    .len(),
                1700
            );
        }
        let elapsed = start.elapsed();
        let io = client.into_transport();
        println!("HOST PIPE ONLY: 200 replies, {} wire bytes, {} read syscalls, {:.3} ms; no UART/ESP32 measurement", io.wire_bytes, io.reads, elapsed.as_secs_f64() * 1000.0);
        assert!(
            io.reads <= 800,
            "Receive path should amortize host system calls; got {}",
            io.reads
        );
    }
}
