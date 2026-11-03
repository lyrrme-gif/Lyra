// Windows: Win32 for the island window and the cursor, %APPDATA% for files.

use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;

use tauri::{AppHandle, WebviewWindow};

use ::windows::core::PWSTR;
use ::windows::Win32::Foundation::{CloseHandle, HANDLE, HLOCAL, HWND, LocalFree, POINT};
use ::windows::Win32::Security::Authorization::ConvertSidToStringSidW;
use ::windows::Win32::Security::{GetTokenInformation, TokenUser, TOKEN_QUERY, TOKEN_USER};
use ::windows::Win32::Media::Audio::Endpoints::IAudioEndpointVolume;
use ::windows::Win32::Media::Audio::{eMultimedia, eRender, IMMDeviceEnumerator, MMDeviceEnumerator};
use ::windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CLSCTX_ALL, COINIT_MULTITHREADED};
use ::windows::Win32::System::SystemInformation::GetLocalTime;
use ::windows::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
use ::windows::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_ESCAPE, VK_LBUTTON};
use ::windows::Win32::UI::WindowsAndMessaging::{
    GetCursorPos, GetForegroundWindow, GetWindowLongPtrW, SetForegroundWindow, SetWindowLongPtrW,
    GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
};

use super::{Foreground, LocalTime};

/// True while a full-screen app, game or presentation is in front.
pub fn fullscreen_app_in_foreground() -> bool {
    use ::windows::Win32::UI::Shell::{SHQueryUserNotificationState, QUNS_BUSY, QUNS_PRESENTATION_MODE, QUNS_RUNNING_D3D_FULL_SCREEN};
    unsafe {
        matches!(SHQueryUserNotificationState(), Ok(QUNS_BUSY | QUNS_PRESENTATION_MODE | QUNS_RUNNING_D3D_FULL_SCREEN))
    }
}

/// File name of the Claude Code relay.
pub const HOOK_EXE: &str = "lyra-hook.exe";

/// Environment variable holding the home directory.
pub const HOME_VAR: &str = "USERPROFILE";

/// Keeps spawned helpers from flashing a console window.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

// ── Files ─────────────────────────────────────────────────────────────────────

/// %APPDATA%\Lyra — preferences (UI file: ui-settings.json; the backend shares the folder).
pub fn config_dir() -> PathBuf {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("Lyra")
}

/// %LOCALAPPDATA%\Lyra — where lyra-hook.exe, the inbox and the log live.
pub fn local_dir() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("Lyra")
}

/// %APPDATA% and %LOCALAPPDATA% are already private to the user.
pub fn ensure_private_dir(dir: &std::path::Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dir)
}

/// Nothing to set up before the webview starts.
pub fn prepare_environment() {}

pub fn local_time() -> LocalTime {
    let t = unsafe { GetLocalTime() };
    LocalTime {
        year: t.wYear.into(),
        month: t.wMonth.into(),
        day: t.wDay.into(),
        hour: t.wHour.into(),
        minute: t.wMinute.into(),
        second: t.wSecond.into(),
    }
}

// ── Processes ─────────────────────────────────────────────────────────────────

/// Spawned helpers must never flash a console window.
pub fn no_console(cmd: &mut Command) -> &mut Command {
    cmd.creation_flags(CREATE_NO_WINDOW)
}

pub fn open_url(url: &str) {
    let _ = no_console(Command::new("rundll32.exe").args(["url.dll,FileProtocolHandler", url]))
        .spawn();
}

pub fn reveal_folder(path: &str) {
    let _ = Command::new("explorer").arg(path).spawn();
}

