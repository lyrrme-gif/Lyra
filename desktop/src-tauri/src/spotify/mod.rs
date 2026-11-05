// Spotify/media-session backend.
//
// model.rs contains the pure media-state helpers; win.rs contains Windows
// process/media-session access. This module only keeps the media session feed
// alive and forwards state to Lyra's main island for the Music tab. There is no
// second Spotify controller window or legacy compact Spotify renderer.
//
// Only Windows has a Spotify backend; elsewhere the module compiles to a no-op.
#![cfg_attr(not(windows), allow(dead_code, unused_imports))]

mod model;
#[cfg(windows)]
mod win;

#[cfg(windows)]
use win as sys;

/// Stand-in for non-Windows builds: Spotify is never "running", so the
/// monitor thread is never started and no window is ever created.
#[cfg(not(windows))]
mod sys {
    use super::model::Media;

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub enum Control {
        TogglePlayPause,
        Next,
        Previous,
        Seek(i64),
        ToggleShuffle,
    }
    pub struct Reader;
    impl Reader {
        pub fn new() -> Self {
            Reader
        }
        pub fn poll(&mut self) -> Result<Option<Media>, String> {
            Ok(None)
        }
        pub fn has_session(&mut self) -> bool {
            false
        }
        pub fn control(&mut self, _: Control) -> Result<(), String> {
            Ok(())
        }
    }
    pub fn init_thread() {}
    pub fn spotify_running() -> bool {
        false
    }
}

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager, State};

use crate::island;
use model::{Dedup, ACTIVE_TICK, IDLE_TICK};

/// Playback state for the page (see `model::Payload`).
const EVENT_STATE: &str = "spotify-state";
const MISSES_BEFORE_HIDE: u32 = 2;
const CONTROL_SETTLE: Duration = Duration::from_millis(150);

enum Cmd {
    /// The island's Music tab opened: send the island window the full current state.
    ResendMain,
    Control(sys::Control),
}

/// Shared between the monitor thread and the commands the page calls.
pub struct SpotifyHandle {
    tx: Sender<Cmd>,
    /// Kept as the media-feed lifetime flag used by the Music tab bridge.
    music_view: AtomicBool,
}

// ── Commands ────────────────────────────────────────────────────────────────

/// The island's Music tab opened (`true`) or closed (`false`). The media feed
/// stays available to the main island and the Music tab, and opening the tab
/// requests a fresh state immediately.
#[tauri::command]
pub fn spotify_music_view(handle: State<'_, SpotifyHandle>, active: bool) {
    // The main notch owns the Spotify presentation now, so its media feed must
    // stay alive even when the optional Music tab is not selected.
    handle.music_view.store(true, Ordering::Relaxed);
    if active {
        let _ = handle.tx.send(Cmd::ResendMain);
    }
}

/// Sends a playback command from the current Music tab to the Windows media session.
/// This is part of the new Music tab API; removing the legacy controller must not
/// remove the shared transport command used by the tab.
#[tauri::command]
pub fn spotify_control(handle: State<'_, SpotifyHandle>, action: String) {
    let control = match action.as_str() {
        "toggle" => sys::Control::TogglePlayPause,
        "next" => sys::Control::Next,
        "previous" => sys::Control::Previous,
        "shuffle" => sys::Control::ToggleShuffle,
        _ if action.starts_with("seek:") => action[5..]
            .parse::<i64>()
            .ok()
            .map(|ms| sys::Control::Seek(ms.saturating_mul(10_000)))
            .unwrap_or(sys::Control::Seek(0)),
        _ => return,
    };
    let _ = handle.tx.send(Cmd::Control(control));
}

// ── Start-up ────────────────────────────────────────────────────────────────

/// Called once from `setup`. Starts the monitor thread (Windows only).
pub fn start(app: &AppHandle) {
    let (tx, rx) = mpsc::channel();
    app.manage(SpotifyHandle {
        tx,
        music_view: AtomicBool::new(true),
    });

    #[cfg(windows)]
    {
        let app = app.clone();
        if let Err(err) = std::thread::Builder::new()
            .name("lyra-spotify".into())
            .spawn(move || run(app, rx))
        {
            crate::log::line(format!("spotify island: could not start monitor: {err}"));
        }
    }
    #[cfg(not(windows))]
    drop(rx);
}

// ── Monitor thread ──────────────────────────────────────────────────────────

/// Logs a message only when it differs from the previous one, so a persistent
/// failure writes one line instead of one per second.
fn note(last: &mut String, message: String) {
    if *last != message {
        crate::log::line(format!("spotify island: {message}"));
        *last = message;
    }
}

fn run(app: AppHandle, rx: Receiver<Cmd>) {
    sys::init_thread();

    let mut reader: Option<sys::Reader> = None;
    // The island window is a second consumer: it needs its own "what does the page
    // already know" memory, or the pill's updates would starve it of the cover.
    let mut main_dedup = Dedup::default();
    let mut was_running = false;
    let mut misses = 0u32;
    let mut last_note = String::new();
    let mut wait = IDLE_TICK;

    loop {
        // Sleep until the next tick — or until the page or a click wants something.
        let cmd = match rx.recv_timeout(wait) {
            Ok(cmd) => Some(cmd),
            Err(RecvTimeoutError::Timeout) => None,
            Err(RecvTimeoutError::Disconnected) => return,
        };
        let handle = app.state::<SpotifyHandle>();
        let music = handle.music_view.load(Ordering::Relaxed);
        let resend_main = matches!(cmd, Some(Cmd::ResendMain));

        // 1. Is there anything to show? Spotify open (process list), or any app with a
        // media session (YouTube in a browser, VLC, Netflix...).
        let media_open = sys::spotify_running()
            || reader.get_or_insert_with(sys::Reader::new).has_session();
        if !media_open {
            misses += 1;
            // The Music tab says "Spotify isn't running" — once that is confirmed, so a
            // restart or an update does not flash it. The dedup keeps it to one event.
            if music && (misses >= MISSES_BEFORE_HIDE || resend_main) {
                if let Some(payload) = main_dedup.update(false, None, Instant::now(), resend_main) {
                    let _ = app.emit_to(island::WINDOW_LABEL, EVENT_STATE, &payload);
                }
            }
            if was_running && misses >= MISSES_BEFORE_HIDE {
                was_running = false;
            }
            wait = IDLE_TICK;
            continue;
        }
        misses = 0;
        if !was_running {
            crate::log::line("spotify island: media source detected");
        }
        was_running = true;

        // The main Lyra island / Music tab is the only presentation surface.
        // There is deliberately no second Spotify controller window.

        if let Some(Cmd::Control(action)) = cmd {
            if let Some(reader) = reader.as_mut() {
                if let Err(err) = reader.control(action) {
                    note(&mut last_note, err);
                }
                std::thread::sleep(CONTROL_SETTLE);
            }
        }

        // Read Spotify/media state for the main Lyra island only.
        let for_island = music || resend_main;
        if for_island {
            let reader = reader.get_or_insert_with(sys::Reader::new);
            let media = match reader.poll() {
                Ok(media) => media,
                Err(err) => {
                    note(&mut last_note, err);
                    None
                }
            };
            if let Some(payload) =
                main_dedup.update(true, media.as_ref(), Instant::now(), resend_main)
            {
                let _ = app.emit_to(island::WINDOW_LABEL, EVENT_STATE, &payload);
            }
        }

        // The main island is the only media presentation surface.
        wait = if music { ACTIVE_TICK } else { IDLE_TICK };
    }
}

