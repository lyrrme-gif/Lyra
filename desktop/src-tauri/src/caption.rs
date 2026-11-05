// The caption window: a transparent, click-through strip near the bottom of the screen
// where Lyra shows what she heard and what she answers (see src/caption).

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindowBuilder};
use serde::Serialize;

pub const LABEL: &str = "caption";
const W: f64 = 760.0;
const H: f64 = 150.0;
/// Taller while the chat bar is open: the bar sits at the bottom, the subtitles stack above it.
const H_CHAT: f64 = 240.0;
/// ...plus room for the past-chats panel that opens above the bar (double-click). The window is made this tall
/// once, when the bar opens, and never resized while it is open: the panel only changes what takes the mouse.
const CHAT_EXTRA_MAX: f64 = 320.0;
/// The chat bar's box inside the window (see #chatbar in caption.css): centred, this wide / tall,
/// this far above the bottom edge. Only this box takes the mouse while the bar is open.
const BAR_W: f64 = 460.0;
const BAR_H: f64 = 50.0;
const BAR_BOTTOM: f64 = 14.0;
const BAR_HIT_PAD: f64 = 6.0;

/// True while the chat bar is open (the window is then interactive and focusable).
static CHAT_OPEN: AtomicBool = AtomicBool::new(false);
/// Height (logical px) of the past-chats panel above the bar right now; 0 = closed. Set by the page (caption_chat_extra).
static CHAT_EXTRA: AtomicU32 = AtomicU32::new(0);
/// Space kept below the pill so it clears the Windows taskbar.
const BOTTOM_GAP: f64 = 64.0;

#[derive(Clone, Serialize)]
struct CursorPoint { x: f64, y: f64 }

pub fn create(app: &AppHandle, pref: &str) {
    let built = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("caption.html".into()))
        .title("Lyra caption")
        .inner_size(W, H)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .visible(false)
        .build();
    let Ok(win) = built else { return };
    let _ = win.set_ignore_cursor_events(true);
    crate::platform::make_non_activating(&win);
    place(app, pref);
    let _ = win.show();
    // Click-through windows do not receive DOM pointer events. Feed the global
    // cursor position to the caption webview so its avatar can still track it.
    #[cfg(windows)]
    {
        let app = app.clone();
        std::thread::spawn(move || {
            use std::time::{Duration, Instant};
            let mut last = (f64::NAN, f64::NAN);
            let mut last_raw: Option<(f64, f64)> = None;
            let mut last_moved = Instant::now();
            let mut last_beat = Instant::now();
            let mut last_maint = Instant::now();
            let mut maint_n: u32 = 0;
            let mut chat_was = false;
            // Click-through state while the chat bar is open: only the bar itself takes the mouse.
            let mut passthrough = true;
            loop {
                let Some(win) = app.get_webview_window(LABEL) else { break };
                // Windows can hide or un-topmost a click-through overlay (full-screen app, show-desktop,
                // display change). Without this a hidden caption window never shows another subtitle.
                // Once a second (the topmost refresh every other second).
                if last_maint.elapsed() >= Duration::from_millis(1000) {
                    last_maint = Instant::now();
                    maint_n = maint_n.wrapping_add(1);
                    if !win.is_visible().unwrap_or(true) {
                        let _ = win.show();
                    }
                    if maint_n % 2 == 0 {
                        let _ = win.set_always_on_top(true);
                    }
                }
                // GetCursorPos is cheap; every window query below is a round trip to the UI thread. So those only run when
                // the cursor moved, the chat bar was opened or closed, or on a half-second heartbeat (the window itself may have moved).
                let raw = crate::platform::cursor_physical();
                let moved = raw != last_raw;
                if moved {
                    last_raw = raw;
                    last_moved = Instant::now();
                }
                let chat = CHAT_OPEN.load(Ordering::Relaxed);
                if moved || chat != chat_was || last_beat.elapsed() >= Duration::from_millis(500) {
                    chat_was = chat;
                    last_beat = Instant::now();
                    if let (Some((x, y)), Ok(pos)) = (raw, win.outer_position()) {
                        let scale = win.scale_factor().unwrap_or(1.0).max(0.1);
                        let point = (x - pos.x as f64, y - pos.y as f64);
                        if point != last {
                            let _ = win.emit("caption-cursor", CursorPoint { x: point.0 / scale, y: point.1 / scale });
                            last = point;
                        }
                        if chat {
                            let (lx, ly) = (point.0 / scale, point.1 / scale);
                            let left = (W - BAR_W) / 2.0 - BAR_HIT_PAD;
                            let right = (W + BAR_W) / 2.0 + BAR_HIT_PAD;
                            let extra = CHAT_EXTRA.load(Ordering::Relaxed) as f64;
                            let bottom = H_CHAT + CHAT_EXTRA_MAX - BAR_BOTTOM + BAR_HIT_PAD;
                            let top = bottom - BAR_H - extra - 2.0 * BAR_HIT_PAD;
                            let inside = lx >= left && lx <= right && ly >= top && ly <= bottom;
                            if inside == passthrough {
                                passthrough = !inside;
                                let _ = win.set_ignore_cursor_events(passthrough);
                            }
                        } else if !passthrough {
                            passthrough = true;
                            let _ = win.set_ignore_cursor_events(true);
                        }
                    }
                }
                // 60 Hz while the cursor is moving (the avatar follows it) or the chat bar is open; 10 Hz when it has been still.
                let calm = !chat && last_moved.elapsed() > Duration::from_millis(1500);
                std::thread::sleep(Duration::from_millis(if calm { 100 } else { 16 }));
            }
        });
    }
}

/// Bottom centre of the monitor the island lives on.
pub fn place(app: &AppHandle, pref: &str) {
    let Some(win) = app.get_webview_window(LABEL) else { return };
    let Some(m) = crate::island::target_monitor(app, pref) else { return };
    let scale = m.scale_factor();
    let mp = *m.position();
    let ms = *m.size();
    let pw = (W * scale).round().max(1.0) as u32;
    let h = if CHAT_OPEN.load(Ordering::Relaxed) { H_CHAT + CHAT_EXTRA_MAX } else { H };
    let ph = (h * scale).round().max(1.0) as u32;
    let x = mp.x + (ms.width as i32 - pw as i32) / 2;
    let y = mp.y + ms.height as i32 - ph as i32 - (BOTTOM_GAP * scale).round() as i32;
    let _ = win.set_size(PhysicalSize::new(pw, ph));
    let _ = win.set_position(PhysicalPosition::new(x, y));
    let _ = win.set_always_on_top(true);
}