/// Our own `where`: walks %PATH% against %PATHEXT%, no shell involved.
/// Rust quotes arguments correctly for `.cmd`/`.bat` targets since 1.77, so
/// spawning `code.cmd` directly is safe.
pub fn find_on_path(stem: &str) -> Option<PathBuf> {
    let exts = std::env::var("PATHEXT").unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".into());
    let dirs = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&dirs) {
        for ext in exts.split(';').filter(|e| !e.is_empty()) {
            let candidate = dir.join(format!("{stem}{}", ext.to_lowercase()));
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

// ── Who we are ────────────────────────────────────────────────────────────────
//
// Named pipes share one machine-wide namespace, so the SID in the name is what
// keeps two accounts on the same machine from ever meeting on `lyra-*`.
// lyra-hook computes the same string (hook/src/win.rs) and additionally checks
// that the process serving the pipe really is us.

/// The SID of the account this process runs as, as `S-1-5-21-…`.
pub fn current_user_sid() -> Option<String> {
    unsafe {
        let mut token = HANDLE::default();
        OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token).ok()?;

        // First call sizes the buffer, second fills it.
        let mut needed = 0u32;
        let _ = GetTokenInformation(token, TokenUser, None, 0, &mut needed);
        if needed == 0 {
            let _ = CloseHandle(token);
            return None;
        }
        let mut buf = vec![0u8; needed as usize];
        let ok = GetTokenInformation(
            token,
            TokenUser,
            Some(buf.as_mut_ptr().cast()),
            needed,
            &mut needed,
        )
        .is_ok();
        let _ = CloseHandle(token);
        if !ok {
            return None;
        }

        let user = &*(buf.as_ptr() as *const TOKEN_USER);
        let mut text = PWSTR::null();
        ConvertSidToStringSidW(user.User.Sid, &mut text).ok()?;
        let sid = text.to_string().ok();
        let _ = LocalFree(Some(HLOCAL(text.0 as *mut _)));
        sid
    }
}

// ── Cursor ────────────────────────────────────────────────────────────────────

/// The 60 Hz poll reads the cursor and flips click-through from it.
pub const CURSOR_POLL: bool = true;

/// Cursor position in physical screen pixels.
pub fn cursor_physical() -> Option<(f64, f64)> {
    let mut p = POINT::default();
    unsafe { GetCursorPos(&mut p).ok()? };
    Some((p.x as f64, p.y as f64))
}

/// True while the left mouse button is held — the only signal we get that a
/// drag might be in flight before it reaches the window.
pub fn left_button_down() -> bool {
    unsafe { (GetAsyncKeyState(VK_LBUTTON.0 as i32) as u16 & 0x8000) != 0 }
}

/// True while Escape is held. The tab wheel window never takes keyboard focus, so it closes on this.
pub fn escape_down() -> bool {
    unsafe { (GetAsyncKeyState(VK_ESCAPE.0 as i32) as u16 & 0x8000) != 0 }
}

// ── Games ─────────────────────────────────────────────────────────────────────

/// Foreground programs the island must never react to. Lower-case file names.
/// Add more here (e.g. "cs2.exe") if the same problem shows up in another game.
const GAME_EXES: &[&str] = &[
    "valorant-win64-shipping.exe", "valorant.exe",
    "cs2.exe", "fortniteclient-win64-shipping.exe", "r5apex.exe", "overwatch.exe",
    "dota2.exe", "league of legends.exe", "gta5.exe", "eldenring.exe", "rocketleague.exe",
];

/// True while the window in front belongs to one of GAME_EXES. Called a few
/// times a second from the cursor poll, so it stays cheap and never panics.
pub fn game_in_foreground() -> bool {
    use ::windows::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    use ::windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};
    unsafe {
        let fg = GetForegroundWindow();
        if fg.0.is_null() {
            return false;
        }
        let mut pid = 0u32;
        GetWindowThreadProcessId(fg, Some(&mut pid));
        if pid == 0 {
            return false;
        }
        let Ok(h) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else {
            return false;
        };
        let mut buf = [0u16; 520];
        let mut len = buf.len() as u32;
        let ok = QueryFullProcessImageNameW(h, PROCESS_NAME_WIN32, PWSTR(buf.as_mut_ptr()), &mut len).is_ok();
        let _ = CloseHandle(h);
        if !ok {
            return false;
        }
        let path = String::from_utf16_lossy(&buf[..len as usize]).to_lowercase();
        let name = path.rsplit('\\').next().unwrap_or("");
        GAME_EXES.contains(&name)
    }
}

