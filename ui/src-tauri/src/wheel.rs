// The launcher window: a transparent, always-on-top window that appears in the middle of the
// active monitor, shows a circle of category icons (a clicked one opens its tabs in a ring) (src/wheel, TypeScript) and goes
// away again. It is its own window so it is not limited to the island's 720×320 window.
//
//   island ──wheel_open(at_cursor, payload)──▶ here: place + show, tell the page ("wheel-open")
//   page ──wheel_pick(view)──▶ here: hide, tell the island ("wheel-pick")
//   page ──wheel_close()──▶ here: hide, tell the island ("wheel-closed")
//   here ──"wheel-leave"──▶ page: play the closing animation (the window is hidden LEAVE_MS later)
//   island ──wheel_cmd("commit" | "stay")──▶ here ──"wheel-cmd"──▶ page (the held shortcut was let go / tapped)
//
// Like the island, the window never takes focus (it must not pull you out of a game or a text
// field), so it cannot see the keyboard, or a click that lands on another window. While it is up a
// small thread watches for both: Esc, or a left click outside the window, closes it.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde_json::Value;
use tauri::webview::PageLoadEvent;
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};

use crate::{island, platform};

pub const LABEL: &str = "wheel";

/// Logical size of the window: room for the biggest ring and its tooltips
/// (src/core/wheelGeometry.ts: WHEEL_W and WHEEL_H are the same numbers). On a small screen the
/// window is made smaller and the page scales the layout down to fit (see `place`).
const WIDTH: f64 = 640.0;
const HEIGHT: f64 = 440.0;

/// Never let the window take more than this share of the monitor.
const MAX_SHARE: f64 = 0.92;

/// How long the window stays up after a choice, so the closing animation can play.
const LEAVE_MS: u64 = 140;

/// Bumped on every show and every hide, so a hide that is waiting out LEAVE_MS knows when the
/// wheel has been opened again in the meantime and must not hide it.
static GEN: AtomicU64 = AtomicU64::new(0);

/// The wheel is on screen.
static UP: AtomicBool = AtomicBool::new(false);
/// The thread that watches for Esc / an outside click is running.
static WATCHING: AtomicBool = AtomicBool::new(false);

/// The wheel's window only exists while it is needed: it is built the first time the wheel opens (about half a second, once)
/// and destroyed again after it has been closed for IDLE_DESTROY_S, so an unused wheel costs no WebView2 process at all.
const IDLE_DESTROY_S: u64 = 90;

/// The payload of an open that is waiting for a freshly built window to finish loading its page.
static PENDING: Mutex<Option<Value>> = Mutex::new(None);
/// A "commit" / "stay" that arrived while that open was still waiting (a quick tap of the shortcut): sent right after the open.
static PENDING_CMD: Mutex<Option<String>> = Mutex::new(None);

/// Builds the (hidden) window; when its page has loaded the waiting open, if any, is shown.
fn build(app: &AppHandle) {
    if app.get_webview_window(LABEL).is_some() {
        return;
    }
    let handle = app.clone();
    let built = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("wheel.html".into()))
        .on_page_load(move |_win, payload| {
            if matches!(payload.event(), PageLoadEvent::Finished) {
                let h = handle.clone();
                std::thread::spawn(move || {
                    // a moment for the page's own listeners to be set up before the first event
                    std::thread::sleep(Duration::from_millis(150));
                    flush_pending(&h);
                });
            }
        })
        .title("Lyra launcher")
        .inner_size(WIDTH, HEIGHT)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .visible(false)
        .build();
    let Ok(win) = built else {
        crate::log::line("wheel: could not build the window");
        return;
    };
    platform::make_non_activating(&win);
}

/// Centres the window on the active monitor: the one under the cursor when it can be read,
/// otherwise the one the island lives on. The launcher is NOT placed around the cursor. On a
/// monitor too small for WIDTH × HEIGHT the window shrinks (same aspect) and the page scales
/// the layout to match, so it is never cut off.
fn place(app: &AppHandle, win: &WebviewWindow, _at_cursor: bool) {
    let pref = app
        .try_state::<crate::Shared>()
        .map(|s| s.settings.lock().unwrap().screen.clone())
        .unwrap_or_else(|| "primary".into());
    let cursor = platform::cursor_physical();
    let monitor = island::target_monitor(app, if cursor.is_some() { "cursor" } else { pref.as_str() });
    let Some(m) = monitor else { return };
    let scale = m.scale_factor();
    let mp = *m.position();
    let ms = *m.size();
    // Largest scale-down (never an enlargement) that keeps the window inside MAX_SHARE of the screen.
    let logical_w = ms.width as f64 / scale;
    let logical_h = ms.height as f64 / scale;
    let k = (logical_w * MAX_SHARE / WIDTH).min(logical_h * MAX_SHARE / HEIGHT).min(1.0);
    let w = (WIDTH * k * scale).round().max(1.0) as i32;
    let h = (HEIGHT * k * scale).round().max(1.0) as i32;
    let x = mp.x + (ms.width as i32 - w) / 2;
    let y = mp.y + (ms.height as i32 - h) / 2;
    let _ = win.set_size(PhysicalSize::new(w as u32, h as u32));
    let _ = win.set_position(PhysicalPosition::new(x, y));
}

