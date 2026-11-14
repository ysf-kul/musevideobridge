//! Explicit device research utility. Keeps one serial client open for repeated matrices;
//! never flashes, formats, discards partials or presents host timing as device FPS.
use muse_video_core::{
    container::Video,
    serial::{self, Client},
    storage, Cancellation, Error, Result,
};
use serde_json::{json, Value};
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

fn argument(args: &[String], flag: &str) -> Option<String> {
    args.iter()
        .position(|a| a == flag)
        .and_then(|i| args.get(i + 1))
        .cloned()
}
fn emit(file: &mut Option<File>, value: &Value) -> Result<()> {
    let line = serde_json::to_string(value)?;
    println!("{line}");
    if let Some(file) = file {
        writeln!(file, "{line}")?;
        file.sync_data()?;
    }
    Ok(())
}
fn wait_video<T: Read + Write>(client: &mut Client<T>, digest: &str) -> Result<Value> {
    let deadline = Instant::now() + Duration::from_secs(24 * 3600);
    loop {
        let status = client.request(json!({"method":"get_status"}))?;
        if status["error"].is_string() {
            return Err(Error::new(status["error"].as_str().unwrap(), &status));
        }
        if status["content_sha256"] == digest
            && ["playing", "paused", "stopped", "ended"]
                .contains(&status["state"].as_str().unwrap_or(""))
        {
            return Ok(status);
        }
        if Instant::now() >= deadline {
            return Err(Error::new("timeout", "Waiting for verified selected video"));
        }
        thread::sleep(Duration::from_millis(500));
    }
}
// Each argument is an independent explicitly selected research parameter.
#[allow(clippy::too_many_arguments)]
fn bench<T: Read + Write>(
    client: &mut Client<T>,
    configuration: &Value,
    mcus: u32,
    seconds: u64,
    target: u32,
    decoder: &str,
    verify_every_frames: u32,
    prefetch: Option<bool>,
    decoder_warmup: Option<bool>,
    decoder_warmup_blocks: u32,
) -> Result<Value> {
    client.request(json!({"method":"pause"}))?;
    if configuration["zero_copy"] == true {
        // Release the bounded decoder/prefetch allocations before acquiring
        // the third display strip. Steady-state work is measured after setup.
        client.request(json!({"method":"set_prefetch_options","enabled":false}))?;
        client.request(json!({"method":"set_decoder_options","decoder":"jpegdec","max_mcus":mcus,"native_blocks":true}))?;
    }
    let applied = client.request(json!({"method":"set_render_options",
        "mode":configuration["mode"],"big_endian":configuration["big_endian"],"spi_hz":configuration["spi_hz"],
        "command_spi_hz":configuration["command_spi_hz"],"verify_every_frames":verify_every_frames,
        "zero_copy":configuration["zero_copy"]}))?;
    let decoded = client.request(json!({"method":"set_decoder_options","max_mcus":mcus,"native_blocks":true,"decoder":decoder}))?;
    if decoder != "jpegdec" && decoded["decoder"] != decoder {
        return Err(Error::new(
            "unsupported_configuration",
            "Requested decoder was not selected by firmware",
        ));
    }
    let applied_prefetch = if let Some(enabled) = prefetch {
        let applied = client.request(json!({"method":"set_prefetch_options","enabled":enabled}))?;
        if applied["enabled"] != enabled {
            return Err(Error::new(
                "unsupported_configuration",
                "Prefetch selection was not confirmed",
            ));
        }
        applied
    } else {
        Value::Null
    };
    let applied_pipeline = if let Some(warmup) = decoder_warmup {
        let applied = client.request(
            json!({"method":"set_decoder_pipeline","warmup":warmup,"blocks":decoder_warmup_blocks}),
        )?;
        if applied["selected"] != warmup {
            return Err(Error::new(
                "unsupported_configuration",
                "Decoder warmup selection was not confirmed",
            ));
        }
        applied
    } else {
        Value::Null
    };
    let applied_loop = if let Some(interval) = configuration["idle_yield_us"].as_u64() {
        let applied =
            client.request(json!({"method":"set_loop_options","idle_yield_us":interval}))?;
        if applied["idle_yield_us"].as_u64() != Some(interval) {
            return Err(Error::new(
                "unsupported_configuration",
                "Loop interval was not confirmed",
            ));
        }
        applied
    } else {
        Value::Null
    };
    let applied_startup = if let Some(enabled) = configuration["preload_first_frame"].as_bool() {
        let applied = client
            .request(json!({"method":"set_startup_options","preload_first_frame":enabled}))?;
        if applied["preload_first_frame"] != enabled {
            return Err(Error::new(
                "unsupported_configuration",
                "Startup preparation selection was not confirmed",
            ));
        }
        applied
    } else {
        Value::Null
    };
    client.request(json!({"method":"set_fps","numerator":target,"denominator":1}))?;
    let loop_before = if applied_loop.is_null() {
        Value::Null
    } else {
        client.request(json!({"method":"get_loop_diagnostics"}))?
    };
    let before = client.diagnostics();
    client.request(json!({"method":"restart"}))?;
    let started = Instant::now();
    let mut last_status;
    loop {
        // Match the desktop's normal status cadence; diagnostic polling must
        // not saturate the 115200-baud link and distort the playback run.
        thread::sleep(Duration::from_secs(1));
        last_status = client.request(json!({"method":"get_status"}))?;
        if last_status["state"] == "error" {
            return Err(Error::new(
                last_status["error"].as_str().unwrap_or("device_error"),
                &last_status,
            ));
        }
        if last_status["state"] == "ended" || started.elapsed() >= Duration::from_secs(seconds) {
            break;
        }
    }
    if last_status["state"] != "ended" {
        client.request(json!({"method":"pause"}))?;
    }
    let loop_after = if applied_loop.is_null() {
        Value::Null
    } else {
        client.request(json!({"method":"get_loop_diagnostics"}))?
    };
    let benchmark = client.request(json!({"method":"get_benchmark"}))?;
    let startup_profile = if applied_startup.is_null() {
        Value::Null
    } else {
        client.request(json!({"method":"get_startup_profile"}))?
    };
    let pipeline = client.request(json!({"method":"get_pipeline_profile"}))?;
    let display = client.request(json!({"method":"get_display_info"}))?;
    let telemetry = client.request(json!({"method":"get_telemetry"}))?;
    let serial = client.request(json!({"method":"get_serial_diagnostics"}))?;
    Ok(
        json!({"event":"playback_run","configuration":configuration,"max_mcus":mcus,
        "applied_options":applied,"applied_decoder_options":decoded,"applied_prefetch":applied_prefetch,"applied_decoder_pipeline":applied_pipeline,"applied_loop_options":applied_loop,"loop_before":loop_before,"loop_after":loop_after,"applied_startup_options":applied_startup,"startup_profile":startup_profile,"requested_decoder":decoder,"requested_fps":{"numerator":target,"denominator":1},
        "encoded_fps":last_status["encoded_fps"],"benchmark":benchmark,
        "pipeline":pipeline,"display":display,"telemetry":telemetry,"serial":serial,
        "last_status":last_status,"host_diagnostics_before":before,"host_diagnostics_after":client.diagnostics()}),
    )
}
fn parse_matrix(path: Option<String>) -> Result<Vec<Value>> {
    let values = if let Some(path) = path {
        serde_json::from_slice::<Vec<Value>>(&fs::read(path)?)?
    } else {
        vec![
            json!({"mode":"legacy","spi_hz":20000000,"big_endian":false}),
            json!({"mode":"blocks","spi_hz":20000000,"big_endian":false}),
            json!({"mode":"blocks","spi_hz":20000000,"big_endian":true}),
            json!({"mode":"blocks","spi_hz":40000000,"big_endian":true}),
            json!({"mode":"blocks","spi_hz":80000000,"big_endian":true}),
            json!({"mode":"dma","spi_hz":40000000,"big_endian":true}),
            json!({"mode":"dma","spi_hz":80000000,"big_endian":true}),
        ]
    };
    if values.is_empty()
        || values.len() > 64
        || values.iter().any(|v| {
            !["legacy", "blocks", "dma"].contains(&v["mode"].as_str().unwrap_or(""))
                || !v["big_endian"].is_boolean()
                || (!v["zero_copy"].is_null() && !v["zero_copy"].is_boolean())
                || (!v["preload_first_frame"].is_null() && !v["preload_first_frame"].is_boolean())
                || ![20000000, 40000000, 80000000].contains(&v["spi_hz"].as_u64().unwrap_or(0))
                || (!v["command_spi_hz"].is_null()
                    && ![10000000, 20000000, 40000000, 80000000]
                        .contains(&v["command_spi_hz"].as_u64().unwrap_or(0)))
                || (!v["idle_yield_us"].is_null()
                    && ![50000, 250000, 1000000]
                        .contains(&v["idle_yield_us"].as_u64().unwrap_or(0)))
        })
    {
        return Err(Error::new(
            "invalid_parameter",
            "Matrix requires 1..64 mode/clock/endian configurations",
        ));
    }
    Ok(values)
}
fn run() -> Result<()> {
    let args: Vec<String> = env::args().skip(1).collect();
    let port = argument(&args, "--port")
        .ok_or_else(|| Error::new("invalid_parameter", "Specify --port"))?;
    let path = PathBuf::from(
        argument(&args, "--video")
            .ok_or_else(|| Error::new("invalid_parameter", "Specify prepared --video"))?,
    );
    let seconds: u64 = argument(&args, "--seconds")
        .unwrap_or_else(|| "5".into())
        .parse()
        .map_err(|_| Error::new("invalid_parameter", "Seconds"))?;
    let target: u32 = argument(&args, "--target-fps")
        .unwrap_or_else(|| "30".into())
        .parse()
        .map_err(|_| Error::new("invalid_parameter", "Target FPS"))?;
    let requested_repeat: u32 = argument(&args, "--repeat")
        .unwrap_or_else(|| "1".into())
        .parse()
        .map_err(|_| Error::new("invalid_parameter", "Repeat count 1..1000"))?;
    if !(1..=1000).contains(&requested_repeat)
        || (args.iter().any(|arg| arg == "--repeat") && argument(&args, "--repeat").is_none())
    {
        return Err(Error::new("invalid_parameter", "Repeat count 1..1000"));
    }
    let decoder = argument(&args, "--decoder").unwrap_or_else(|| "jpegdec".into());
    let prefetch = argument(&args, "--prefetch")
        .map(|value| match value.as_str() {
            "on" => Ok(true),
            "off" => Ok(false),
            _ => Err(Error::new("invalid_parameter", "Prefetch on/off")),
        })
        .transpose()?;
    let decoder_warmup = argument(&args, "--decoder-warmup")
        .map(|value| match value.as_str() {
            "on" => Ok(true),
            "off" => Ok(false),
            _ => Err(Error::new("invalid_parameter", "Decoder warmup on/off")),
        })
        .transpose()?;
    let decoder_warmup_blocks: u32 = argument(&args, "--decoder-warmup-blocks")
        .unwrap_or_else(|| "1".into())
        .parse()
        .map_err(|_| Error::new("invalid_parameter", "Decoder warmup blocks"))?;
    if !(1..=2).contains(&decoder_warmup_blocks)
        || (decoder_warmup_blocks == 2 && decoder_warmup != Some(true))
    {
        return Err(Error::new(
            "invalid_parameter",
            "Warmup blocks 1..2; two blocks require --decoder-warmup on",
        ));
    }
    if !["jpegdec", "rom", "tjpgd_fast", "new_jpeg"].contains(&decoder.as_str()) {
        return Err(Error::new(
            "invalid_parameter",
            "Decoder jpegdec, rom, tjpgd_fast or new_jpeg",
        ));
    }
    let verify_every_frames: u32 = argument(&args, "--verify-every-frames")
        .unwrap_or_else(|| "15".into())
        .parse()
        .map_err(|_| Error::new("invalid_parameter", "Verification interval"))?;
    if verify_every_frames > 300 {
        return Err(Error::new(
            "invalid_parameter",
            "Verification interval 0..300",
        ));
    }
    if !(2..=600).contains(&seconds) || !(1..=30).contains(&target) {
        return Err(Error::new(
            "invalid_parameter",
            "Seconds 2..600 and target FPS 1..30",
        ));
    }
    let mcus: Vec<u32> = argument(&args, "--mcus")
        .unwrap_or_else(|| "1,4,8,32".into())
        .split(',')
        .map(|n| {
            n.parse::<u32>()
                .map_err(|_| Error::new("invalid_parameter", "MCU list"))
        })
        .collect::<Result<_>>()?;
    if mcus.is_empty() || mcus.len() > 32 || mcus.iter().any(|n| !(1..=32).contains(n)) {
        return Err(Error::new("invalid_parameter", "MCUs must be 1..32"));
    }
    let matrix = parse_matrix(argument(&args, "--matrix"))?;
    let cancellation = Cancellation::default();
    let mut video = Video::open(File::open(&path)?, &cancellation)?;
    video.verify(&cancellation)?;
    let header = video.header;
    let digest = storage::digest_file(&path, &cancellation)?;
    let manifest: Value = path
        .parent()
        .map(|p| p.join("manifest.json"))
        .and_then(|p| fs::read(p).ok())
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or(Value::Null);
    let source_fps = if manifest["sha256"] == digest {
        manifest["input"]["original_fps"].clone()
    } else {
        Value::Null
    };
    let mut output = argument(&args, "--output")
        .map(|p| {
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(Path::new(&p))
        })
        .transpose()?;
    let baud: u32 = argument(&args, "--baud")
        .unwrap_or_else(|| "115200".into())
        .parse()
        .map_err(|_| Error::new("invalid_parameter", "UART baud"))?;
    let mut client = serial::open_port(&port, baud)?;
    let hello = client.hello()?;
    emit(
        &mut output,
        &json!({"event":"session","hello":hello,"prepared_sha256":digest,
        "run_index":0,"requested_repeat":requested_repeat,"run_index_base":1,
        "source_fps":source_fps,"encoded_fps":header.fps,"resolution":[header.width,header.height],
        "file_bytes":header.file_bytes,"matrix":matrix,"max_mcus":mcus,
        "physical_visual_quality":"requires_owner_inspection"}),
    )?;
    let preparation = (|| {
        if args.iter().any(|a| a == "--upload") {
            let (sender, receiver) = mpsc::sync_channel::<muse_video_core::media::Progress>(64);
            let mut progress_output = output.as_ref().map(File::try_clone).transpose()?;
            let progress_logger = thread::spawn(move || {
                let mut last = Instant::now();
                let mut previous_stage = String::new();
                for progress in receiver {
                    if progress.stage != previous_stage
                        || last.elapsed() >= Duration::from_secs(1)
                        || progress.total == Some(progress.completed)
                    {
                        previous_stage = progress.stage.clone();
                        last = Instant::now();
                        emit(
                            &mut progress_output,
                            &json!({"event":"usb_progress","progress":progress}),
                        )?;
                    }
                }
                Ok::<(), Error>(())
            });
            let uploaded = client.upload(&path, &cancellation, Some(sender));
            progress_logger
                .join()
                .map_err(|_| Error::new("io_error", "Upload progress logger failed"))??;
            let uploaded = uploaded?;
            emit(
                &mut output,
                &json!({"event":"usb_upload","result":uploaded}),
            )?;
        } else {
            client.request(json!({"method":"select_video","sha256":digest}))?;
        }
        let automatic_status = wait_video(&mut client, &digest)?;
        if args.iter().any(|arg| arg == "--isolate-queue") {
            // Explicit research option: prevent queue advancement/loop restart
            // from resetting frame counters midway through a timed clip.
            let state = client.request(json!({"method":"get_playback_state"}))?;
            client.request(json!({"method":"set_queue","ids":[digest],
                "expected_queue_revision":state["queue_revision"]}))?;
            client.request(json!({"method":"set_loop_mode","mode":"play_once"}))?;
            emit(
                &mut output,
                &json!({"event":"queue_isolation",
                "state":client.request(json!({"method":"get_playback_state"}))?}),
            )?;
        }
        let automatic_defaults = match client.request(json!({"method":"get_playback_defaults"})) {
            Ok(value) => json!({"result":value}),
            Err(error) => json!({"error":error}),
        };
        emit(
            &mut output,
            &json!({"event":"automatic_playback_defaults",
            "status":automatic_status,"defaults":automatic_defaults,
            "display":client.request(json!({"method":"get_display_info"}))?}),
        )?;
        client.request(json!({"method":"pause"}))?;
        if let Some(clock) = argument(&args, "--sd-spi-hz") {
            let hz: u32 = clock
                .parse()
                .map_err(|_| Error::new("invalid_parameter", "SD clock"))?;
            if ![10000000, 20000000, 40000000].contains(&hz) {
                return Err(Error::new("invalid_parameter", "SD clock 10/20/40 MHz"));
            }
            let applied = client.request(json!({"method":"set_sd_clock","hz":hz}))?;
            emit(
                &mut output,
                &json!({"event":"sd_clock_applied","result":applied}),
            )?;
            wait_video(&mut client, &digest)?;
            client.request(json!({"method":"pause"}))?;
        }
        client.request(
            json!({"method":"set_transform","x":(320-u32::from(header.width))/2,
            "y":(240-u32::from(header.height))/2,"width":header.width,"height":header.height,
            "rotation_deg":0,"lock_aspect":true}),
        )?;
        Ok::<(), Error>(())
    })();
    if let Err(error) = preparation {
        let mut device_diagnostics = serde_json::Map::new();
        // Refresh a lost session for diagnostics, without replaying the action.
        // Retaining this handle avoids an RTS/DTR close/open reset hiding the cause.
        if ["stale_session", "timeout"].contains(&error.code.as_str()) {
            let refreshed = match client.hello() {
                Ok(hello) => json!({"result":hello}),
                Err(error) => json!({"error":error}),
            };
            device_diagnostics.insert("hello_refresh".into(), refreshed);
        }
        for method in ["upload_status", "get_serial_diagnostics", "get_status"] {
            let diagnostic = match client.request(json!({"method":method})) {
                Ok(value) => json!({"result":value}),
                Err(error) => json!({"error":error}),
            };
            device_diagnostics.insert(method.into(), diagnostic);
        }
        emit(
            &mut output,
            &json!({"event":"preparation_error","run_index":0,"requested_repeat":requested_repeat,"error":error,"same_handle_device_diagnostics":device_diagnostics,"host_diagnostics":client.diagnostics()}),
        )?;
        return Err(error);
    }
    let mut completed_runs = 0;
    // Preparation and the serial client remain outside the repetition loop.
    // bench() sends restart on the retained session for each configuration,
    // resetting firmware's completed-draw FPS counters for that run.
    for run_index in 1..=requested_repeat {
        for configuration in &matrix {
            let mut applied_successfully = false;
            for &mcus in &mcus {
                let result = bench(
                    &mut client,
                    configuration,
                    mcus,
                    seconds,
                    target,
                    &decoder,
                    verify_every_frames,
                    prefetch,
                    decoder_warmup,
                    decoder_warmup_blocks,
                );
                match result {
                    Ok(mut result) => {
                        applied_successfully = true;
                        completed_runs += 1;
                        result["run_index"] = json!(run_index);
                        result["requested_repeat"] = json!(requested_repeat);
                        emit(&mut output, &result)?;
                        if args.iter().any(|arg| arg == "--frame-integrity") {
                            let checked = match client.request(json!({"method":"probe_pipeline","stage":"frame_integrity","repetitions":1})) {
                            Ok(value) => json!({"result":value}),
                            Err(error) => json!({"error":error}),
                        };
                            emit(
                                &mut output,
                                &json!({"event":"frame_integrity","run_index":run_index,"requested_repeat":requested_repeat,"configuration":configuration,"max_mcus":mcus,"check":checked}),
                            )?;
                        }
                    }
                    Err(error) => {
                        let _ = client.request(json!({"method":"pause"}));
                        emit(
                            &mut output,
                            &json!({"event":"run_error","configuration":configuration,
                        "run_index":run_index,"requested_repeat":requested_repeat,
                        "max_mcus":mcus,"error":error,"host_diagnostics":client.diagnostics()}),
                        )?;
                    }
                }
            }
            if applied_successfully && args.iter().any(|a| a == "--probes") {
                for stage in ["sd", "decode", "display"] {
                    match client
                        .request(json!({"method":"probe_pipeline","stage":stage,"repetitions":1}))
                    {
                        Ok(result) => emit(
                            &mut output,
                            &json!({"event":"microbenchmark","run_index":run_index,"requested_repeat":requested_repeat,"configuration":configuration,"stage":stage,"result":result}),
                        )?,
                        Err(error) => emit(
                            &mut output,
                            &json!({"event":"microbenchmark_error","run_index":run_index,"requested_repeat":requested_repeat,"configuration":configuration,"stage":stage,"error":error}),
                        )?,
                    }
                }
            }
        }
    }
    client.request(json!({"method":"pause"}))?;
    if completed_runs == 0 {
        return Err(Error::new(
            "verification_failed",
            "No playback configuration completed successfully; inspect run_error records",
        ));
    }
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{}", json!({"ok":false,"error":error}));
        std::process::exit(1);
    }
}
