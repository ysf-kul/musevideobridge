use muse_video_core::{
    container::{Rate, Video},
    media::{self, Fit, Options, Scaling, Tools},
    serial::{self, Client, Simulator},
    storage, Cancellation, Error, Result,
};
use serde_json::{json, Value};
use std::{
    env,
    fs::{File, OpenOptions},
    io::{Read, Write},
    path::PathBuf,
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

fn argument(args: &[String], flag: &str) -> Option<String> {
    args.iter()
        .position(|s| s == flag)
        .and_then(|i| args.get(i + 1))
        .cloned()
}
fn path(args: &[String], index: usize) -> Result<PathBuf> {
    args.get(index)
        .map(PathBuf::from)
        .ok_or_else(|| Error::new("invalid_parameter", "Missing path argument"))
}
fn rate(s: &str) -> Result<Rate> {
    let (n, d) = s.split_once('/').unwrap_or((s, "1"));
    Rate::new(
        n.parse()
            .map_err(|_| Error::new("invalid_parameter", "FPS numerator"))?,
        d.parse()
            .map_err(|_| Error::new("invalid_parameter", "FPS denominator"))?,
    )
}
fn device<T: Read + Write>(
    mut client: Client<T>,
    args: &[String],
    cancel: &Cancellation,
    sink: media::ProgressSink,
) -> Result<Value> {
    let hello = client.hello()?;
    let uploaded = if let Some(file) = argument(args, "--upload") {
        Some(client.upload(&PathBuf::from(file), cancel, sink)?)
    } else {
        None
    };
    if let Some(seconds) = argument(args, "--benchmark-seconds") {
        let seconds: u64 = seconds
            .parse()
            .map_err(|_| Error::new("invalid_parameter", "Benchmark seconds"))?;
        if !(2..=600).contains(&seconds) {
            return Err(Error::new(
                "invalid_parameter",
                "Benchmark duration must be 2..600 seconds",
            ));
        }
        let deadline = Instant::now() + Duration::from_secs(600);
        loop {
            cancel.check()?;
            let status = client.request(json!({"method":"get_status"}))?;
            if status["error"].is_string() {
                return Err(Error::new(status["error"].as_str().unwrap(), &status));
            }
            if ["playing", "paused", "stopped", "ended"]
                .contains(&status["state"].as_str().unwrap_or(""))
            {
                break;
            }
            if Instant::now() > deadline {
                return Err(Error::new("timeout", "Waiting for uploaded video playback"));
            }
            thread::sleep(Duration::from_millis(100));
        }
        let target = rate(&argument(args, "--target-fps").unwrap_or_else(|| "30".into()))?;
        client.request(json!({"method":"set_fps","numerator":target.numerator,"denominator":target.denominator}))?;
        let mcus: u32 = argument(args, "--decoder-mcus")
            .unwrap_or_else(|| "32".into())
            .parse()
            .map_err(|_| Error::new("invalid_parameter", "Decoder MCUs"))?;
        let decoder = argument(args, "--decoder").unwrap_or_else(|| "jpegdec".into());
        if !["jpegdec", "rom", "tjpgd_fast", "new_jpeg"].contains(&decoder.as_str()) {
            return Err(Error::new("invalid_parameter", "Unknown JPEG decoder"));
        }
        let applied_decoder = client.request(json!({"method":"set_decoder_options","max_mcus":mcus,"native_blocks":!args.iter().any(|arg|arg=="--row-render"),"decoder":decoder}))?;
        if decoder != "jpegdec" && applied_decoder["decoder"] != decoder {
            return Err(Error::new(
                "unsupported_configuration",
                "Requested decoder was not selected",
            ));
        }
        if let Some(mode) = argument(args, "--render-mode") {
            let spi: u32 = argument(args, "--tft-spi-hz")
                .unwrap_or_else(|| "20000000".into())
                .parse()
                .map_err(|_| Error::new("invalid_parameter", "TFT SPI clock"))?;
            let command_spi: u32 = argument(args, "--command-spi-hz")
                .unwrap_or_else(|| spi.min(40000000).to_string())
                .parse()
                .map_err(|_| Error::new("invalid_parameter", "TFT command SPI clock"))?;
            client.request(json!({"method":"set_render_options","mode":mode,
                "big_endian":args.iter().any(|arg|arg=="--big-endian"),"spi_hz":spi,"command_spi_hz":command_spi}))?;
        }
        let native = args.iter().any(|arg| arg == "--display-native");
        if args.iter().any(|arg| arg == "--display-full") || native {
            let file = argument(args, "--upload").ok_or_else(|| {
                Error::new(
                    "invalid_parameter",
                    "--display-full requires --upload to determine encoded aspect",
                )
            })?;
            let video = Video::open(File::open(file)?, cancel)?;
            let h = video.header;
            let (width, height) = if native {
                (u32::from(h.width), u32::from(h.height))
            } else if u32::from(h.width) * 240 >= u32::from(h.height) * 320 {
                (320, (320 * u32::from(h.height) / u32::from(h.width)).max(1))
            } else {
                ((240 * u32::from(h.width) / u32::from(h.height)).max(1), 240)
            };
            client.request(json!({"method":"set_transform","x":(320-width)/2,"y":(240-height)/2,"width":width,"height":height,"rotation_deg":0,"lock_aspect":false}))?;
        }
        if let Some(prefetch) = argument(args, "--prefetch") {
            let enabled = match prefetch.as_str() {
                "on" => true,
                "off" => false,
                _ => return Err(Error::new("invalid_parameter", "Prefetch on/off")),
            };
            let applied =
                client.request(json!({"method":"set_prefetch_options","enabled":enabled}))?;
            if applied["enabled"] != enabled {
                return Err(Error::new(
                    "unsupported_configuration",
                    "Prefetch selection was not confirmed",
                ));
            }
        }
        client.request(json!({"method":"restart"}))?;
        let started = Instant::now();
        let outcome = (|| {
            let mut samples = Vec::new();
            while started.elapsed() < Duration::from_secs(seconds) {
                cancel.check()?;
                let status = client.request(json!({"method":"get_status"}))?;
                if status["error"].is_string() {
                    return Err(Error::new(status["error"].as_str().unwrap(), &status));
                }
                let ended = status["state"] == "ended";
                samples.push(status);
                if ended {
                    break;
                }
                thread::sleep(Duration::from_millis(200));
            }
            let measured = client.request(json!({"method":"get_benchmark"}))?;
            let pipeline = optional_diagnostic(&mut client, "get_pipeline_profile")?;
            let display = optional_diagnostic(&mut client, "get_display_info")?;
            let hardware = client.request(json!({"method":"get_telemetry"}))?;
            let serial = client.request(json!({"method":"get_serial_diagnostics"}))?;
            Ok(
                json!({"hello":hello,"upload":uploaded,"benchmark":measured,"pipeline_profile":pipeline,"display_info":display,"status_samples":samples,"telemetry":hardware,"serial_diagnostics":serial,"host_diagnostics":client.diagnostics()}),
            )
        })();
        let paused = client.request(json!({"method":"pause"}));
        return match outcome {
            Ok(result) => {
                paused?;
                Ok(result)
            }
            Err(error) => Err(error),
        };
    }
    if let Some(uploaded) = uploaded {
        return Ok(uploaded);
    }
    let requested =
        argument(args, "--request").unwrap_or_else(|| "{\"method\":\"get_status\"}".into());
    let commands: Value = serde_json::from_str(&requested)?;
    let commands = if let Some(a) = commands.as_array() {
        a.clone()
    } else {
        vec![commands]
    };
    let mut replies = Vec::new();
    for command in commands {
        cancel.check()?;
        replies.push(client.request(command)?);
    }
    Ok(json!({"hello":hello,"responses":replies,"events":client.events}))
}
fn optional_diagnostic<T: Read + Write>(client: &mut Client<T>, method: &str) -> Result<Value> {
    match client.request(json!({"method":method})) {
        Ok(value) => Ok(value),
        Err(error) if error.code == "unsupported_command" => Ok(Value::Null),
        Err(error) => Err(error),
    }
}
fn run(args: &[String], cancel: &Cancellation, sink: media::ProgressSink) -> Result<Value> {
    let tools = Tools {
        ffmpeg: argument(args, "--ffmpeg")
            .unwrap_or_else(|| "ffmpeg".into())
            .into(),
        ffprobe: argument(args, "--ffprobe")
            .unwrap_or_else(|| "ffprobe".into())
            .into(),
    };
    match args.first().map(String::as_str) {
        Some("probe")=>{let m=media::probe(&path(args,1)?,&tools,cancel)?;Ok(json!({"metadata":m,"estimate":media::estimate(&m,&Options::default())?}))},
        Some("convert")=>{
            let fps=match argument(args,"--fps") {Some(value)=>rate(&value)?,None=>Options::default().fps};
            let qscale=argument(args,"--quality").unwrap_or_else(||"8".into()).parse().map_err(|_|Error::new("invalid_parameter","JPEG quality"))?;
            let width=argument(args,"--width").unwrap_or_else(||"320".into()).parse().map_err(|_|Error::new("invalid_parameter","Width"))?;
            let height=argument(args,"--height").unwrap_or_else(||"240".into()).parse().map_err(|_|Error::new("invalid_parameter","Height"))?;
            let scaling=match argument(args,"--scaling").as_deref().unwrap_or("bicubic") {"bicubic"=>Scaling::Bicubic,"area"=>Scaling::Area,"lanczos"=>Scaling::Lanczos,_=>return Err(Error::new("invalid_parameter","Scaling must be bicubic, area or lanczos"))};
            let o=Options {fps,qscale,fit:if args.iter().any(|x|x=="--crop") {Fit::Crop} else {Fit::Contain},width,height,scaling};
            Ok(serde_json::to_value(media::convert(&path(args,1)?,&path(args,2)?,&o,&tools,cancel,sink)?)?)
        },
        Some("verify")=>{let p=path(args,1)?;let h=media::verify_decoding(&p,&tools,cancel,sink)?;Ok(json!({"header":h,"sha256":storage::digest_file(&p,cancel)?}))},
        Some("frame")=>{let mut v=Video::open(File::open(path(args,1)?)?,cancel)?;let i=args.get(2).and_then(|s|s.parse().ok()).ok_or_else(||Error::new("invalid_parameter","Frame index"))?;
            let b=v.frame(i)?;let mut f=OpenOptions::new().write(true).create_new(true).open(path(args,3)?)?;f.write_all(&b)?;f.sync_all()?;Ok(json!({"frame":i,"bytes":b.len()}))},
        Some("destination")=>Ok(serde_json::to_value(storage::inspect(&path(args,1)?)?)?),
        Some("export")=>Ok(serde_json::to_value(storage::export(&path(args,1)?,&path(args,2)?,args.iter().any(|s|s=="--test-directory"),cancel,sink)?)?),
        Some("ports")=>Ok(json!(serialport_names()?)),
        Some("device")=>{
            if let Some(sim)=argument(args,"--simulator") {let sd=argument(args,"--sd").ok_or_else(||Error::new("invalid_parameter","Simulator needs --sd test directory"))?;device(Client::new(Simulator::spawn(&PathBuf::from(sim),&PathBuf::from(sd))?),args,cancel,sink)}
            else {let port=argument(args,"--port").ok_or_else(||Error::new("invalid_parameter","Select --port or explicit --simulator"))?;let baud=argument(args,"--baud").unwrap_or_else(||"115200".into()).parse().map_err(|_|Error::new("invalid_parameter","Baud"))?;device(serial::open_port(&port,baud)?,args,cancel,sink)}
        },
        _=>Err(Error::new("invalid_parameter","Commands: probe, convert, verify, frame, destination, export, ports, device. See README.")),
    }
}
fn serialport_names() -> Result<Vec<String>> {
    serial::port_names()
}
fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let cancel = Cancellation::default();
    let token = cancel.clone();
    if let Err(e) = ctrlc::set_handler(move || token.cancel()) {
        eprintln!("{e}");
        std::process::exit(1);
    }
    let (tx, rx) = mpsc::sync_channel(16);
    let cancel_bytes = argument(&args, "--cancel-after-bytes").and_then(|s| s.parse::<u64>().ok());
    let progress_cancel = cancel.clone();
    let progress = thread::spawn(move || {
        for event in rx {
            let event: media::Progress = event;
            if event.stage == "uploading"
                && cancel_bytes.is_some_and(|limit| event.completed >= limit)
            {
                progress_cancel.cancel();
            }
            if let Ok(text) = serde_json::to_string(&event) {
                eprintln!("{text}");
            }
        }
    });
    let result = run(&args, &cancel, Some(tx));
    let _ = progress.join();
    match result {
        Ok(value) => println!(
            "{}",
            serde_json::to_string_pretty(&value).unwrap_or_default()
        ),
        Err(error) => {
            let locale = argument(&args, "--lang").unwrap_or_else(|| "tr".into());
            let locale = if locale == "en" {
                muse_video_core::localization::Locale::English
            } else {
                muse_video_core::localization::Locale::Turkish
            };
            let message = muse_video_core::localization::error_message(locale, &error.code);
            println!("{}", json!({"ok":false,"error":error,"message":message}));
            std::process::exit(1);
        }
    }
}
