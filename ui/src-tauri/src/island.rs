// Island window: placement on the chosen display, the two window sizes
// (full panel / invisible wake strip), click-through and the cursor poll.
//
// There is no notch on a PC, so the island is a black shape drawn at the top
// centre of the main display inside a borderless, transparent, always-on-top
// window that never takes focus.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, Monitor, PhysicalPosition, PhysicalSize, WebviewWindow};

use crate::platform::{self, cursor_physical, left_button_down};

/// Logical size of the full window — the largest island view, like the macOS panel.
pub const PANEL_W: f64 = 720.0;
pub const PANEL_H: f64 = 320.0;
/// Logical size of the invisible strip that wakes the island when it is hidden.
pub const STRIP_W: f64 = 320.0;
pub const STRIP_H: f64 = 14.0;

pub const WINDOW_LABEL: &str = "island";

/// Margin around the island that still counts as "on the island", in logical px.
/// Tight while the small notch is showing, so the pixels around it stay clickable
/// (browser tabs, window buttons); a little wider once the panel is open.
const HIT_MARGIN_COMPACT: f64 = 4.0;
const HIT_MARGIN_PANEL: f64 = 12.0;
/// Anything shorter than this is the compact notch, not the open panel.
const COMPACT_MAX_H: f64 = 80.0;

fn hit_margin(r: &IslandRect) -> f64 {
    if r.h < COMPACT_MAX_H { HIT_MARGIN_COMPACT } else { HIT_MARGIN_PANEL }
}

/// Margin around the dock and other secondary shapes.
const HIT_MARGIN_EXTRA: f64 = 4.0;

fn rect_hit(r: &IslandRect, m: f64, x: f64, y: f64) -> bool {
    r.w > 0.0 && x >= r.x - m && x <= r.x + r.w + m && y >= r.y - m && y <= r.y + r.h + m
}

/// Is the cursor over something Lyra draws right now? Used when the UI pushes a
/// new shape, so the window is only re-armed when it really is under the cursor.
pub fn cursor_on_hit(app: &AppHandle, gate: &PollGate) -> bool {
    let Some(win) = window(app) else { return false };
    let Ok(origin) = win.outer_position() else { return false };
    let scale = win.scale_factor().unwrap_or(1.0);
    let Some((cx, cy)) = cursor_physical() else { return false };
    gate.hit((cx - origin.x as f64) / scale, (cy - origin.y as f64) / scale)
}

#[derive(Serialize, Clone)]
pub struct CursorPayload {
    pub x: f64,
    pub y: f64,
}

#[derive(Serialize, Clone)]
pub struct ScreenInfo {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub scale: f64,
}

/// The island shape in window-logical coordinates, pushed by the front end.
/// The poll thread owns the click-through decision so it lands in the same 16 ms
/// tick as the cursor read — an IPC round trip here loses clicks.
#[derive(Clone, Copy, Default, Deserialize)]
pub struct IslandRect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

/// Wakes / parks the cursor poll thread so a hidden island costs literally nothing.
pub struct PollGate {
    active: Mutex<bool>,
    cv: Condvar,
    pub collapsed: AtomicBool,
    pub rect: Mutex<IslandRect>,
    /// Further interactive shapes (tab dock, secondary islands). Everything else
    /// in the window — including the gaps between these shapes — is click-through.
    pub extra: Mutex<Vec<IslandRect>>,
    /// When enabled, the full expanded webview stays interactive so Windows OLE
    /// file drags can enter the clipboard tab even though the island normally
    /// uses click-through outside its painted shape.
    pub drop_target: AtomicBool,
    /// Mirrors the window flag so we only call into the OS when it changes.
    ignoring: AtomicBool,
}

impl PollGate {
    pub fn new() -> Self {
        Self {
            active: Mutex::new(false),
            cv: Condvar::new(),
            collapsed: AtomicBool::new(true),
            rect: Mutex::new(IslandRect::default()),
            extra: Mutex::new(Vec::new()),
            drop_target: AtomicBool::new(false),
            ignoring: AtomicBool::new(false),
        }
    }

    pub fn set_rect(&self, rect: IslandRect) {
        *self.rect.lock().unwrap() = rect;
    }

    pub fn set_extra(&self, extra: Vec<IslandRect>) {
        *self.extra.lock().unwrap() = extra;
    }

