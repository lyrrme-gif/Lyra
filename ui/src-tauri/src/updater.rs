//! Lyra - verified updates, Windows side (phase 8).
//!
//! The Python backend does the checking, the download and the first SHA-256 check (backend/updater.py,
//! api_p8.py). This file is the LAST gate before an installer runs, and it trusts nothing it is handed:
//!   * the file must be a regular, non-empty `.exe` that lives DIRECTLY inside `%APPDATA%\Lyra\updates`
//!     (resolved first, so `..`, junctions and symlinks cannot point it somewhere else),
//!   * its SHA-256 is computed here, from the bytes on disk right now, and must equal the hash the backend
//!     verified against the release's published checksum,
//!   * only then is the installer started (detached, per-user NSIS, progress window only) and Lyra quits.
//! No network access here, no elevation, nothing runs unless the user clicked "Install and restart".
//! Non-Windows builds refuse with a clear message.

use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};
use tauri::AppHandle;

/// Same ceiling as backend/updater.py (MAX_INSTALLER_BYTES).
const MAX_INSTALLER_BYTES: u64 = 600 * 1024 * 1024;

/// `%APPDATA%\Lyra\updates` - where backend/paths.py puts `updates/` in an installed build.
fn updates_dir() -> Result<PathBuf, String> {
    let base = std::env::var_os("APPDATA").ok_or_else(|| "APPDATA is not set".to_string())?;
    Ok(PathBuf::from(base).join("Lyra").join("updates"))
}

pub fn is_hex64(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Lower-case hex SHA-256 of a file, streamed (installers are large).
pub fn sha256_file(path: &Path) -> Result<String, String> {
    let mut file = File::open(path).map_err(|e| format!("cannot open the installer: {e}"))?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1024 * 1024];
    loop {
        let n = file.read(&mut buf).map_err(|e| format!("cannot read the installer: {e}"))?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    let digest = hasher.finalize();
    Ok(digest.iter().map(|b| format!("{b:02x}")).collect())
}

/// `candidate` must be a regular, non-empty .exe directly inside `dir`, after resolving links and "..".
pub fn validate_installer_path(dir: &Path, candidate: &Path) -> Result<PathBuf, String> {
    let dir_resolved = dir
        .canonicalize()
        .map_err(|_| "Lyra's updates folder does not exist".to_string())?;
    let file = candidate
        .canonicalize()
        .map_err(|_| "the installer file does not exist".to_string())?;
    if file.parent() != Some(dir_resolved.as_path()) {
        return Err("the installer is not inside Lyra's updates folder".to_string());
    }
    let is_exe = file
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.eq_ignore_ascii_case("exe"))
        .unwrap_or(false);
    if !is_exe {
        return Err("the update file is not an .exe".to_string());
    }
    let meta = std::fs::metadata(&file).map_err(|e| format!("cannot read the installer: {e}"))?;
    if !meta.is_file() || meta.len() == 0 || meta.len() > MAX_INSTALLER_BYTES {
        return Err("the installer has an unexpected size".to_string());
    }
    Ok(file)
}

/// Everything that must be true before the installer may run. Returns the resolved path on success.
pub fn verify(dir: &Path, candidate: &Path, expected_sha256: &str) -> Result<PathBuf, String> {
    if !is_hex64(expected_sha256) {
        return Err("no valid SHA-256 was supplied".to_string());
    }
    let file = validate_installer_path(dir, candidate)?;
    let actual = sha256_file(&file)?;
    if !actual.eq_ignore_ascii_case(expected_sha256) {
        return Err("the installer does not match its verified SHA-256 - not starting it".to_string());
    }
    Ok(file)
}

#[cfg(windows)]
fn launch(exe: &Path) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    const DETACHED_PROCESS: u32 = 0x0000_0008;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    // /P = passive (progress bar, no questions) for the Tauri NSIS installer; the installer's own
    // PREINSTALL hook stops lyra-backend.exe and its POSTINSTALL hook starts Lyra again.
    std::process::Command::new(exe)
        .arg("/P")
        .creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("could not start the installer: {e}"))
}