/// Shows the open that was waiting for the window to load.
fn flush_pending(app: &AppHandle) {
    let payload = match PENDING.lock() {
        Ok(mut g) => g.take(),
        Err(_) => None,
    };
    let Some(payload) = payload else { return };
    let Some(win) = app.get_webview_window(LABEL) else { return };
    show_now(app, &win, false, payload);
    let cmd = match PENDING_CMD.lock() {
        Ok(mut g) => g.take(),
        Err(_) => None,
    };
    if let Some(cmd) = cmd {
        let _ = app.emit_to(LABEL, "wheel-cmd", cmd);
    }
}

/// Takes the wheel down. True if it was actually up. The page is told first so it can play its
/// closing animation; the window itself goes LEAVE_MS later, unless the wheel was shown again
/// in the meantime.
fn hide(app: &AppHandle) -> bool {
    if let Ok(mut g) = PENDING.lock() {
        *g = None;                      // closed before the page had even loaded: nothing to show any more
    }
    if let Ok(mut c) = PENDING_CMD.lock() {
        *c = None;
    }
    let was_up = UP.swap(false, Ordering::SeqCst);
    if !was_up {
        return false;
    }
    let gen = GEN.fetch_add(1, Ordering::SeqCst) + 1;
    let _ = app.emit_to(LABEL, "wheel-leave", ());
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(LEAVE_MS));
        if GEN.load(Ordering::SeqCst) != gen {
            return;
        }
        if let Some(win) = app.get_webview_window(LABEL) {
            let _ = win.hide();
        }
        // Closed for a good while: give the WebView2 process back (the next open builds it again).
        std::thread::sleep(Duration::from_secs(IDLE_DESTROY_S));
        if GEN.load(Ordering::SeqCst) == gen && !UP.load(Ordering::SeqCst) {
            if let Some(win) = app.get_webview_window(LABEL) {
                let _ = win.destroy();
            }
        }
    });
    true
}

/// Show the launcher. `payload` (the tabs, the current tab, hold mode, theme) goes to the page as is.
/// `at_cursor` is kept for the island's call but no longer moves the window: it is always centred.
#[tauri::command]
pub fn wheel_open(app: AppHandle, at_cursor: bool, payload: Value) {
    GEN.fetch_add(1, Ordering::SeqCst);        // cancels a hide (or the idle destroy) that is still waiting
    let Some(win) = app.get_webview_window(LABEL) else {
        // No window yet (first open, or destroyed after idling): build it; its page shows the wheel once loaded.
        // Built on a thread: building a window from a sync command can deadlock on Windows.
        if cfg!(windows) {
            if let Ok(mut g) = PENDING.lock() {
                *g = Some(payload);
            }
            std::thread::spawn(move || build(&app));
        }
        return;
    };
    show_now(&app, &win, at_cursor, payload);
}

/// Places, shows and fills the wheel window (which exists).
fn show_now(app: &AppHandle, win: &WebviewWindow, at_cursor: bool, payload: Value) {
    place(app, win, at_cursor);
    let _ = win.set_always_on_top(true);
    let _ = win.show();
    UP.store(true, Ordering::SeqCst);
    let _ = app.emit_to(LABEL, "wheel-open", payload);
    watch(app);
}

/// Hide the wheel without a choice.
#[tauri::command]
pub fn wheel_close(app: AppHandle) {
    if hide(&app) {
        let _ = app.emit_to(island::WINDOW_LABEL, "wheel-closed", ());
    }
}

/// The page chose a tab.
#[tauri::command]
pub fn wheel_pick(app: AppHandle, view: String) {
    hide(&app);
    let _ = app.emit_to(island::WINDOW_LABEL, "wheel-pick", view);
}

/// The island forwards "commit" (the shortcut was held and let go) or "stay" (it was only tapped).
#[tauri::command]
pub fn wheel_cmd(app: AppHandle, cmd: String) {
    let waiting = match PENDING.lock() {
        Ok(g) => g.is_some(),
        Err(_) => false,
    };
    if waiting {
        // the window is still loading: keep the command and send it right after the wheel opens
        if let Ok(mut c) = PENDING_CMD.lock() {
            *c = Some(cmd);
        }
        return;
    }
    let _ = app.emit_to(LABEL, "wheel-cmd", cmd);
}

/// While the wheel is up: Esc, or a left click outside the window, closes it.
#[cfg(windows)]
fn watch(app: &AppHandle) {
    if WATCHING.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        // A button that is already down when the wheel opens is not a "click outside".
        let mut was_down = platform::left_button_down();
        while UP.load(Ordering::SeqCst) {
            std::thread::sleep(std::time::Duration::from_millis(16));
            let Some(win) = app.get_webview_window(LABEL) else { break };
            let down = platform::left_button_down();
            let mut dismiss = platform::escape_down();
            if down && !was_down && !dismiss {
                if let (Some((x, y)), Ok(pos), Ok(size)) =
                    (platform::cursor_physical(), win.outer_position(), win.outer_size())
                {
                    dismiss = x < pos.x as f64
                        || y < pos.y as f64
                        || x >= pos.x as f64 + size.width as f64
                        || y >= pos.y as f64 + size.height as f64;
                }
            }
            was_down = down;
            if dismiss {
                wheel_close(app.clone());
                break;
            }
        }
        WATCHING.store(false, Ordering::SeqCst);
    });
}

/// No global key/mouse state off Windows: the page's own Esc and click handling is all there is.
#[cfg(not(windows))]
fn watch(_app: &AppHandle) {
    WATCHING.store(false, Ordering::SeqCst);
}