// ── Desktop / app / screen sharing (auto-hide) ────────────────────────────────

/// Window classes of the desktop and the shell around it. Focus on one of these = "on the desktop".
const DESKTOP_CLASSES: &[&str] = &[
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
];

/// Which kind of window has the keyboard. Called a few times a second; never panics.
pub fn foreground() -> Foreground {
    use ::windows::Win32::Foundation::RECT;
    use ::windows::Win32::System::Threading::GetCurrentProcessId;
    use ::windows::Win32::UI::WindowsAndMessaging::{
        GetClassNameW, GetSystemMetrics, GetWindowRect, GetWindowThreadProcessId, IsZoomed, SM_CXSCREEN, SM_CYSCREEN,
    };
    unsafe {
        let fg = GetForegroundWindow();
        if fg.0.is_null() {
            return Foreground::Desktop;
        }
        let mut pid = 0u32;
        GetWindowThreadProcessId(fg, Some(&mut pid));
        if pid == GetCurrentProcessId() {
            return Foreground::Own;
        }
        let mut buf = [0u16; 64];
        let n = GetClassNameW(fg, &mut buf).max(0) as usize;
        let class = String::from_utf16_lossy(&buf[..n.min(buf.len())]);
        if DESKTOP_CLASSES.contains(&class.as_str()) {
            return Foreground::Desktop;
        }
        let mut r = RECT::default();
        let fills_screen = GetWindowRect(fg, &mut r).is_ok()
            && r.left <= 0
            && r.top <= 0
            && r.right >= GetSystemMetrics(SM_CXSCREEN)
            && r.bottom >= GetSystemMetrics(SM_CYSCREEN);
        Foreground::App { covering: IsZoomed(fg).as_bool() || fills_screen }
    }
}

/// Visible top-level windows whose title says a screen share is running (lower-case fragments).
/// Windows has no API that says "someone is capturing the screen", so this looks for the little
/// "you are sharing" bars the meeting apps and browsers put up. Add a fragment here when an app is missed.
const SHARING_TITLES: &[&str] = &[
    "is sharing your screen",
    "is sharing a window",
    "is sharing your window",
    "you're sharing your screen",
    "you are sharing your screen",
    "sharing control bar",
    "sharing toolbar",
];

/// True while one of SHARING_TITLES is on screen. Walks the window list in Z-order (no callback needed).
pub fn screen_sharing() -> bool {
    use ::windows::Win32::UI::WindowsAndMessaging::{GetTopWindow, GetWindow, GetWindowTextW, IsWindowVisible, GW_HWNDNEXT};
    unsafe {
        let mut cur = GetTopWindow(None).ok();
        let mut guard = 0u32;
        while let Some(hwnd) = cur {
            guard += 1;
            if guard > 3000 {
                break;
            }
            if IsWindowVisible(hwnd).as_bool() {
                let mut buf = [0u16; 160];
                let n = GetWindowTextW(hwnd, &mut buf).max(0) as usize;
                if n > 0 {
                    let title = String::from_utf16_lossy(&buf[..n.min(buf.len())]).to_lowercase();
                    if SHARING_TITLES.iter().any(|t| title.contains(t)) {
                        return true;
                    }
                }
            }
            cur = GetWindow(hwnd, GW_HWNDNEXT).ok();
        }
        false
    }
}

// ── Island window ─────────────────────────────────────────────────────────────

fn hwnd_of(win: &WebviewWindow) -> Option<HWND> {
    let raw = win.hwnd().ok()?.0 as isize;
    if raw == 0 {
        return None;
    }
    Some(HWND(raw as *mut _))
}

