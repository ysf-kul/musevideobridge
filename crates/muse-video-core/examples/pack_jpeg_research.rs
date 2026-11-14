//! Host research helper: stream baseline JPEGs through the production MVB writer.
//! No serial/device access; deliberately creates a new directory without replacing files.
use muse_video_core::{
    container::{Rate, Video, Writer},
    jpeg, storage, Cancellation, Error, Result,
};
use serde_json::json;
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::BufReader,
    path::PathBuf,
};

fn run() -> Result<()> {
    let args: Vec<_> = env::args().skip(1).collect();
    if args.len() != 3 {
        return Err(Error::new(
            "invalid_parameter",
            "JPEG-stream output-directory integer-FPS",
        ));
    }
    let fps = Rate::new(
        args[2]
            .parse()
            .map_err(|_| Error::new("invalid_parameter", "FPS"))?,
        1,
    )?;
    let output = PathBuf::from(&args[1]);
    fs::create_dir(&output)?;
    let file = OpenOptions::new()
        .write(true)
        .read(true)
        .create_new(true)
        .open(output.join("video.mvb"))?;
    let index = OpenOptions::new()
        .write(true)
        .read(true)
        .create_new(true)
        .open(output.join("index.part"))?;
    let mut writer = Writer::new(file, index, fps)?;
    let mut input = BufReader::with_capacity(65536, File::open(&args[0])?);
    let cancel = Cancellation::default();
    while let Some(frame) = jpeg::next_frame(&mut input)? {
        writer.push(&frame)?;
    }
    let header = writer.finish(&cancel)?;
    let path = output.join("video.mvb");
    let mut checked = Video::open(File::open(&path)?, &cancel)?;
    checked.verify(&cancel)?;
    let digest = storage::digest_file(&path, &cancel)?;
    println!(
        "{}",
        json!({"scope":"host_research_existing_writer_and_parser","header":header,"sha256":digest,"research_jpeg_chroma":cfg!(feature = "research-jpeg-chroma"),"hardware_tested":false})
    );
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
