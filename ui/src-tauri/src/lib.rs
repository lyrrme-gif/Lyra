// Lyra UI — Coucou visual shell (window, tray, island). Agent/Claude code removed.

mod autohide;
mod backend;
mod caption;
mod island;
mod log;
mod mascot;
mod platform;
mod settings;
mod spotify;
mod tray;
// >>> phase-8 begin
mod updater;
// <<< phase-8 end
mod volume;
mod wheel;

use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_autostart::{ManagerExt, MacosLauncher};

use backend::BackendProcess;
use island::{PollGate, ScreenInfo};
use settings::Settings;

pub struct Shared {
    pub settings: Mutex<Settings>,
    pub gate: Arc<PollGate>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootInfo {
    settings: Settings,
    screen: ScreenInfo,
    version: String,
    hook_path: String,
    cursor_poll: bool,
}

#[tauri::command]
fn boot(app: AppHandle, shared: State<'_, Shared>) -> BootInfo {
    let settings = shared.settings.lock().unwrap().clone();
    let screen = island::screen_info(&app, &settings.screen);
    BootInfo {
        settings,
        screen,
        version: env!("CARGO_PKG_VERSION").to_string(),
        hook_path: String::new(),
        cursor_poll: platform::CURSOR_POLL,
    }
}

#[tauri::command]
fn save_settings(app: AppHandle, shared: State<'_, Shared>, settings: Settings) {
    let (screen_changed, autostart_changed) = {
        let mut current = shared.settings.lock().unwrap();
        let screen_changed = current.screen != settings.screen;
        let autostart_changed = current.autostart != settings.autostart;
        *current = settings.clone();
        (screen_changed, autostart_changed)
    };
    if let Err(err) = settings::save(&settings) {
        eprintln!("[lyra] could not save settings: {err}");
    }
    if autostart_changed {
        let manager = app.autolaunch();
        let result = if settings.autostart {
            manager.enable()
        } else {
            manager.disable()
        };
        if let Err(err) = result {
            eprintln!("[lyra] autostart: {err}");
        }
    }
    if screen_changed {
        let collapsed = shared.gate.collapsed.load(Ordering::Relaxed);
        island::apply_geometry(&app, &settings.screen, collapsed);
        caption::place(&app, &settings.screen);
    }
    let _ = app.emit("settings-changed", settings);
}

#[tauri::command]
fn set_chat_panel(app: AppHandle, shared: State<'_, Shared>, tall: bool) {
    let pref = shared.settings.lock().unwrap().screen.clone();
    island::set_chat_geometry(&app, &pref, tall);
}

#[tauri::command]
fn set_collapsed(app: AppHandle, shared: State<'_, Shared>, collapsed: bool) {
    let pref = shared.settings.lock().unwrap().screen.clone();
    shared.gate.collapsed.store(collapsed, Ordering::Relaxed);
    island::apply_geometry(&app, &pref, collapsed);
    if collapsed {
        // Keep a lightweight global cursor poll alive while the 320×14 wake strip
        // is collapsed. Relying on WebView2 mouseenter here is unreliable when the
        // window has just resized or the cursor was already at the top edge.
        // The native poll keeps the strip non-click-through and emits cursor events
        // so the front end can wake immediately.
        island::set_ignore_cursor(&app, false);
        shared.gate.forget_ignore_state();
        shared.gate.set_active(true);
    } else {
        island::refresh_click_through(&app, &shared.gate);
        shared.gate.set_active(true);
    }
}

#[tauri::command]
fn set_island_rect(
    app: AppHandle,
    shared: State<'_, Shared>,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    extra: Option<Vec<island::IslandRect>>,
) {
    // Store the island shape (plus dock / secondary shapes). Re-arm the WebView
    // only when the cursor is already over one of them; otherwise the window
    // stays click-through, so animating shapes never flicker-block the desktop.
    shared.gate.set_rect(island::IslandRect { x, y, w: width, h: height });
    shared.gate.set_extra(extra.unwrap_or_default());
    if island::cursor_on_hit(&app, &shared.gate) {
        island::set_ignore_cursor(&app, false);
        shared.gate.force_interactive();
    }
}

#[tauri::command]
fn focus_window(app: AppHandle, focused: bool) {
    if let Some(win) = island::window(&app) {
        if focused {
            let _ = win.set_focus();
        }
    }
}

#[tauri::command]
fn caption_chat(app: AppHandle, shared: State<'_, Shared>, open: bool) -> bool {
    let pref = shared.settings.lock().unwrap().screen.clone();
    caption::set_chat(&app, &pref, open)
}

#[tauri::command]
fn caption_chat_extra(extra: f64) {
    caption::set_chat_extra(extra);
}

#[tauri::command]
fn reposition(app: AppHandle, shared: State<'_, Shared>) {
    let pref = shared.settings.lock().unwrap().screen.clone();
    let collapsed = shared.gate.collapsed.load(Ordering::Relaxed);
    island::apply_geometry(&app, &pref, collapsed);
    caption::place(&app, &pref);
}

#[tauri::command]
fn open_url(url: String) {
    let _ = open::that(&url);
}

#[tauri::command]
fn open_in_vscode(path: Option<String>) -> bool {
    let Some(path) = path else { return false };
    let mut cmd = std::process::Command::new("code");
    cmd.arg(&path);
    // `code` is a .cmd launcher: without this flag a console window flashes on screen.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn().is_ok()
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn log_line(message: String) {
    log::line(message);
}

// async: building a window from a sync command can deadlock on Windows.
#[tauri::command]
async fn open_settings_window(app: AppHandle) {
    if let Some(win) = app.get_webview_window("settings") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
        return;
    }
    // Not open (never opened, or closed = destroyed): build it now, visible. It loads its state on mount.
    create_settings_window(&app, true);
}

#[tauri::command]
fn set_paused(shared: State<'_, Shared>, on: bool) {
    shared.gate.set_active(!on);
}

#[tauri::command]
fn set_drop_target(app: AppHandle, shared: State<'_, Shared>, enabled: bool) {
    shared.gate.drop_target.store(enabled, Ordering::Relaxed);
    if enabled {
        // Revoke WebView2's child drag target before the next Explorer drag.
        // Tauri then receives the native drop through its parent target.
        platform::unblock_webview_drops(&app);
    }
    // The poll thread decides click-through; nothing is force-armed here.
    shared.gate.forget_ignore_state();
}

/// Big enough that the whole sidebar is visible without scrolling: about 88% of the screen's height
/// (never below 700 or above 960) and 82% of its width (1040..1320), in logical pixels.
fn settings_window_size(app: &AppHandle) -> (f64, f64) {
    if let Some(m) = app.primary_monitor().ok().flatten() {
        let scale = m.scale_factor().max(0.5);
        let (sw, sh) = (m.size().width as f64 / scale, m.size().height as f64 / scale);
        return ((sw * 0.82).clamp(1040.0, 1320.0).min(sw), (sh * 0.88).clamp(700.0, 960.0).min(sh));
    }
    (1180.0, 820.0)
}

fn create_settings_window(app: &AppHandle, visible: bool) {
    let (w, h) = settings_window_size(app);
    let _ = WebviewWindowBuilder::new(
        app,
        "settings",
        WebviewUrl::App("settings.html".into()),
    )
    .title("Settings — Lyra")
    .inner_size(w, h)
    .min_inner_size(900.0, 600.0)
    .center()
    .resizable(true)
    // No title bar: the page draws its own drag area and close button (see settings/main.ts, settings-frameless.css).
    // shadow(true) keeps the soft shadow and, on Windows 11, the rounded corners of an undecorated window.
    .decorations(false)
    .shadow(true)
    .visible(visible)
    .focused(visible)
    .build();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let loaded = settings::load();
    let gate = Arc::new(PollGate::new());

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            let _ = app.emit_to(island::WINDOW_LABEL, "tray", "open".to_string());
        }))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, None))
        .manage(Shared {
            settings: Mutex::new(loaded.clone()),
            gate: gate.clone(),
        })
        .manage(BackendProcess::new())
        .invoke_handler(tauri::generate_handler![
            boot,
            save_settings,
            set_collapsed,
            set_chat_panel,
            set_island_rect,
            focus_window,
            caption_chat,
            caption_chat_extra,
            reposition,
            open_url,
            open_in_vscode,
            quit_app,
            log_line,
            open_settings_window,
            set_paused,
            set_drop_target,
            autohide::context,
            spotify::spotify_control,
            spotify::spotify_music_view,
            mascot::mascot_boot,
            mascot::mascot_refresh_prefs,
            mascot::mascot_set_rects,
            mascot::mascot_set_active,
            mascot::mascot_monitor,
            mascot::mascot_open_chat,
            mascot::mascot_raise,
            mascot::mascot_carry,
            wheel::wheel_open,
            wheel::wheel_close,
            wheel::wheel_pick,
            wheel::wheel_cmd,
            // >>> phase-8 begin
            updater::install_update,
            // <<< phase-8 end
        ])
        // The settings window is built on first open and destroyed on close (default close behaviour):
        // nothing of it lives in memory while it is shut.
        .setup(move |app| {
            let handle = app.handle().clone();
            // Backend first. Installed build: start lyra-backend.exe if the bridge is down and WAIT until it answers.
            // No window exists yet at this point (the island is built below), so the UI cannot open before the backend.
            if let Some(bp) = handle.try_state::<BackendProcess>() {
                backend::start_if_needed(&handle, &bp);
            }
            // The island window is declared with "create": false in tauri.conf.json: build it now, backend is up.
            for cfg in app.config().app.windows.iter().filter(|w| !w.create) {
                if handle.get_webview_window(&cfg.label).is_none() {
                    if let Err(err) = WebviewWindowBuilder::from_config(&handle, cfg).and_then(|b| b.build()) {
                        log::line(format!("window {}: build failed: {err}", cfg.label));
                    }
                }
            }
            tray::build(&handle)?;
            // Bottom-of-screen caption pill (what Lyra heard / answered). Windows only.
            if cfg!(windows) {
                caption::create(&handle, &loaded.screen);
                // The tab wheel (hold Ctrl+CapsLock): its own hidden window, shown at the cursor.
                wheel::create(&handle);
            }

            if let Some(win) = island::window(&handle) {
                platform::make_non_activating(&win);
                island::apply_geometry(&handle, &loaded.screen, false);
                let _ = win.show();
            }
            gate.collapsed.store(false, Ordering::Relaxed);
            if !platform::CURSOR_POLL {
                island::refresh_click_through(&handle, &gate);
            }
            gate.set_active(true);
            island::spawn_cursor_poll(handle.clone(), gate.clone());
            volume::start(&handle);
            autohide::start(&handle);
            spotify::start(&handle);
            // The mascot owns a separate full-monitor overlay and cursor thread.
            // Start it after backend startup is scheduled; its control loop waits
            // for the feature settings endpoint before creating the window.
            if cfg!(windows) {
                mascot::start(&handle);
            }

            // The installer may have switched "start with Windows" on: show the real state.
            if let Ok(on) = handle.autolaunch().is_enabled() {
                let shared = handle.state::<Shared>();
                let mut s = shared.settings.lock().unwrap();
                if s.autostart != on {
                    s.autostart = on;
                    let _ = settings::save(&s);
                }
            }

            log::line(format!("--- Lyra UI {} started ---", env!("CARGO_PKG_VERSION")));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Lyra UI")
        .run(|app_handle, event| {
            backend::on_run_event(app_handle, &event);
            if let RunEvent::Exit = event {
                // ensure backend is stopped even if the ExitRequested path was skipped
                if let Some(bp) = app_handle.try_state::<BackendProcess>() {
                    backend::stop(&bp);
                }
            }
        });
}
