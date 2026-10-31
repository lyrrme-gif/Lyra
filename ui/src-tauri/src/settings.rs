// Preferences, stored as plain JSON in ui-settings.json under platform::config_dir().

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub sound_enabled: bool,
    pub sound_volume: f64,
    pub auto_close_interval: f64,
    #[serde(default = "default_compact_auto_hide")]
    pub compact_auto_hide_interval: f64,
    pub absence_interval: f64,
    pub active_integrations: Vec<String>,
    /// "primary" = the main display, "cursor" = whichever display the mouse is on, "display:N" = screen N (1-based).
    pub screen: String,
    pub autostart: bool,
    pub hooks_installed: bool,
    #[serde(default = "default_model")]
    pub model: String,
    /// Keep the compact notch on screen all the time. When off, it retracts to the
    /// invisible wake strip after a few idle seconds (unless music or a timer is up).
    /// `default` matters: without it an older settings.json fails to parse and
    /// `load()` would silently reset every preference.
    #[serde(default = "default_true")]
    pub always_show_notch: bool,
    /// "dwell" = rest on the top edge briefly, "instant" = the old behaviour (any hover wakes it).
    /// (An older "double" value is read as "dwell": see `normalize`.)
    #[serde(default = "default_wake_mode")]
    pub wake_mode: String,
    /// Round the inner cards/surfaces used by the island tabs.
    #[serde(default = "default_true")]
    pub rounded_surfaces: bool,
    /// Auto-hide of the island notch: "always" (never) or "desktop" (only on the desktop).
    /// Screen sharing always hides it. (An older "apps" value is read as "always".)
    #[serde(default = "default_auto_hide")]
    pub island_auto_hide: String,
}

fn default_wake_mode() -> String {
    "dwell".to_string()
}

fn default_true() -> bool {
    true
}

fn default_auto_hide() -> String {
    "always".to_string()
}

fn default_compact_auto_hide() -> f64 { 8.0 }

fn default_model() -> String {
    "lyra".to_string()
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            sound_enabled: true,
            sound_volume: 0.12,
            auto_close_interval: 15.0,
            compact_auto_hide_interval: 8.0,
            absence_interval: 180.0,
            active_integrations: vec![],
            screen: "primary".into(),
            autostart: false,
            hooks_installed: false,
            model: default_model(),
            always_show_notch: true,
            wake_mode: default_wake_mode(),
            rounded_surfaces: true,
            island_auto_hide: default_auto_hide(),
        }
    }
}

pub use crate::platform::{config_dir, local_dir};

pub fn hook_exe_path() -> PathBuf {
    local_dir().join("bin").join("lyra-hook")
}

/// ui-settings.json, not settings.json: the Python backend keeps its own
/// settings.json in the same folder.
fn settings_path() -> PathBuf {
    config_dir().join("ui-settings.json")
}

/// One-time copy of the old %APPDATA%\Coucou\settings.json. Never overwrites,
/// never deletes the original.
fn migrate_legacy() {
    let new = settings_path();
    if new.exists() {
        return;
    }
    let Some(parent) = config_dir().parent().map(|p| p.to_path_buf()) else { return };
    let old = parent.join("Coucou").join("settings.json");
    if old.is_file() {
        let _ = std::fs::create_dir_all(config_dir());
        let _ = std::fs::copy(&old, &new);
    }
}

/// Values that older versions wrote and that no longer exist are mapped to their closest survivor.
fn normalize(mut s: Settings) -> Settings {
    if s.wake_mode != "dwell" && s.wake_mode != "instant" {
        s.wake_mode = default_wake_mode();
    }
    if s.island_auto_hide != "desktop" {
        s.island_auto_hide = default_auto_hide();
    }
    s
}

pub fn load() -> Settings {
    migrate_legacy();
    match std::fs::read(settings_path()) {
        Ok(bytes) => normalize(serde_json::from_slice(&bytes).unwrap_or_default()),
        Err(_) => Settings::default(),
    }
}

pub fn save(settings: &Settings) -> std::io::Result<()> {
    let dir = config_dir();
    crate::platform::ensure_private_dir(&dir)?;
    let json = serde_json::to_vec_pretty(settings)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    std::fs::write(settings_path(), json)
}
