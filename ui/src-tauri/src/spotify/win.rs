// Spotify island — Windows backend.
//
// Two independent questions, answered by two independent mechanisms:
//
//   1. "Is the Spotify desktop app open?"  → the process list (ToolHelp snapshot).
//      This never depends on whether music is playing, or on any web API.
//   2. "What is it playing?"               → the Windows media session
//      (System Media Transport Controls, the same thing behind the volume
//      flyout's media card). Spotify publishes title, artist, artwork, play
//      state and timeline there and accepts play/pause/next/previous back.
//
// Why the media session and not the Spotify Web API: it needs no OAuth, no
// client secret, no token storage or refresh, makes no network request, and it
// is the desktop app's own state rather than "the account's last active
// device". Nothing here can leak a credential because there is none.
//
// No Tauri types in this file on purpose — it only needs the `windows` crate.

use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use windows::Media::Control::{
    GlobalSystemMediaTransportControlsSession as Session,
    GlobalSystemMediaTransportControlsSessionManager as Manager,
    GlobalSystemMediaTransportControlsSessionMediaProperties as MediaProperties,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
};
use windows::Storage::Streams::DataReader;
use windows::Win32::Foundation::CloseHandle;
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::WinRT::{RoInitialize, RO_INIT_MULTITHREADED};

use super::model::{data_url, friendly_source, timeline_ms, Media, Status};

/// Process image name of the desktop client (classic installer and Microsoft Store).
const SPOTIFY_EXE: &str = "spotify.exe";

/// Artwork bigger than this is ignored; Spotify's covers are well under 1 MB.
const MAX_ART_BYTES: u64 = 4 * 1024 * 1024;

/// How many polls to keep retrying a missing thumbnail. Spotify publishes the
/// title a moment before the cover, so the first reading often has none.
const ART_RETRIES: u8 = 6;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Control {
    TogglePlayPause,
    Next,
    Previous,
    /// Absolute playback position in Windows TimeSpan ticks (100ns).
    Seek(i64),
    /// Flips shuffle on/off through the media session.
    ToggleShuffle,
}

/// Called once on the monitor thread, before any media-session call.
pub fn init_thread() {
    // Multithreaded apartment: the blocking `.get()` calls below are fine there.
    // An error only means the thread is already initialised.
    let _ = unsafe { RoInitialize(RO_INIT_MULTITHREADED) };
}

// ── 1. Is Spotify open? ─────────────────────────────────────────────────────

fn is_spotify_exe(name: &[u16]) -> bool {
    let len = name.iter().position(|&c| c == 0).unwrap_or(name.len());
    String::from_utf16_lossy(&name[..len]).eq_ignore_ascii_case(SPOTIFY_EXE)
}

