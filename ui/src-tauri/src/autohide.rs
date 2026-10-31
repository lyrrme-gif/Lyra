// What the user is looking at, for the Island and Mochi auto-hide options (Settings → Island / Mochi).
//
// This thread only reports facts: is the desktop in front, is an app covering the screen, is a screen
// share running. Each window applies its own mode to them (the island and the notch Mochi in their
// pages, the floating Mochi in mascot.rs), so the two options stay independent.

use std::sync::atomic::{AtomicU8, Ordering};
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter};

use crate::platform::{self, Foreground};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Context {
    /// No app window is focused (desktop, taskbar, tray).
    pub desktop: bool,
    /// The focused app is maximized or full-screen.
    pub covering: bool,
    /// A screen share is running.
    pub sharing: bool,
}

const DESKTOP: u8 = 1;
const COVERING: u8 = 2;
const SHARING: u8 = 4;

/// Packed so mascot.rs can read it without a lock. Starts as "on the desktop" so nothing hides before the first look.
static CTX: AtomicU8 = AtomicU8::new(DESKTOP);

impl Context {
    fn pack(self) -> u8 {
        (if self.desktop { DESKTOP } else { 0 })
            | (if self.covering { COVERING } else { 0 })
            | (if self.sharing { SHARING } else { 0 })
    }
    fn unpack(v: u8) -> Self {
        Self { desktop: v & DESKTOP != 0, covering: v & COVERING != 0, sharing: v & SHARING != 0 }
    }
}

pub fn current() -> Context {
    Context::unpack(CTX.load(Ordering::Relaxed))
}

/// Should something set to `mode` be hidden right now? Mirrors `autoHidden()` in src/core/autohide.ts.
pub fn hidden(mode: &str, ctx: Context) -> bool {
    if ctx.sharing {
        return true;
    }
    match mode {
        "desktop" => !ctx.desktop,
        _ => false,
    }
}

#[tauri::command]
pub fn context() -> Context {
    current()
}

/// Called once from lib.rs setup. Foreground is read ~3×/s (one Win32 call); the screen-share check
/// walks the window list about once a second.
pub fn start(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        let (mut desktop, mut covering, mut sharing) = (true, false, false);
        let mut ticks: u32 = 0;
        loop {
            std::thread::sleep(Duration::from_millis(300));
            match platform::foreground() {
                Foreground::Desktop => {
                    desktop = true;
                    covering = false;
                }
                Foreground::App { covering: c } => {
                    desktop = false;
                    covering = c;
                }
                // Our own window has focus (settings, chat bar): keep the last answer.
                Foreground::Own => {}
            }
            ticks = ticks.wrapping_add(1);
            if ticks % 3 == 0 {
                sharing = platform::screen_sharing();
            }
            let next = Context { desktop, covering, sharing };
            if next != current() {
                CTX.store(next.pack(), Ordering::Relaxed);
                crate::log::line(format!(
                    "context: desktop={} covering={} sharing={}",
                    next.desktop, next.covering, next.sharing
                ));
                let _ = app.emit_to(crate::island::WINDOW_LABEL, "context", next);
            }
        }
    });
}