    /// True when (x, y) — window-logical — is over the island or any extra shape.
    pub fn hit(&self, x: f64, y: f64) -> bool {
        let r = *self.rect.lock().unwrap();
        if rect_hit(&r, hit_margin(&r), x, y) {
            return true;
        }
        // An open panel (not the little notch) takes the mouse over the whole box that
        // holds the island and its dock, plus a margin. Exact shapes are only kept for the
        // notch, so a slightly stale or offset measurement can no longer leave dead spots
        // on tabs and buttons inside the open panel.
        if r.w > 0.0 && r.h >= COMPACT_MAX_H {
            let (mut x0, mut y0, mut x1, mut y1) = (r.x, r.y, r.x + r.w, r.y + r.h);
            for e in self.extra.lock().unwrap().iter() {
                x0 = x0.min(e.x);
                y0 = y0.min(e.y);
                x1 = x1.max(e.x + e.w);
                y1 = y1.max(e.y + e.h);
            }
            const M: f64 = 28.0;
            if x >= x0 - M && x <= x1 + M && y >= y0 - M && y <= y1 + M {
                return true;
            }
        }
        self.extra
            .lock()
            .unwrap()
            .iter()
            .any(|e| rect_hit(e, HIT_MARGIN_EXTRA, x, y))
    }

    /// Forces the next poll tick to re-apply the flag (after a window resize).
    pub fn forget_ignore_state(&self) {
        self.ignoring.store(false, Ordering::Relaxed);
    }

    /// Marks the native cursor state as interactive so the next poll tick can
    /// safely hand mouse input to the WebView after a geometry update.
    pub fn force_interactive(&self) {
        self.ignoring.store(false, Ordering::Relaxed);
    }

    pub fn set_active(&self, on: bool) {
        let mut guard = self.active.lock().unwrap();
        *guard = on;
        self.cv.notify_all();
    }

    fn wait_until_active(&self) {
        let mut guard = self.active.lock().unwrap();
        while !*guard {
            guard = self.cv.wait(guard).unwrap();
        }
    }

    fn is_active(&self) -> bool {
        *self.active.lock().unwrap()
    }
}

pub fn window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window(WINDOW_LABEL)
}

fn monitor_contains(m: &Monitor, x: f64, y: f64) -> bool {
    let p = m.position();
    let s = m.size();
    x >= p.x as f64
        && x < (p.x + s.width as i32) as f64
        && y >= p.y as f64
        && y < (p.y + s.height as i32) as f64
}

/// "display:2" -> Some(1): the screen's position in the list Windows gives (what Settings shows as Screen 2).
fn display_index(pref: &str) -> Option<usize> {
    pref.strip_prefix("display:")?.trim().parse::<usize>().ok()?.checked_sub(1)
}

/// The display the island lives on: the primary one, the one under the cursor, or a chosen screen.
/// A chosen screen that is not there any more (unplugged) falls back to the primary one.
pub(crate) fn target_monitor(app: &AppHandle, pref: &str) -> Option<Monitor> {
    let monitors = app.available_monitors().ok()?;
    if let Some(m) = display_index(pref).and_then(|i| monitors.get(i)) {
        return Some(m.clone());
    }
    if pref == "cursor" {
        if let Some((cx, cy)) = cursor_physical() {
            if let Some(m) = monitors.iter().find(|m| monitor_contains(m, cx, cy)) {
                return Some(m.clone());
            }
        }
    }
    app.primary_monitor()
        .ok()
        .flatten()
        .or_else(|| monitors.into_iter().next())
}