/// Intentionally does nothing.
///
/// The island window runs with Tauri's native file-drop hook switched off
/// (`dragDropEnabled: false` in tauri.conf.json), so WebView2's own OLE drop target
/// is the one that receives Explorer drags and hands them to the page as normal HTML5
/// drag events. Revoking that target here is exactly what used to produce the
/// "not allowed" cursor, so the old revoke-on-every-mouse-press hack is gone. The
/// function stays so the callers in lib.rs / island.rs keep compiling.
pub fn unblock_webview_drops(_app: &AppHandle) {}

/// WS_EX_NOACTIVATE keeps clicks from stealing focus; WS_EX_TOOLWINDOW keeps the
/// island out of Alt-Tab.
pub fn make_non_activating(win: &WebviewWindow) {
    let Some(hwnd) = hwnd_of(win) else { return };
    unsafe {
        let ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        let want = ex | WS_EX_NOACTIVATE.0 as isize | WS_EX_TOOLWINDOW.0 as isize;
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, want);
    }
}

/// Temporarily allow activation so a text field inside the island can be typed in.
pub fn set_activating(win: &WebviewWindow, activating: bool) {
    let Some(hwnd) = hwnd_of(win) else { return };
    unsafe {
        let ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        let want = if activating {
            ex & !(WS_EX_NOACTIVATE.0 as isize)
        } else {
            ex | WS_EX_NOACTIVATE.0 as isize
        };
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, want);
    }
}

/// The window that had the keyboard before the caption chat bar took it (see caption.rs).
static PREV_FOREGROUND: std::sync::atomic::AtomicIsize = std::sync::atomic::AtomicIsize::new(0);

/// Remember which window is in front, so closing the chat bar can hand the keyboard back to it.
pub fn remember_foreground() {
    let fg = unsafe { GetForegroundWindow() };
    PREV_FOREGROUND.store(fg.0 as isize, std::sync::atomic::Ordering::Relaxed);
}

/// Give the keyboard back to the window remembered by `remember_foreground`, but only if
/// `win` still has it (if the user already clicked elsewhere, leave that alone).
pub fn restore_foreground(win: &WebviewWindow) {
    let Some(me) = hwnd_of(win) else { return };
    let prev = PREV_FOREGROUND.swap(0, std::sync::atomic::Ordering::Relaxed);
    if prev == 0 {
        return;
    }
    unsafe {
        if GetForegroundWindow() == me {
            let _ = SetForegroundWindow(HWND(prev as *mut _));
        }
    }
}

/// Click-through here is the poll's WS_EX_TRANSPARENT toggle, not a region.
pub fn set_input_region(_win: &WebviewWindow, _rect: Option<(f64, f64, f64, f64)>) {}

// ── Speaker volume ────────────────────────────────────────────────────────────

/// Reads the master volume of the default playback device. Lives on one thread
/// (COM is initialised there) and re-opens the device every few seconds so a
/// change of default speakers or headphones is followed.
pub struct VolumeReader {
    endpoint: Option<IAudioEndpointVolume>,
    opened: std::time::Instant,
}

impl VolumeReader {
    pub fn new() -> Self {
        unsafe {
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
        }
        Self { endpoint: None, opened: std::time::Instant::now() }
    }

    fn open() -> Option<IAudioEndpointVolume> {
        unsafe {
            let enumerator: IMMDeviceEnumerator =
                CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL).ok()?;
            let device = enumerator.GetDefaultAudioEndpoint(eRender, eMultimedia).ok()?;
            device.Activate::<IAudioEndpointVolume>(CLSCTX_ALL, None).ok()
        }
    }

    /// (level 0.0..=1.0, muted)
    pub fn read(&mut self) -> Option<(f32, bool)> {
        if self.endpoint.is_none() || self.opened.elapsed().as_secs() >= 3 {
            self.endpoint = Self::open();
            self.opened = std::time::Instant::now();
        }
        let ep = self.endpoint.as_ref()?;
        let read = unsafe {
            let level = ep.GetMasterVolumeLevelScalar().ok();
            let muted = ep.GetMute().ok().map(|b| b.as_bool());
            level.zip(muted)
        };
        if read.is_none() {
            self.endpoint = None;
        }
        read
    }
}