/// True while any `Spotify.exe` process exists — whether the window is
/// focused, minimised, parked in the tray, or music is stopped.
pub fn spotify_running() -> bool {
    unsafe {
        let Ok(snapshot) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else {
            return false;
        };
        let mut entry = PROCESSENTRY32W {
            dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        let mut found = false;
        if Process32FirstW(snapshot, &mut entry).is_ok() {
            loop {
                if is_spotify_exe(&entry.szExeFile) {
                    found = true;
                    break;
                }
                if Process32NextW(snapshot, &mut entry).is_err() {
                    break;
                }
            }
        }
        let _ = CloseHandle(snapshot);
        found
    }
}

// ── 2. What is it playing? ──────────────────────────────────────────────────

/// Owns the media-session manager and the artwork cache. Created when Spotify
/// opens and dropped when it closes, so nothing outlives the app it describes.
pub struct Reader {
    manager: Option<Manager>,
    art_key: String,
    art: Option<Arc<str>>,
    art_rev: u64,
    art_tries: u8,
}

impl Reader {
    pub fn new() -> Self {
        Self { manager: None, art_key: String::new(), art: None, art_rev: 0, art_tries: 0 }
    }

    fn manager(&mut self) -> Result<Manager, String> {
        if let Some(m) = &self.manager {
            return Ok(m.clone());
        }
        let m = Manager::RequestAsync()
            .and_then(|op| op.get())
            .map_err(|e| format!("media session manager: {e}"))?;
        self.manager = Some(m.clone());
        Ok(m)
    }

    /// True when any app (Spotify, a browser tab, VLC, Netflix...) has a media
    /// session. Cheap enough to ask on every tick.
    pub fn has_session(&mut self) -> bool {
        let Ok(manager) = self.manager() else { return false };
        match manager.GetSessions().and_then(|s| s.Size()) {
            Ok(n) => n > 0,
            Err(_) => {
                self.manager = None;
                false
            }
        }
    }

    /// The session to show, in this order: Spotify if it is playing, any other
    /// app that is playing, whatever Windows calls the current session (the one
    /// used last), Spotify, then the first session there is. So music keeps
    /// working exactly as before, and a YouTube tab, VLC or Netflix take over
    /// the notch while they play.
    fn session(&mut self) -> Result<Option<Session>, String> {
        let manager = self.manager()?;
        let result: windows::core::Result<Option<Session>> = (|| {
            let sessions = manager.GetSessions()?;
            let mut playing_other: Option<Session> = None;
            let mut spotify: Option<Session> = None;
            let mut first: Option<Session> = None;
            for i in 0..sessions.Size()? {
                let s = sessions.GetAt(i)?;
                let id = s.SourceAppUserModelId()?.to_string().to_lowercase();
                let playing = s
                    .GetPlaybackInfo()
                    .and_then(|info| info.PlaybackStatus())
                    .map(|p| p == PlaybackStatus::Playing)
                    .unwrap_or(false);
                let is_spotify = id.contains("spotify");
                if playing && is_spotify {
                    return Ok(Some(s));
                }
                if playing && playing_other.is_none() {
                    playing_other = Some(s.clone());
                }
                if is_spotify && spotify.is_none() {
                    spotify = Some(s.clone());
                }
                if first.is_none() {
                    first = Some(s);
                }
            }
            if playing_other.is_some() {
                return Ok(playing_other);
            }
            if let Ok(current) = manager.GetCurrentSession() {
                return Ok(Some(current));
            }
            Ok(spotify.or(first))
        })();
        match result {
            Ok(s) => Ok(s),
            Err(e) => {
                // A stale manager is the usual cause: rebuild it next time.
                self.manager = None;
                Err(format!("media sessions: {e}"))
            }
        }
    }

    /// One reading. `Ok(None)` means Spotify is open but has no media session
    /// (nothing played yet) — an idle island, never a made-up track.
    pub fn poll(&mut self) -> Result<Option<Media>, String> {
        let Some(session) = self.session()? else {
            return Ok(None);
        };
        let props = session
            .TryGetMediaPropertiesAsync()
            .and_then(|op| op.get())
            .map_err(|e| format!("media properties: {e}"))?;

        let title = props.Title().map(|s| s.to_string()).unwrap_or_default();
        let artist = props.Artist().map(|s| s.to_string()).unwrap_or_default();
        let album = props.AlbumTitle().map(|s| s.to_string()).unwrap_or_default();

        let info = session.GetPlaybackInfo().map_err(|e| format!("playback info: {e}"))?;
        let raw_status = info.PlaybackStatus().unwrap_or(PlaybackStatus::Closed);
        let has_track = !title.is_empty();
        let status = if raw_status == PlaybackStatus::Playing {
            Status::Playing
        } else if raw_status == PlaybackStatus::Paused || has_track {
            // Stopped / changing / opened with a track still named: show it paused.
            Status::Paused
        } else {
            Status::Idle
        };

        if !has_track && status == Status::Idle {
            return Ok(None);
        }

        let (can_prev, can_next, can_toggle) = match info.Controls() {
            Ok(c) => (
                c.IsPreviousEnabled().unwrap_or(false),
                c.IsNextEnabled().unwrap_or(false),
                c.IsPlayEnabled().unwrap_or(false) || c.IsPauseEnabled().unwrap_or(false),
            ),
            Err(_) => (false, false, false),
        };

        // Shuffle: `shuffle` is None when the app doesn't report it to Windows at all,
        // `can_shuffle` is whether it says it accepts shuffle requests.
        let shuffle = info.IsShuffleActive().and_then(|flag| flag.Value()).ok();
        let can_shuffle = info
            .Controls()
            .and_then(|c| c.IsShuffleEnabled())
            .unwrap_or(false);

        let (position_ms, duration_ms) = match session.GetTimelineProperties() {
            Ok(t) => {
                let now_unix_ms = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map(|d| d.as_millis() as i64)
                    .unwrap_or(0);
                timeline_ms(
                    t.StartTime().map(|v| v.Duration).unwrap_or(0),
                    t.EndTime().map(|v| v.Duration).unwrap_or(0),
                    t.Position().map(|v| v.Duration).unwrap_or(0),
                    t.LastUpdatedTime().map(|v| v.UniversalTime).unwrap_or(0),
                    now_unix_ms,
                    status == Status::Playing,
                )
            }
            Err(_) => (None, None),
        };

        self.refresh_art(&props, &format!("{title}\u{1}{artist}\u{1}{album}"));
        let source = session
            .SourceAppUserModelId()
            .map(|s| friendly_source(&s.to_string()))
            .unwrap_or_default();

        Ok(Some(Media {
            title,
            artist,
            album,
            status,
            position_ms,
            duration_ms,
            can_prev,
            can_next,
            can_toggle,
            shuffle,
            can_shuffle,
            art: self.art.clone(),
            art_rev: self.art_rev,
            source,
        }))
    }

    /// Reads the cover only when the track changed (or the first read came up
    /// empty) — never on every tick.
    fn refresh_art(&mut self, props: &MediaProperties, key: &str) {
        if self.art_key != key {
            self.art_key = key.to_string();
            self.art = None;
            self.art_tries = 0;
        }
        if self.art.is_some() || self.art_tries >= ART_RETRIES {
            return;
        }
        self.art_tries += 1;
        if let Some(url) = read_thumbnail(props).as_deref().and_then(data_url) {
            self.art = Some(Arc::from(url));
            self.art_rev += 1;
        }
    }

    /// Sends a transport command to Spotify. Fire-and-forget: the request is
    /// handed to the session and not waited on, so an unresponsive Spotify can
    /// never freeze the island. The next poll shows the outcome.
    pub fn control(&mut self, action: Control) -> Result<(), String> {
        let Some(session) = self.session()? else {
            return Ok(());
        };
        let result = match action {
            Control::TogglePlayPause => session.TryTogglePlayPauseAsync().map(drop),
            Control::Next => session.TrySkipNextAsync().map(drop),
            Control::Previous => session.TrySkipPreviousAsync().map(drop),
            Control::Seek(ticks) => session.TryChangePlaybackPositionAsync(ticks).map(drop),
            Control::ToggleShuffle => return toggle_shuffle(&session),
        };
        result.map_err(|e| format!("{action:?}: {e}"))
    }
}

/// Flips shuffle: reads what the app reports now (off when it reports nothing) and asks
/// for the opposite. Unlike the other controls this waits for the answer, so an app that
/// doesn't support shuffle through Windows ends up in the log instead of failing silently.
fn toggle_shuffle(session: &Session) -> Result<(), String> {
    let on = session
        .GetPlaybackInfo()
        .and_then(|info| info.IsShuffleActive())
        .and_then(|flag| flag.Value())
        .unwrap_or(false);
    let accepted = session
        .TryChangeShuffleActiveAsync(!on)
        .and_then(|op| op.get())
        .map_err(|e| format!("shuffle: {e}"))?;
    if accepted {
        Ok(())
    } else {
        Err("shuffle: the player did not accept the request (it may not support shuffle through Windows)".to_string())
    }
}

fn read_thumbnail(props: &MediaProperties) -> Option<Vec<u8>> {
    let reference = props.Thumbnail().ok()?;
    let stream = reference.OpenReadAsync().ok()?.get().ok()?;
    let size = stream.Size().ok()?;
    if size == 0 || size > MAX_ART_BYTES {
        return None;
    }
    let reader = DataReader::CreateDataReader(&stream).ok()?;
    reader.LoadAsync(size as u32).ok()?.get().ok()?;
    let mut bytes = vec![0u8; size as usize];
    reader.ReadBytes(&mut bytes).ok()?;
    Some(bytes)
}
