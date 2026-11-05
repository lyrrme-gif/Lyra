// The mascot window: a transparent, click-through, always-on-top overlay that covers
// one whole monitor. The character itself is drawn by src/mascot (TypeScript).
//
// Three jobs live here:
//   1. Window life cycle. A control thread asks the backend (GET /settings/features)
//      whether `feature_mascot` is on, creates or destroys the window to match, hides it
//      while a full-screen app/game is in front, and keeps it on the right monitor.
//   2. Cursor feed + click-through. A second thread reads the cursor (same platform call
//      the island poll uses), streams it to the page as `mascot-cursor` events (~60 Hz while the
//      character is moving, ~10 Hz otherwise), and turns click-through off only while
//      the cursor is over a rectangle the page reported (the character or its menu).
//   3. Commands used by the page: mascot_boot, mascot_set_rects, mascot_set_active,
//      mascot_monitor, mascot_open_chat, mascot_raise, mascot_carry.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::Value;
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};

use crate::island::{self, CursorPayload, IslandRect, ScreenInfo};
use crate::platform;

pub const LABEL: &str = "mascot";

/// Which monitor the window covers.
const MODE_CURSOR: u8 = 0; // the one the cursor is on (following)
const MODE_ISLAND: u8 = 1; // the island's monitor (goto / point / tour / home use normalized coordinates on it)
const MODE_HOLD: u8 = 2; // stay where it is (character parked)

type ScreenKey = (i32, i32, u32, u32, u64);

struct State {
    /// Window-logical rectangles that take the mouse (character, open menu).
    rects: Mutex<Vec<IslandRect>>,
    /// The character is moving: stream the cursor at full rate.
    active: AtomicBool,
    mode: AtomicU8,
    /// `feature_mascot` as last read from the backend.
    enabled: AtomicBool,
    /// A full-screen app or game is in front: window hidden.
    suspended: AtomicBool,
    /// One of the known games (see platform::game_in_foreground) is in front: the
    /// Mochi plays with a controller and the window never takes the mouse.
    gaming: AtomicBool,
    /// Settings → Mochi → Auto-hide says the window must not be on screen right now.
    autohidden: AtomicBool,
    prefs: Mutex<Value>,
    screen: Mutex<Option<(ScreenKey, ScreenInfo)>>,
    started: AtomicBool,
    /// Someone just saved prefs: re-read them now instead of waiting for the slow safety poll.
    refresh: AtomicBool,
    /// The Mochi is being dragged out of the island's Mochi tab: the press began in the ISLAND window, so this
    /// window never sees the release. The first tick the button is up, "mascot-drop" tells the page where.
    carry: AtomicBool,
}

fn st() -> &'static State {
    static S: OnceLock<State> = OnceLock::new();
    S.get_or_init(|| State {
        rects: Mutex::new(Vec::new()),
        active: AtomicBool::new(false),
        mode: AtomicU8::new(MODE_ISLAND),
        enabled: AtomicBool::new(false),
        suspended: AtomicBool::new(false),
        gaming: AtomicBool::new(false),
        autohidden: AtomicBool::new(false),
        prefs: Mutex::new(Value::Null),
        screen: Mutex::new(None),
        started: AtomicBool::new(false),
        refresh: AtomicBool::new(true),
        carry: AtomicBool::new(false),
    })
}

// ── Start-up ──────────────────────────────────────────────────────────────────

/// Called once from lib.rs setup (Windows only). Both threads idle cheaply while
/// the feature is off.
pub fn start(app: &AppHandle) {
    if st().started.swap(true, Ordering::SeqCst) {
        return;
    }
    let a = app.clone();
    std::thread::spawn(move || control_loop(a));
    let b = app.clone();
    std::thread::spawn(move || cursor_loop(b));
}

// ── Window ────────────────────────────────────────────────────────────────────

fn island_pref(app: &AppHandle) -> String {
    app.try_state::<crate::Shared>()
        .map(|s| s.settings.lock().unwrap().screen.clone())
        .unwrap_or_else(|| "primary".into())
}

fn create(app: &AppHandle) {
    let built = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("mascot.html".into()))
        .title("Lyra mascot")
        .inner_size(800.0, 600.0)
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
        crate::log::line("mascot: could not build the window");
        return;
    };
    let _ = win.set_ignore_cursor_events(true);
    platform::make_non_activating(&win);
    st().rects.lock().unwrap().clear();
    st().suspended.store(false, Ordering::Relaxed);
    st().autohidden.store(false, Ordering::Relaxed); // the control loop hides it again if needed
    place(app, &win, true);
    let _ = win.show();
    crate::log::line("mascot: window created");
}

