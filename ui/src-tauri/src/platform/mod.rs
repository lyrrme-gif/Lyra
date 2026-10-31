// Everything that differs between operating systems, behind one set of names.
//
// The rest of the app calls `platform::…` and never touches Win32 or a Linux
// API directly. Each OS file exposes the same functions; the compiler picks one.

use std::path::PathBuf;

#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub use self::windows::*;

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub use self::linux::*;

/// Wall-clock time in the user's time zone, for log lines and backup names.
pub struct LocalTime {
    pub year: u32,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
}

/// What the window in front is, for the Island / Mochi auto-hide options.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Foreground {
    /// Nothing focused, or the desktop / taskbar / tray.
    Desktop,
    /// An app window. `covering` = maximized or full-screen (it hides the desktop).
    App { covering: bool },
    /// One of our own windows (island, settings, chat bar): says nothing about the user's context.
    Own,
}

/// The user's home directory, where `.claude/settings.json` lives.
pub fn home_dir() -> PathBuf {
    std::env::var_os(HOME_VAR)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}