pub fn screen_info(app: &AppHandle, pref: &str) -> ScreenInfo {
    match target_monitor(app, pref) {
        Some(m) => {
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
        None => ScreenInfo { x: 0.0, y: 0.0, width: 1920.0, height: 1080.0, scale: 1.0 },
    }
}

/// Places and sizes the window. `collapsed` picks the wake strip instead of the panel.
/// Resize the native island window for the full-screen chat surface without
/// changing the normal 720×320 footprint used by every other view.
pub fn set_chat_geometry(app: &AppHandle, pref: &str, tall: bool) {
    let Some(win) = window(app) else { return };
    let Some(m) = target_monitor(app, pref) else { return };
    let scale = m.scale_factor();
    let mp = *m.position();
    let ms = *m.size();
    let logical_w = PANEL_W;
    // The expanded Chat can be 360px for normal conversations or up to 540px for
    // rich-search results (with a knowledge card). Reserve the dock plus breathing
    // room so neither presentation is clipped by the native window.
    let logical_h = if tall {
        620.0
    } else {
        PANEL_H
    };
    let pw = (logical_w * scale).round().max(1.0) as u32;
    let ph = (logical_h * scale).round().max(1.0) as u32;
    let x = mp.x + (ms.width as i32 - pw as i32) / 2;
    let y = mp.y;
    #[cfg(target_os = "linux")]
    let _ = win.set_resizable(true);
    let _ = win.set_size(PhysicalSize::new(pw, ph));
    let _ = win.set_position(PhysicalPosition::new(x, y));
    let _ = win.set_always_on_top(true);
}

pub fn apply_geometry(app: &AppHandle, pref: &str, collapsed: bool) {
    let Some(win) = window(app) else { return };
    let Some(m) = target_monitor(app, pref) else { return };

    let scale = m.scale_factor();
    let mp = *m.position();
    let ms = *m.size();

    let (lw, lh) = if collapsed { (ms.width as f64 / scale, STRIP_H) } else { (PANEL_W, PANEL_H) };
    let pw = (lw * scale).round().max(1.0) as u32;
    let ph = (lh * scale).round().max(1.0) as u32;
    let x = mp.x + (ms.width as i32 - pw as i32) / 2;
    let y = mp.y;

    // GTK never sizes a non-resizable window below its natural size (200 px
    // here), so on Linux the 8 px wake strip would stay a 200 px block. tao
    // re-applies the config's `resizable: false` after the first configure, so
    // this is asked every time, just before the resize. Undecorated, the window
    // still offers the user nothing to resize it by. (Found by @YossiYad, #44.)
    #[cfg(target_os = "linux")]
    let _ = win.set_resizable(true);
    let _ = win.set_size(PhysicalSize::new(pw, ph));
    let _ = win.set_position(PhysicalPosition::new(x, y));
    // Moving across displays can rescale the window: re-assert the physical size.
    let _ = win.set_size(PhysicalSize::new(pw, ph));
    let _ = win.set_always_on_top(true);
}

/// Position, size and scale of the monitor the island lives on. Any change here
/// means the island has to be placed again.
fn current_screen_key(app: &AppHandle) -> Option<(i32, i32, u32, u32, u64)> {
    let pref = app
        .try_state::<crate::Shared>()
        .map(|s| s.settings.lock().unwrap().screen.clone())
        .unwrap_or_else(|| "primary".into());
    let m = target_monitor(app, &pref)?;
    let p = m.position();
    let size = m.size();
    Some((p.x, p.y, size.width, size.height, m.scale_factor().to_bits()))
}

/// Emits `cursor` (window-logical coordinates) at ~60 Hz while the island is
/// visible. Parked on a condvar the rest of the time.
pub fn spawn_cursor_poll(app: AppHandle, gate: Arc<PollGate>) {
    std::thread::spawn(move || {
        crate::log::line("hit-test thread started (build v9: wake-strip + press diagnostics)");
        let mut was_down = false;
        // Hidden island only: the left button went down somewhere OUTSIDE the wake strip and is
        // still held. That is what a file being dragged towards the island looks like. A plain click
        // on the top edge starts inside the strip, so it never arms this and is never intercepted.
        let mut strip_armed = false;
        // Last time the click-through flag was pushed to the OS (see the desync note below).
        let mut last_assert = Instant::now();
        // Remembered across wakes so a display change while hidden is noticed the
        // moment the island comes back.
        let mut last_screen: Option<(i32, i32, u32, u32, u64)> = None;
        // Without a cursor to read (Linux) the loop only watches the display
        // layout, and twice a second is plenty for that: waking at 60 Hz just to
        // find no cursor costs CPU for nothing.
        let (visible_period, screen_every) = if platform::CURSOR_POLL { (16, 30) } else { (500, 1) };
        loop {
            gate.wait_until_active();
            let mut last = (f64::MIN, f64::MIN);
            let mut ticks: u32 = 0;
            // Valorant (and friends) in front: the island must not react at all.
            let mut in_game = false;
            let mut game_was = false;
            while gate.is_active() {
                // The hidden wake strip only needs a light heartbeat; the visible
                // island keeps the faster cadence so clicks and drag/drop remain
                // responsive.
                let period = if gate.collapsed.load(Ordering::Relaxed) { 40 } else { visible_period };
                std::thread::sleep(Duration::from_millis(period));

                // Monitors get plugged in, unplugged, rearranged and rescaled, and
                // an island pinned to coordinates that no longer exist is an island
                // nobody can reach. Checked about twice a second — the cursor poll
                // is already running, so this costs one monitor query.
                ticks = ticks.wrapping_add(1);
                if ticks % 8 == 0 {
                    in_game = platform::game_in_foreground();
                }
                if ticks % screen_every == 0 {
                    let now = current_screen_key(&app);
                    if now.is_some() && now != last_screen {
                        let first = last_screen.is_none();
                        last_screen = now;
                        if !first {
                            crate::log::line("display layout changed — repositioning".to_string());
                            let _ = app.emit_to(WINDOW_LABEL, "screen-changed", ());
                        }
                    }
                }

                let Some(win) = window(&app) else { continue };

                // A game is in front: touching the top edge must not wake the
                // island or hand it the mouse (that is what dropped the player out
                // of the match). The window stays click-through and gets no cursor.
                if in_game {
                    if !game_was {
                        game_was = true;
                        crate::log::line("game in foreground - island ignores the mouse".to_string());
                        let _ = win.emit("cursor", CursorPayload { x: -10_000.0, y: -10_000.0 });
                    }
                    if !gate.ignoring.load(Ordering::Relaxed) {
                        gate.ignoring.store(true, Ordering::Relaxed);
                        let _ = win.set_ignore_cursor_events(true);
                    }
                    continue;
                } else if game_was {
                    game_was = false;
                    last = (f64::MIN, f64::MIN);
                    crate::log::line("game left the foreground - island active again".to_string());
                }

                // Drop target (clipboard) no longer keeps the whole window
                // interactive: that left an invisible 720x320 blocker over the
                // desktop. A file drag holds the left button, which the
                // `dragging` test below already turns into an interactive
                // window, so drops still land while the rest stays click-through.
                let Ok(origin) = win.outer_position() else { continue };
                let scale = win.scale_factor().unwrap_or(1.0);
                let Some((cx, cy)) = cursor_physical() else { continue };
                let x = (cx - origin.x as f64) / scale;
                let y = (cy - origin.y as f64) / scale;
                let size = match win.inner_size() {
                    Ok(s) => (s.width as f64 / scale, s.height as f64 / scale),
                    Err(_) => (PANEL_W, PANEL_H),
                };
                // When the window is collapsed it is the native wake strip. Keep
                // it mouse-visible at all times and let the web UI decide whether
                // the pointer entered the wake zone. This is more reliable on
                // Windows/WebView2 than waiting for a DOM mouseenter on a transparent
                // element, and it also works when the cursor is already at the top
                // edge when the island retracts.
                if gate.collapsed.load(Ordering::Relaxed) {
                    // A file dragged up to the hidden island must be able to land on the strip.
                    // Click-through (WS_EX_TRANSPARENT) hides the window from Windows' drop
                    // hit-test, so without this OLE finds no target ("no drop" cursor) and the page
                    // never sees the drag - the island could not wake for it. So: a drag that ARRIVES
                    // in the middle of the strip (button already held when it got here) makes the
                    // strip take the mouse; the page then opens the Clipboard tab (see island.ts).
                    let down = left_button_down();
                    let in_strip = x >= 0.0 && x <= size.0 && y >= 0.0 && y <= size.1;
                    let near_notch = in_strip && (x - size.0 / 2.0).abs() <= PANEL_W / 2.0;
                    if !down {
                        strip_armed = false;
                    } else if !in_strip {
                        strip_armed = true;
                    }
                    let take_drag = down && strip_armed && near_notch;
                    if take_drag == gate.ignoring.load(Ordering::Relaxed) {
                        gate.ignoring.store(!take_drag, Ordering::Relaxed);
                        let _ = win.set_ignore_cursor_events(!take_drag);
                    }
                    let _ = win.emit("cursor", CursorPayload { x, y });
                    continue;
                }

                // Do not skip hit-testing just because the cursor is stationary.
                // If the native window was click-through during the previous tick,
                // the user can move the pointer onto the island and stop; a click
                // must still be able to re-arm the WebView. The old early-continue
                // left the window permanently click-through until the next movement.
                let cursor_moved = (x - last.0).abs() >= 1.0 || (y - last.1).abs() >= 1.0;
                if cursor_moved {
                    last = (x, y);
                }

                // Click-through: the window only takes the mouse over the island
                // shape. A generous entry margin prevents fast pointer movement
                // from leaving the webview click-through before the click lands.
                let on_island = gate.hit(x, y);

                // A file being dragged has to be able to find us. WS_EX_TRANSPARENT
                // — what click-through is on Windows — hides the window from
                // WindowFromPoint, so OLE finds no drop target and shows the "no
                // drop" cursor. macOS has no such problem: AppKit delivers drags to
                // registered destinations whatever ignoresMouseEvents says. So while
                // a button is held anywhere over the panel, the whole panel takes
                // the mouse, which also makes the drop zone as forgiving as the Mac's.
                // A press may be the start of a drag: make sure the drop target is
                // ours before the file arrives.
                let down = left_button_down();
                if down && !was_down {
                    let handle = app.clone();
                    let _ = app.run_on_main_thread(move || platform::unblock_webview_drops(&handle));
                    // Diagnostics: every press is logged with what the hit-test thought, so a
                    // "dead" click can be explained from %LOCALAPPDATA%\Coucou\coucou.log.
                    let r = *gate.rect.lock().unwrap();
                    crate::log::line(format!(
                        "press at ({:.0},{:.0}) island rect ({:.0},{:.0} {:.0}x{:.0}) hit={} ignoring={} window={:.0}x{:.0} scale={:.2}",
                        x, y, r.x, r.y, r.w, r.h, on_island,
                        gate.ignoring.load(Ordering::Relaxed), size.0, size.1, scale
                    ));
                }
                was_down = down;

                let dragging = down
                    && x >= 0.0
                    && x <= size.0
                    && y >= 0.0
                    && y <= size.1;

                let accept = on_island || dragging;
                // `ignoring` is only our own bookkeeping. Windows can drop WS_EX_TRANSPARENT's
                // counterpart (or keep it) when the window is resized / repositioned / re-topmosted,
                // leaving the real state different from the flag - the panel then looks fine but
                // swallows no clicks ("dead" tabs and buttons) until something toggles it again.
                // So while the cursor is over the island, re-assert "interactive" every 250 ms.
                let reassert = accept && last_assert.elapsed() >= Duration::from_millis(250);
                if gate.ignoring.load(Ordering::Relaxed) == accept || reassert {
                    gate.ignoring.store(!accept, Ordering::Relaxed);
                    let _ = win.set_ignore_cursor_events(!accept);
                    last_assert = Instant::now();
                }

                if cursor_moved {
                    let _ = win.emit("cursor", CursorPayload { x, y });
                }
            }
        }
    });
}

/// Re-applies click-through after the window or the island changed shape.
///
/// With the cursor poll (Windows) the window takes the mouse again and the next
/// tick decides from the cursor. Without it (Linux) the input region is set to
/// the island itself, or to the whole wake strip while collapsed.
pub fn refresh_click_through(app: &AppHandle, gate: &PollGate) {
    if platform::CURSOR_POLL {
        set_ignore_cursor(app, false);
        gate.forget_ignore_state();
        return;
    }
    let Some(win) = window(app) else { return };
    let region = if gate.collapsed.load(Ordering::Relaxed) {
        // The wake strip itself, never "the whole window": if the window ever
        // fails to shrink to the strip, the rest of it must not swallow clicks
        // meant for whatever sits under the top of the screen.
        Some((0.0, 0.0, STRIP_W, STRIP_H))
    } else {
        let r = *gate.rect.lock().unwrap();
        if r.w <= 0.0 {
            // Nothing drawn yet: nothing takes the mouse.
            Some((0.0, 0.0, 0.0, 0.0))
        } else {
            // The input region API takes one box: use the union of the island
            // and the extra shapes (Windows uses the exact shapes instead).
            let m = hit_margin(&r);
            let mut x0 = (r.x - m).max(0.0);
            let mut y0 = (r.y - m).max(0.0);
            let mut x1 = r.x + r.w + m;
            let mut y1 = r.y + r.h + m;
            for e in gate.extra.lock().unwrap().iter().filter(|e| e.w > 0.0) {
                x0 = x0.min((e.x - HIT_MARGIN_EXTRA).max(0.0));
                y0 = y0.min((e.y - HIT_MARGIN_EXTRA).max(0.0));
                x1 = x1.max(e.x + e.w + HIT_MARGIN_EXTRA);
                y1 = y1.max(e.y + e.h + HIT_MARGIN_EXTRA);
            }
            Some((x0, y0, x1 - x0, y1 - y0))
        }
    };
    platform::set_input_region(&win, region);
}

pub fn set_ignore_cursor(app: &AppHandle, ignore: bool) {
    if let Some(win) = window(app) {
        let _ = win.set_ignore_cursor_events(ignore);
    }
}