fn destroy(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(LABEL) {
        let _ = win.destroy();
    }
    *st().screen.lock().unwrap() = None;
    st().rects.lock().unwrap().clear();
    st().active.store(false, Ordering::Relaxed);
    crate::log::line("mascot: window removed");
}

fn screen_info(m: &tauri::Monitor) -> ScreenInfo {
    let scale = m.scale_factor();
    let p = m.position();
    let s = m.size();
    ScreenInfo {
        x: p.x as f64 / scale,
        y: p.y as f64 / scale,
        width: s.width as f64 / scale,
        height: s.height as f64 / scale,
        scale,
    }
}

/// Covers the monitor chosen by the current mode. Does nothing when that monitor
/// (position, size, scale) is already the one covered, unless `force`.
fn place(app: &AppHandle, win: &WebviewWindow, force: bool) {
    let mode = st().mode.load(Ordering::Relaxed);
    if mode == MODE_HOLD && !force {
        return;
    }
    let monitor = if mode == MODE_CURSOR {
        island::target_monitor(app, "cursor")
    } else {
        island::target_monitor(app, &island_pref(app))
    };
    let Some(m) = monitor else { return };
    let mp = *m.position();
    let ms = *m.size();
    let key: ScreenKey = (mp.x, mp.y, ms.width, ms.height, m.scale_factor().to_bits());
    let info = screen_info(&m);
    {
        let mut cur = st().screen.lock().unwrap();
        if !force && matches!(&*cur, Some((k, _)) if *k == key) {
            return;
        }
        *cur = Some((key, info.clone()));
    }
    let _ = win.set_size(PhysicalSize::new(ms.width, ms.height));
    let _ = win.set_position(PhysicalPosition::new(mp.x, mp.y));
    // Moving across displays can rescale the window: re-assert the physical size.
    let _ = win.set_size(PhysicalSize::new(ms.width, ms.height));
    let _ = win.set_always_on_top(true);
    let _ = app.emit_to(LABEL, "mascot-screen", info);
}

// ── Control thread: switch, full-screen, monitor ──────────────────────────────

/// Reads the "features" settings group from the backend with a bare HTTP/1.0 GET
/// (no HTTP client in the dependency list). None while the backend is down.
fn fetch_prefs() -> Option<Value> {
    let addr: SocketAddr = "127.0.0.1:8765".parse().ok()?;
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_millis(300)).ok()?;
    s.set_read_timeout(Some(Duration::from_millis(700))).ok()?;
    s.set_write_timeout(Some(Duration::from_millis(300))).ok()?;
    s.write_all(b"GET /settings/features HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .ok()?;
    let mut buf = Vec::new();
    let mut chunk = [0u8; 2048];
    loop {
        match s.read(&mut chunk) {
            Ok(0) | Err(_) => break,
            Ok(n) => buf.extend_from_slice(&chunk[..n]),
        }
    }
    let text = String::from_utf8_lossy(&buf).to_string();
    let body = text.split("\r\n\r\n").nth(1)?;
    serde_json::from_str::<Value>(body.trim()).ok()
}

