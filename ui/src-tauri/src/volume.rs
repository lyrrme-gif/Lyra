// Speaker volume feed for the island's volume HUD.
//
// One small thread reads the master volume of the default playback device about
// eleven times a second (a single COM call each) and tells the island window
// only when it changes — by key, slider or the Windows mixer alike. The first
// reading is just the baseline, so the HUD never pops up at start-up.

use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter};

use crate::island::WINDOW_LABEL;
use crate::platform::VolumeReader;

#[derive(Serialize, Clone)]
struct VolumePayload {
    /// 0.0 ..= 1.0
    level: f32,
    muted: bool,
}

pub fn start(app: &AppHandle) {
    let app = app.clone();
    let spawned = std::thread::Builder::new()
        .name("lyra-volume".into())
        .spawn(move || {
            let mut reader = VolumeReader::new();
            let mut last: Option<(f32, bool)> = None;
            loop {
                std::thread::sleep(Duration::from_millis(90));
                let Some((level, muted)) = reader.read() else { continue };
                let changed = match last {
                    None => false, // baseline
                    Some((l, m)) => (l - level).abs() >= 0.004 || m != muted,
                };
                last = Some((level, muted));
                if changed {
                    let _ = app.emit_to(WINDOW_LABEL, "volume", VolumePayload { level, muted });
                }
            }
        });
    if let Err(err) = spawned {
        crate::log::line(format!("volume HUD: could not start: {err}"));
    }
}