#[cfg(not(windows))]
fn launch(_exe: &Path) -> Result<(), String> {
    Err("Updates install on Windows only.".to_string())
}

/// Called by Settings -> About after the backend's `/p8/install` returned a verified installer.
/// Re-checks everything, starts the installer, then quits so the installer can replace Lyra's files.
#[tauri::command]
pub fn install_update(app: AppHandle, path: String, sha256: String) -> Result<(), String> {
    let dir = updates_dir()?;
    let exe = verify(&dir, Path::new(&path), &sha256).map_err(|e| {
        crate::log::line(format!("update: refused to start installer: {e}"));
        e
    })?;
    crate::log::line(format!("update: starting verified installer {}", exe.display()));
    launch(&exe).map_err(|e| {
        crate::log::line(format!("update: {e}"));
        e
    })?;
    // Exit lets lib.rs's run-event handler stop the backend cleanly before the installer needs the files.
    app.exit(0);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    // SHA-256("abc")
    const ABC: &str = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lyra-p8-{}-{}", name, std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn write(path: &Path, bytes: &[u8]) {
        let mut f = File::create(path).unwrap();
        f.write_all(bytes).unwrap();
    }

    #[test]
    fn hex64_rules() {
        assert!(is_hex64(ABC));
        assert!(is_hex64(&ABC.to_uppercase()));
        assert!(!is_hex64(""));
        assert!(!is_hex64(&ABC[..63]));
        assert!(!is_hex64(&format!("{}z", &ABC[..63])));
    }

    #[test]
    fn hashes_a_file() {
        let d = scratch("hash");
        let f = d.join("a.exe");
        write(&f, b"abc");
        assert_eq!(sha256_file(&f).unwrap(), ABC);
    }

    #[test]
    fn accepts_a_matching_installer_in_the_updates_folder() {
        let d = scratch("ok");
        let f = d.join("Lyra-1.2.0-setup.exe");
        write(&f, b"abc");
        assert!(verify(&d, &f, ABC).is_ok());
        assert!(verify(&d, &f, &ABC.to_uppercase()).is_ok());
    }

    #[test]
    fn refuses_a_changed_file() {
        let d = scratch("changed");
        let f = d.join("Lyra-1.2.0-setup.exe");
        write(&f, b"abd");
        assert!(verify(&d, &f, ABC).is_err());
    }

    #[test]
    fn refuses_a_bad_or_missing_hash() {
        let d = scratch("nohash");
        let f = d.join("x.exe");
        write(&f, b"abc");
        assert!(verify(&d, &f, "").is_err());
        assert!(verify(&d, &f, "not-a-hash").is_err());
    }

    #[test]
    fn refuses_files_outside_the_updates_folder() {
        let d = scratch("outside");
        let other = scratch("elsewhere");
        let f = other.join("x.exe");
        write(&f, b"abc");
        assert!(verify(&d, &f, ABC).is_err());
    }

    #[test]
    fn refuses_dotdot_and_subfolders() {
        let d = scratch("dotdot");
        let sub = d.join("sub");
        std::fs::create_dir_all(&sub).unwrap();
        write(&sub.join("x.exe"), b"abc");
        assert!(verify(&d, &sub.join("x.exe"), ABC).is_err());
        let outside = d.parent().unwrap().join(format!("lyra-p8-evil-{}.exe", std::process::id()));
        write(&outside, b"abc");
        let sneaky = d.join("..").join(outside.file_name().unwrap());
        assert!(verify(&d, &sneaky, ABC).is_err());
        let _ = std::fs::remove_file(outside);
    }

    #[test]
    fn refuses_non_exe_empty_and_missing() {
        let d = scratch("kinds");
        write(&d.join("x.zip"), b"abc");
        assert!(verify(&d, &d.join("x.zip"), ABC).is_err());
        write(&d.join("e.exe"), b"");
        assert!(verify(&d, &d.join("e.exe"), ABC).is_err());
        assert!(verify(&d, &d.join("nope.exe"), ABC).is_err());
        assert!(verify(&d.join("no-such-dir"), &d.join("e.exe"), ABC).is_err());
    }
}