fn control_loop(app: AppHandle) {
    // /settings/features is only re-read when something says it changed (a save from the tray or the
    // UI sets `refresh`), plus a slow safety poll in case the backend restarted or a pref was edited
    // by hand. It used to be asked every 2 s forever, which flooded the backend log.
    const SAFETY_POLL: Duration = Duration::from_secs(60);
    const RETRY_WHILE_DOWN: Duration = Duration::from_secs(3);
    let mut last_fetch = Instant::now() - Duration::from_secs(600);
    let mut have_settings = false;
    loop {
        std::thread::sleep(Duration::from_millis(250));

        let due = if have_settings { SAFETY_POLL } else { RETRY_WHILE_DOWN };
        if st().refresh.swap(false, Ordering::Relaxed) || last_fetch.elapsed() >= due {
            last_fetch = Instant::now();
            if let Some(p) = fetch_prefs() {
                let on = p.get("feature_mascot").and_then(Value::as_bool).unwrap_or(false);
                st().enabled.store(on, Ordering::Relaxed);
                have_settings = true;
                let changed = {
                    let mut cur = st().prefs.lock().unwrap();
                    if *cur != p {
                        *cur = p.clone();
                        true
                    } else {
                        false
                    }
                };
                if changed {
                    let _ = app.emit_to(LABEL, "mascot-prefs", p);
                }
            }
        }

        // The switch: the window exists exactly while the feature is on.
        let want = have_settings && st().enabled.load(Ordering::Relaxed);
        let exists = app.get_webview_window(LABEL).is_some();
        if want && !exists {
            create(&app);
        } else if !want && exists {
            destroy(&app);
        }
        // A game in front? The page (and the notch Mochi in the island window) switch to the
        // "gaming" mood, and cursor_loop below stops this window from ever taking a click.
        let in_game = platform::game_in_foreground();
        if in_game != st().gaming.swap(in_game, Ordering::Relaxed) {
            let _ = app.emit_to(LABEL, "mascot-game", in_game);
            let _ = app.emit_to(crate::island::WINDOW_LABEL, "mascot-game", in_game);
            crate::log::line(format!(
                "game {} - mascot {}",
                if in_game { "in front" } else { "left the foreground" },
                if in_game { "plays and ignores the mouse" } else { "active again" }
            ));
        }
        let Some(win) = app.get_webview_window(LABEL) else { continue };

        // Screen sharing: take the window off the screen altogether, so a hidden Mochi costs nothing
        // and can never catch a click. (The Mochi has no auto-hide setting any more.)
        let mode = "always";
        let hide = crate::autohide::hidden(mode, crate::autohide::current());
        if hide != st().autohidden.swap(hide, Ordering::Relaxed) {
            if hide {
                let _ = win.hide();
            } else {
                let _ = win.show();
                let _ = win.set_always_on_top(true);
            }
            crate::log::line(format!("mascot: auto-hide ({mode}) -> {}", if hide { "hidden" } else { "shown" }));
        }
        if hide {
            continue;
        }

        // Keep the mascot visible while full-screen apps are active too. The old
        // notification-state heuristic fluctuated on some Windows setups and
        // repeatedly hid and showed the overlay.
        if st().suspended.swap(false, Ordering::Relaxed) {
            let _ = win.show();
            let _ = win.set_always_on_top(true);
            let _ = app.emit_to(LABEL, "mascot-suspend", false);
        }

        // Multi-monitor: while following, cover the monitor the cursor is on.
        place(&app, &win, false);
    }
}

// ── Cursor thread: cursor feed + click-through ────────────────────────────────

fn rect_hit(r: &IslandRect, margin: f64, x: f64, y: f64) -> bool {
    r.w > 0.0 && x >= r.x - margin && x <= r.x + r.w + margin && y >= r.y - margin && y <= r.y + r.h + margin
}

fn cursor_loop(app: AppHandle) {
    let mut ignoring = true; // mirrors the window flag: the OS is only called when it changes
    let mut was_down = false;
    let mut grab = false; // a press began on the character: keep the mouse until release
    let mut last = (f64::MIN, f64::MIN);
    let mut last_window = None;
    let mut last_emit = Instant::now();
    let mut last_assert = Instant::now();
    loop {
        let active = st().active.load(Ordering::Relaxed);
        std::thread::sleep(Duration::from_millis(if active { 16 } else { 50 }));
        if st().suspended.load(Ordering::Relaxed) || st().autohidden.load(Ordering::Relaxed) {
            continue;
        }
        let Some(win) = app.get_webview_window(LABEL) else {
            ignoring = true;
            was_down = false;
            grab = false;
            std::thread::sleep(Duration::from_millis(250));
            continue;
        };
        let Ok(origin) = win.outer_position() else { continue };
        let scale = win.scale_factor().unwrap_or(1.0);
        let Some((cx, cy)) = platform::cursor_physical() else { continue };
        // Carrying the Mochi out of the island: the button went up, so this is where it was let go (checked before
        // the monitor test below, so a release anywhere is reported; the page clamps it onto its own screen).
        if st().carry.load(Ordering::Relaxed) && !platform::left_button_down() {
            st().carry.store(false, Ordering::Relaxed);
            let _ = app.emit_to(
                LABEL,
                "mascot-drop",
                CursorPayload { x: (cx - origin.x as f64) / scale, y: (cy - origin.y as f64) / scale },
            );
        }
        // The control thread may still be moving the overlay between displays.
        // Don't interpret a cursor position from a different display using this
        // window's old origin; overlapping desktop coordinates can otherwise
        // produce plausible but badly offset local positions.
        let Some(monitor) = win.current_monitor().ok().flatten() else { continue };
        let mp = monitor.position();
        let ms = monitor.size();
        if cx < mp.x as f64
            || cy < mp.y as f64
            || cx >= (mp.x as f64 + ms.width as f64)
            || cy >= (mp.y as f64 + ms.height as f64)
        {
            // In cursor-follow mode, the control thread normally moves the
            // overlay on its next 250 ms tick. Don't drop all cursor samples
            // during that interval: move it from this same cursor observation.
            if st().mode.load(Ordering::Relaxed) == MODE_CURSOR {
                place(&app, &win, false);
            }
            continue;
        }
        let x = (cx - origin.x as f64) / scale;
        let y = (cy - origin.y as f64) / scale;
        let window_key = (origin.x, origin.y, scale.to_bits());
        let window_changed = last_window != Some(window_key);
        last_window = Some(window_key);

        let over = {
            let rects = st().rects.lock().unwrap();
            rects.iter().any(|r| rect_hit(r, 6.0, x, y))
        };
        let down = platform::left_button_down();
        if down && !was_down {
            grab = over;
        }
        if !down {
            grab = false;
        }
        was_down = down;

        // Same re-assert as the island poll: Windows can lose the flag after a move/resize.
        // While a game is in front the window never accepts the mouse: a click on the Mochi
        // must reach the game, exactly like the island.
        let accept = (over || grab) && !st().gaming.load(Ordering::Relaxed);
        let reassert = accept && last_assert.elapsed() >= Duration::from_millis(250);
        if ignoring == accept || reassert {
            ignoring = !accept;
            let _ = win.set_ignore_cursor_events(!accept);
            last_assert = Instant::now();
        }

        // ~60 Hz while the character moves, ~10 Hz otherwise (still enough to wake it).
        let moved = window_changed || (x - last.0).abs() >= 1.0 || (y - last.1).abs() >= 1.0;
        if moved && (active || last_emit.elapsed() >= Duration::from_millis(100)) {
            last = (x, y);
            last_emit = Instant::now();
            let _ = app.emit_to(LABEL, "mascot-cursor", CursorPayload { x, y });
        }
    }
}

