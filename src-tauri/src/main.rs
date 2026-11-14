#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use muse_video_core::{
    media::{Estimate, Options},
    storage::Destination,
    Error, Result,
};
use muse_video_desktop::{settings::Settings, App, Media, Snapshot};
use serde_json::Value;
use tauri::{Emitter, Manager};
use tauri_plugin_dialog::DialogExt;
async fn blocking<T: Send + 'static>(f: impl FnOnce() -> Result<T> + Send + 'static) -> Result<T> {
    tauri::async_runtime::spawn_blocking(f)
        .await
        .map_err(|e| Error::new("io_error", e))?
}
#[tauri::command]
fn snapshot(app: tauri::State<App>) -> Snapshot {
    app.snapshot()
}
#[tauri::command]
async fn save_settings(app: tauri::State<'_, App>, settings: Settings) -> Result<Settings> {
    let app = app.inner().clone();
    blocking(move || app.settings(settings)).await
}
#[tauri::command]
async fn select_video(
    handle: tauri::AppHandle,
    app: tauri::State<'_, App>,
) -> Result<Option<Media>> {
    let app = app.inner().clone();
    blocking(move || {
        let lang = app.snapshot().settings.language;
        let label = muse_video_core::localization::text(
            if lang == "en" {
                muse_video_core::localization::Locale::English
            } else {
                muse_video_core::localization::Locale::Turkish
            },
            "video.select",
        );
        let Some(path) = handle
            .dialog()
            .file()
            .set_title(label)
            .add_filter("MP4", &["mp4"])
            .blocking_pick_file()
        else {
            return Ok(None);
        };
        let path = path.into_path().map_err(|e| Error::new("unsafe_path", e))?;
        app.select(&path).map(Some)
    })
    .await
}
#[tauri::command]
async fn select_card(
    handle: tauri::AppHandle,
    app: tauri::State<'_, App>,
) -> Result<Option<(String, Destination)>> {
    let app = app.inner().clone();
    blocking(move || {
        let lang = app.snapshot().settings.language;
        let label = muse_video_core::localization::text(
            if lang == "en" {
                muse_video_core::localization::Locale::English
            } else {
                muse_video_core::localization::Locale::Turkish
            },
            "storage.select_folder",
        );
        let Some(path) = handle
            .dialog()
            .file()
            .set_title(label)
            .blocking_pick_folder()
        else {
            return Ok(None);
        };
        let path = path.into_path().map_err(|e| Error::new("unsafe_path", e))?;
        let info = app.destination(&path)?;
        Ok(Some((path.to_string_lossy().into(), info)))
    })
    .await
}
#[tauri::command]
async fn select_videos(
    handle: tauri::AppHandle,
    app: tauri::State<'_, App>,
) -> Result<Option<Vec<Media>>> {
    let app = app.inner().clone();
    blocking(move || {
        let Some(paths) = handle
            .dialog()
            .file()
            .add_filter("MP4", &["mp4"])
            .blocking_pick_files()
        else {
            return Ok(None);
        };
        let paths = paths
            .into_iter()
            .map(|p| p.into_path().map_err(|e| Error::new("unsafe_path", e)))
            .collect::<Result<Vec<_>>>()?;
        app.select_many(&paths).map(Some)
    })
    .await
}
#[tauri::command]
fn convert_videos(app: tauri::State<App>, options: Options) -> Result<()> {
    app.convert_many(options)
}
#[tauri::command]
fn upload_videos(app: tauri::State<App>, video_ids: Vec<String>) -> Result<()> {
    app.upload_many(video_ids)
}
#[tauri::command]
fn export_videos(
    app: tauri::State<App>,
    destination: String,
    video_ids: Vec<String>,
) -> Result<()> {
    app.export_many(destination.into(), video_ids, false)
}
#[tauri::command]
fn estimate(app: tauri::State<App>, options: Options) -> Result<Estimate> {
    app.estimate(&options)
}
#[tauri::command]
fn convert(app: tauri::State<App>, options: Options) -> Result<()> {
    app.convert(options)
}
#[tauri::command]
fn export(app: tauri::State<App>, destination: String) -> Result<()> {
    app.export(destination.into(), false)
}
#[tauri::command]
fn upload(app: tauri::State<App>, video_id: Option<String>) -> Result<()> {
    app.upload_video(video_id)
}
#[tauri::command]
fn cancel(app: tauri::State<App>) {
    app.cancel()
}
#[tauri::command]
async fn ports() -> Result<Vec<String>> {
    blocking(muse_video_core::serial::port_names).await
}
#[tauri::command]
fn connect(app: tauri::State<App>, port: String) -> Result<()> {
    app.device.connect(port)
}
#[tauri::command]
fn disconnect(app: tauri::State<App>) -> Result<()> {
    app.device.disconnect()
}
#[tauri::command]
async fn command(app: tauri::State<'_, App>, value: Value) -> Result<Value> {
    let app = app.inner().clone();
    blocking(move || app.command(value)).await
}
#[tauri::command]
async fn frame(
    app: tauri::State<'_, App>,
    index: u32,
    digest: String,
) -> Result<tauri::ipc::Response> {
    let app = app.inner().clone();
    blocking(move || app.frame(index, &digest).map(tauri::ipc::Response::new)).await
}
#[tauri::command]
async fn source_preview(app: tauri::State<'_, App>) -> Result<tauri::ipc::Response> {
    let app = app.inner().clone();
    blocking(move || app.source_preview().map(tauri::ipc::Response::new)).await
}
fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|handle| {
            let h = handle.handle().clone();
            let emit = std::sync::Arc::new(move |name: &str, value: Value| {
                let _ = h.emit(name, value);
            });
            let (config, cache) = muse_video_desktop::paths::directories(
                &std::env::current_exe()?,
                handle.path().app_config_dir()?,
                handle.path().app_cache_dir()?,
            )?;
            let app = App::new(config, cache.clone(), emit, None)?;
            handle.manage(app);
            // Create after state initialization; portable Windows WebView data stays in Data.
            let window =
                tauri::WebviewWindowBuilder::from_config(handle, &handle.config().app.windows[0])?;
            #[cfg(target_os = "windows")]
            let window = window.data_directory(cache.join("webview"));
            window.build()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            source_preview,
            snapshot,
            save_settings,
            select_video,
            select_videos,
            convert_videos,
            upload_videos,
            export_videos,
            select_card,
            estimate,
            convert,
            export,
            upload,
            cancel,
            ports,
            connect,
            disconnect,
            command,
            frame
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                window.state::<App>().shutdown();
            }
        })
        .run(tauri::generate_context!())
        .expect("Muse Video Bridge desktop runtime");
}
