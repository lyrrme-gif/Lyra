//! Start / stop the Python backend (`lyra-backend.exe`) as a child of the UI.
//!
//! Installed build only: in `tauri dev` (debug) we leave the backend alone so
//! the existing `python main.py` workflow keeps working. On start we probe
//! http://127.0.0.1:8765/health; if something is already answering we do not
//! spawn a second copy. On quit we only kill the process we started.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager, RunEvent};

const HEALTH_HOST: &str = "127.0.0.1";
const HEALTH_PORT: u16 = 8765;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Holds the child we spawned (if any). Managed as Tauri state.
pub struct BackendProcess(pub Mutex<Option<Child>>);

/// Set once this UI has started a backend. "Restart backend" in Settings makes the
/// backend relaunch itself, so our Child handle then points at a dead process.
static SPAWNED: AtomicBool = AtomicBool::new(false);

impl BackendProcess {
    pub fn new() -> Self {
        Self(Mutex::new(None))
    }
}

/// True when the local UI bridge answers /health as Lyra.
fn health_ok() -> bool {
    let addr = format!("{HEALTH_HOST}:{HEALTH_PORT}");
    let Ok(mut stream) = TcpStream::connect_timeout(
        &addr.parse().expect("static addr"),
        Duration::from_millis(350),
    ) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(700)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(400)));
    let req = format!(
        "GET /health HTTP/1.0\r\nHost: {HEALTH_HOST}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 512];
    match stream.read(&mut buf) {
        Ok(n) if n > 0 => {
            let text = String::from_utf8_lossy(&buf[..n]);
            text.contains("200")
                && (text.contains("lyra") || text.contains("\"ok\"") || text.contains("service"))
        }
        _ => false,
    }
}

/// Resolve lyra-backend.exe next to the installed app / resource dir.
fn find_backend_exe(app: &AppHandle) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();

    if let Ok(resource) = app.path().resource_dir() {
        // Tauri keeps the path given in "resources" (binaries/lyra-backend/).
        candidates.push(resource.join("binaries").join("lyra-backend").join("lyra-backend.exe"));
        candidates.push(resource.join("lyra-backend").join("lyra-backend.exe"));
        candidates.push(resource.join("lyra-backend.exe"));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("binaries").join("lyra-backend").join("lyra-backend.exe"));
            candidates.push(dir.join("lyra-backend").join("lyra-backend.exe"));
            candidates.push(dir.join("lyra-backend.exe"));
            // Tauri sometimes puts resources under resources/ next to the exe
            candidates.push(dir.join("resources").join("lyra-backend").join("lyra-backend.exe"));
        }
    }

    candidates.into_iter().find(|p| p.is_file())
}

fn spawn_backend(exe: &Path) -> std::io::Result<Child> {
    let work_dir = exe.parent().unwrap_or_else(|| Path::new("."));
    let mut cmd = Command::new(exe);
    cmd.current_dir(work_dir);
    // >>> phase-8 begin
    // The backend's update checker compares releases against the version of the app that started it.
    cmd.env("LYRA_APP_VERSION", env!("CARGO_PKG_VERSION"));
    // <<< phase-8 end
    // Windowed: no console flash. Job is still tied to this process tree so
    // the OS can clean up if the UI is force-killed.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd.spawn()
}

/// Called once from setup. No-op in debug (`tauri dev`) and when the bridge
/// is already up or the backend binary is missing.
pub fn start_if_needed(app: &AppHandle, state: &BackendProcess) {
    if cfg!(debug_assertions) {
        crate::log::line("backend: skip launch (dev / debug build)");
        return;
    }

    if health_ok() {
        crate::log::line("backend: already answering on :8765 — not spawning");
        return;
    }

    let Some(exe) = find_backend_exe(app) else {
        crate::log::line(
            "backend: lyra-backend.exe not found next to the app — start it yourself",
        );
        return;
    };

    match spawn_backend(&exe) {
        Ok(child) => {
            crate::log::line(format!(
                "backend: started {} (pid {})",
                exe.display(),
                child.id()
            ));
            SPAWNED.store(true, Ordering::Relaxed);
            if let Ok(mut guard) = state.0.lock() {
                *guard = Some(child);
            }
        }
        Err(err) => {
            crate::log::line(format!("backend: failed to start {}: {err}", exe.display()));
        }
    }
}

/// Kill only the child we started. Safe to call more than once.
pub fn stop(state: &BackendProcess) {
    // Only when we started one: also stop a copy the backend relaunched by itself.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        if SPAWNED.swap(false, Ordering::Relaxed) {
            let _ = Command::new("taskkill")
                .args(["/F", "/T", "/IM", "lyra-backend.exe"])
                .creation_flags(CREATE_NO_WINDOW)
                .status();
        }
    }
    if let Ok(mut guard) = state.0.lock() {
        if let Some(mut child) = guard.take() {
            let pid = child.id();
            match child.kill() {
                Ok(()) => {
                    let _ = child.wait();
                    crate::log::line(format!("backend: stopped pid {pid}"));
                }
                Err(err) => {
                    crate::log::line(format!("backend: kill pid {pid} failed: {err}"));
                }
            }
        }
    }
}

/// Wire Exit / ExitRequested so quitting the tray stops the backend.
pub fn on_run_event(app: &AppHandle, event: &RunEvent) {
    match event {
        RunEvent::Exit | RunEvent::ExitRequested { .. } => {
            if let Some(state) = app.try_state::<BackendProcess>() {
                stop(&state);
            }
        }
        _ => {}
    }
}