// ── Commands used by the page ─────────────────────────────────────────────────

#[derive(Serialize)]
pub struct Boot {
    screen: ScreenInfo,
    prefs: Value,
    suspended: bool,
}

#[tauri::command]
pub fn mascot_boot(app: AppHandle) -> Boot {
    let screen = st()
        .screen
        .lock()
        .unwrap()
        .as_ref()
        .map(|(_, info)| info.clone())
        .unwrap_or_else(|| island::screen_info(&app, &island_pref(&app)));
    Boot {
        screen,
        prefs: st().prefs.lock().unwrap().clone(),
        suspended: st().suspended.load(Ordering::Relaxed),
    }
}

/// The UI saved /settings/features: re-read it right away (called from featuresCache.postFeatures).
#[tauri::command]
pub fn mascot_refresh_prefs() {
    st().refresh.store(true, Ordering::Relaxed);
}

#[tauri::command]
pub fn mascot_set_rects(rects: Vec<IslandRect>) {
    *st().rects.lock().unwrap() = rects;
}

#[tauri::command]
pub fn mascot_set_active(active: bool) {
    st().active.store(active, Ordering::Relaxed);
}

#[tauri::command]
pub fn mascot_monitor(app: AppHandle, mode: String) {
    let m = match mode.as_str() {
        "cursor" => MODE_CURSOR,
        "hold" => MODE_HOLD,
        _ => MODE_ISLAND,
    };
    st().mode.store(m, Ordering::Relaxed);
    if m != MODE_HOLD {
        if let Some(win) = app.get_webview_window(LABEL) {
            place(&app, &win, false);
        }
    }
}

#[tauri::command]
pub fn mascot_open_chat(app: AppHandle) {
    // The island's tray handler (src/main.ts) opens the Chat tab on "chat".
    let _ = app.emit_to(island::WINDOW_LABEL, "tray", "chat".to_string());
}

/// Puts the overlay above the island (and everything else that is always on top), so the Mochi is drawn over the
/// island while it is dragged to or from it. Off and on again, because asking for "on top" twice does nothing.
#[tauri::command]
pub fn mascot_raise(app: AppHandle) {
    if let Some(win) = app.get_webview_window(LABEL) {
        let _ = win.set_always_on_top(false);
        let _ = win.set_always_on_top(true);
    }
}

/// The page took over a Mochi that the user is dragging out of the island's Mochi tab (`on`), or is done with it.
/// While on, the cursor thread reports the release as "mascot-drop".
#[tauri::command]
pub fn mascot_carry(app: AppHandle, on: bool) {
    st().carry.store(on, Ordering::Relaxed);
    if on {
        st().active.store(true, Ordering::Relaxed); // full-rate cursor from the first frame
        mascot_raise(app);
    }
}
