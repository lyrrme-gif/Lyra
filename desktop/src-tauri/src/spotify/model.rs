// Spotify island — platform-independent core.
//
// No Tauri, no Win32: just the data that travels to the pill, the logic that
// decides *when* something is worth sending, and the small conversions
// (timeline maths, image → data URL). Keeping it pure is what lets it be unit
// tested on any machine; `cargo test` runs the tests at the bottom.

use std::sync::Arc;
use std::time::{Duration, Instant};

use serde::Serialize;

/// What Spotify is doing, as the pill needs to know it.
#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    /// Spotify is open but has no track (nothing played yet, or the session is gone).
    Idle,
    Playing,
    Paused,
}

/// One reading of Spotify's media session.
#[derive(Clone, Debug)]
pub struct Media {
    pub title: String,
    pub artist: String,
    pub album: String,
    pub status: Status,
    /// Playback position *now* (already advanced to the moment of the reading).
    pub position_ms: Option<u64>,
    pub duration_ms: Option<u64>,
    pub can_prev: bool,
    pub can_next: bool,
    pub can_toggle: bool,
    /// Shuffle state as the app reports it; None when it doesn't report one.
    pub shuffle: Option<bool>,
    /// True when the app says it accepts shuffle requests.
    pub can_shuffle: bool,
    /// `data:` URL of the album art. Shared, because it is large and the reader
    /// hands out the same one on every poll until the track changes.
    pub art: Option<Arc<str>>,
    /// Bumped by the reader each time a *new* image is loaded.
    pub art_rev: u64,
    /// Friendly name of the app that owns the session ("Spotify", "Chrome", "VLC"...).
    pub source: String,
}

/// Turns a media session's app id (`Spotify.exe`, `Chrome`, `MSEdge`,
/// `Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic`...) into a short name.
pub fn friendly_source(app_id: &str) -> String {
    const KNOWN: &[(&str, &str)] = &[
        ("spotify", "Spotify"),
        ("msedge", "Edge"),
        ("chrome", "Chrome"),
        ("firefox", "Firefox"),
        ("brave", "Brave"),
        ("opera", "Opera"),
        ("vivaldi", "Vivaldi"),
        ("vlc", "VLC"),
        ("netflix", "Netflix"),
        ("disney", "Disney+"),
        ("primevideo", "Prime Video"),
        ("zunemusic", "Media Player"),
        ("zunevideo", "Movies & TV"),
        ("mediaplayer", "Media Player"),
        ("wmplayer", "Media Player"),
        ("applemusic", "Apple Music"),
        ("itunes", "iTunes"),
        ("tidal", "Tidal"),
        ("deezer", "Deezer"),
        ("potplayer", "PotPlayer"),
        ("mpv", "mpv"),
    ];
    let id = app_id.to_lowercase();
    for (key, name) in KNOWN {
        if id.contains(key) {
            return (*name).to_string();
        }
    }
    // Unknown app: its id without the ".exe" / package suffix, first letter capitalised.
    let base = id.split(|c| c == '!' || c == '_').next().unwrap_or("");
    let base = base.trim_end_matches(".exe");
    // Firefox reports a 16-digit hex hash instead of a name.
    if base.len() >= 12 && base.chars().all(|c| c.is_ascii_hexdigit()) {
        return "Browser".to_string();
    }
    let mut chars = base.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().chain(chars).take(24).collect(),
        None => String::new(),
    }
}

impl Media {
    /// Identity of the track, for "did the song change".
    pub fn track_key(&self) -> String {
        format!("{}\u{1}{}\u{1}{}", self.title, self.artist, self.album)
    }
}

/// The event payload sent to the pill (`spotify-state`).
///
/// The artwork is big, so it is only attached when it changed (`art_changed`):
/// the page keeps the last one it received.
#[derive(Serialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Payload {
    /// False once the Spotify process is gone: the pill plays its exit animation.
    pub running: bool,
    pub status: Status,
    pub title: String,
    pub artist: String,
    pub album: String,
    pub position_ms: Option<u64>,
    pub duration_ms: Option<u64>,
    pub can_prev: bool,
    pub can_next: bool,
    pub can_toggle: bool,
    /// None when the app doesn't report shuffle (the button then shows as unavailable).
    pub shuffle: Option<bool>,
    pub can_shuffle: bool,
    pub track_key: String,
    pub art_changed: bool,
    pub art: Option<String>,
    /// Which app the session belongs to (see `Media::source`); empty when idle.
    pub source: String,
}

/// How far the position may drift from what the page extrapolates before we
/// send a correction (a seek, mostly). Spotify's own timeline is only accurate
/// to a second or so, so anything tighter would just be noise.
const POSITION_TOLERANCE_MS: u64 = 2500;

#[derive(Clone, Debug, PartialEq)]
struct Fingerprint {
    running: bool,
    status: Status,
    track_key: String,
    duration_ms: Option<u64>,
    can_prev: bool,
    can_next: bool,
    can_toggle: bool,
    shuffle: Option<bool>,
    can_shuffle: bool,
    art_rev: u64,
    has_art: bool,
}

struct Sent {
    fp: Fingerprint,
    position_ms: Option<u64>,
    at: Instant,
}

/// Remembers what the page already knows and only lets through what changed.
/// This is what keeps the IPC quiet: a steady song produces no events at all.
#[derive(Default)]
pub struct Dedup {
    last: Option<Sent>,
    /// The art the page currently holds, kept so a freshly loaded page can be
    /// brought up to date without asking the OS again.
    art: Option<Arc<str>>,
}

impl Dedup {
    /// Forget everything (Spotify closed): the next reading is sent in full.
    pub fn reset(&mut self) {
        self.last = None;
        self.art = None;
    }

    /// Returns a payload if `media` differs from what was last sent.
    /// `force` sends it regardless, with the artwork attached (page just loaded).
    pub fn update(
        &mut self,
        running: bool,
        media: Option<&Media>,
        now: Instant,
        force: bool,
    ) -> Option<Payload> {
        let fp = fingerprint(running, media);
        let position_ms = media.and_then(|m| m.position_ms);

        let changed = match &self.last {
            None => true,
            Some(prev) => prev.fp != fp || drifted(prev, position_ms, now),
        };
        if !changed && !force {
            return None;
        }

        let art_changed = force
            || match &self.last {
                None => true,
                Some(prev) => prev.fp.art_rev != fp.art_rev || prev.fp.has_art != fp.has_art,
            };
        if art_changed {
            self.art = media.and_then(|m| m.art.clone());
        }
        let payload = build(running, media, art_changed, self.art.as_deref());
        self.last = Some(Sent { fp, position_ms, at: now });
        Some(payload)
    }
}

fn fingerprint(running: bool, media: Option<&Media>) -> Fingerprint {
    match media {
        Some(m) => Fingerprint {
            running,
            status: m.status,
            track_key: m.track_key(),
            duration_ms: m.duration_ms,
            can_prev: m.can_prev,
            can_next: m.can_next,
            can_toggle: m.can_toggle,
            shuffle: m.shuffle,
            can_shuffle: m.can_shuffle,
            art_rev: m.art_rev,
            has_art: m.art.is_some(),
        },
        None => Fingerprint {
            running,
            status: Status::Idle,
            track_key: String::new(),
            duration_ms: None,
            can_prev: false,
            can_next: false,
            can_toggle: false,
            shuffle: None,
            can_shuffle: false,
            art_rev: 0,
            has_art: false,
        },
    }
}

/// Has the real position moved away from where the page thinks it is?
fn drifted(prev: &Sent, now_pos: Option<u64>, now: Instant) -> bool {
    let (Some(then), Some(actual)) = (prev.position_ms, now_pos) else {
        return prev.position_ms.is_some() != now_pos.is_some();
    };
    let elapsed = if prev.fp.status == Status::Playing {
        now.saturating_duration_since(prev.at).as_millis() as u64
    } else {
        0
    };
    let expected = then.saturating_add(elapsed);
    expected.abs_diff(actual) > POSITION_TOLERANCE_MS
}

fn build(running: bool, media: Option<&Media>, art_changed: bool, art: Option<&str>) -> Payload {
    let art = if art_changed { art.map(str::to_owned) } else { None };
    match media {
        Some(m) => Payload {
            running,
            status: m.status,
            title: m.title.clone(),
            artist: m.artist.clone(),
            album: m.album.clone(),
            position_ms: m.position_ms,
            duration_ms: m.duration_ms,
            can_prev: m.can_prev,
            can_next: m.can_next,
            can_toggle: m.can_toggle,
            shuffle: m.shuffle,
            can_shuffle: m.can_shuffle,
            track_key: m.track_key(),
            art_changed,
            art,
            source: m.source.clone(),
        },
        None => Payload {
            running,
            status: Status::Idle,
            title: String::new(),
            artist: String::new(),
            album: String::new(),
            position_ms: None,
            duration_ms: None,
            can_prev: false,
            can_next: false,
            can_toggle: false,
            shuffle: None,
            can_shuffle: false,
            track_key: String::new(),
            art_changed,
            art,
            source: String::new(),
        },
    }
}

// ── Timeline ────────────────────────────────────────────────────────────────

/// Windows timestamps count 100 ns ticks from 1601-01-01; Unix time from 1970.
const TICKS_PER_MS: i64 = 10_000;
const EPOCH_DIFF_MS: i64 = 11_644_473_600_000;

/// Turns the raw numbers of a media-session timeline into (position, duration)
/// in milliseconds.
///
/// Spotify only refreshes its timeline when something happens (play, pause,
/// seek, new track), so while playing the stored position is advanced by the
/// time elapsed since `last_updated`.
pub fn timeline_ms(
    start_ticks: i64,
    end_ticks: i64,
    position_ticks: i64,
    last_updated_ticks: i64,
    now_unix_ms: i64,
    playing: bool,
) -> (Option<u64>, Option<u64>) {
    let duration = (end_ticks - start_ticks) / TICKS_PER_MS;
    if duration <= 0 {
        return (None, None);
    }
    let mut position = (position_ticks - start_ticks) / TICKS_PER_MS;
    if playing && last_updated_ticks > 0 {
        let updated_unix_ms = last_updated_ticks / TICKS_PER_MS - EPOCH_DIFF_MS;
        let elapsed = now_unix_ms - updated_unix_ms;
        // A wildly large or negative gap means the clock reading is unusable
        // (some players leave it at zero): keep the stored position.
        if (0..=duration).contains(&elapsed) {
            position += elapsed;
        }
    }
    let position = position.clamp(0, duration);
    (Some(position as u64), Some(duration as u64))
}

// ── Artwork ─────────────────────────────────────────────────────────────────

fn mime_for(bytes: &[u8]) -> &'static str {
    if bytes.starts_with(&[0x89, b'P', b'N', b'G']) {
        "image/png"
    } else if bytes.len() > 12 && &bytes[0..4] == b"RIFF" && &bytes[8..12] == b"WEBP" {
        "image/webp"
    } else if bytes.starts_with(b"GIF8") {
        "image/gif"
    } else if bytes.starts_with(&[0xFF, 0xD8]) {
        "image/jpeg"
    } else {
        // Not an image we recognise: better no artwork than a broken <img>.
        ""
    }
}

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

fn base64(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        out.push(B64[(n >> 18) as usize & 63] as char);
        out.push(B64[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 { B64[(n >> 6) as usize & 63] as char } else { '=' });
        out.push(if chunk.len() > 2 { B64[n as usize & 63] as char } else { '=' });
    }
    out
}

/// `data:image/…;base64,…` for the page's `<img>`, or `None` for anything that
/// is not a recognisable image. The page's CSP already allows `data:` images.
pub fn data_url(bytes: &[u8]) -> Option<String> {
    let mime = mime_for(bytes);
    if mime.is_empty() {
        return None;
    }
    Some(format!("data:{mime};base64,{}", base64(bytes)))
}

/// Gap used when waiting for the next tick; kept here so the loop and the
/// tests agree on one number.
pub const ACTIVE_TICK: Duration = Duration::from_millis(1000);
pub const IDLE_TICK: Duration = Duration::from_millis(1500);

#[cfg(test)]
mod tests {
    use super::*;

    fn media(title: &str, status: Status, pos: Option<u64>) -> Media {
        Media {
            title: title.into(),
            artist: "Artist".into(),
            album: "Album".into(),
            status,
            position_ms: pos,
            duration_ms: Some(200_000),
            can_prev: true,
            can_next: true,
            can_toggle: true,
            shuffle: Some(false),
            can_shuffle: true,
            art: Some(Arc::from("data:image/png;base64,AAAA")),
            art_rev: 1,
            source: "Spotify".into(),
        }
    }

    // ── base64 / data URL ───────────────────────────────────────────────────

    #[test]
    fn base64_matches_rfc4648_vectors() {
        assert_eq!(base64(b""), "");
        assert_eq!(base64(b"f"), "Zg==");
        assert_eq!(base64(b"fo"), "Zm8=");
        assert_eq!(base64(b"foo"), "Zm9v");
        assert_eq!(base64(b"foob"), "Zm9vYg==");
        assert_eq!(base64(b"fooba"), "Zm9vYmE=");
        assert_eq!(base64(b"foobar"), "Zm9vYmFy");
    }

    #[test]
    fn base64_handles_every_byte_value() {
        let all: Vec<u8> = (0..=255).collect();
        let encoded = base64(&all);
        assert_eq!(encoded.len(), 344);
        assert!(encoded.ends_with('='));
        assert!(encoded.starts_with("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g"));
    }

    #[test]
    fn data_url_detects_image_types() {
        let jpeg = data_url(&[0xFF, 0xD8, 0xFF, 0xE0]).unwrap();
        assert!(jpeg.starts_with("data:image/jpeg;base64,"));
        let png = data_url(&[0x89, b'P', b'N', b'G', 0x0D]).unwrap();
        assert!(png.starts_with("data:image/png;base64,"));
        let webp = data_url(b"RIFF\x00\x00\x00\x00WEBPVP8 ").unwrap();
        assert!(webp.starts_with("data:image/webp;base64,"));
    }

    #[test]
    fn data_url_rejects_non_images() {
        assert!(data_url(b"").is_none());
        assert!(data_url(b"<html>not an image</html>").is_none());
    }

    // ── timeline ────────────────────────────────────────────────────────────

    /// Unix ms → Windows 100 ns ticks since 1601.
    fn win_ticks(unix_ms: i64) -> i64 {
        (unix_ms + EPOCH_DIFF_MS) * TICKS_PER_MS
    }

    #[test]
    fn timeline_advances_position_while_playing() {
        let now = 1_700_000_100_000;
        let (pos, dur) = timeline_ms(0, 200_000 * 10_000, 30_000 * 10_000, win_ticks(now - 5_000), now, true);
        assert_eq!(dur, Some(200_000));
        assert_eq!(pos, Some(35_000));
    }

    #[test]
    fn timeline_freezes_position_while_paused() {
        let now = 1_700_000_100_000;
        let (pos, _) = timeline_ms(0, 200_000 * 10_000, 30_000 * 10_000, win_ticks(now - 90_000), now, false);
        assert_eq!(pos, Some(30_000));
    }

    #[test]
    fn timeline_ignores_unusable_clock_readings() {
        let now = 1_700_000_100_000;
        // never updated
        let (pos, _) = timeline_ms(0, 200_000 * 10_000, 12_000 * 10_000, 0, now, true);
        assert_eq!(pos, Some(12_000));
        // update stamp in the future
        let (pos, _) = timeline_ms(0, 200_000 * 10_000, 12_000 * 10_000, win_ticks(now + 60_000), now, true);
        assert_eq!(pos, Some(12_000));
        // update stamp absurdly old (longer than the track)
        let (pos, _) = timeline_ms(0, 200_000 * 10_000, 12_000 * 10_000, win_ticks(now - 9_999_999), now, true);
        assert_eq!(pos, Some(12_000));
    }

    #[test]
    fn timeline_clamps_and_handles_no_duration() {
        let now = 1_700_000_100_000;
        let (pos, _) = timeline_ms(0, 100_000 * 10_000, 99_000 * 10_000, win_ticks(now - 50_000), now, true);
        assert_eq!(pos, Some(100_000));
        assert_eq!(timeline_ms(0, 0, 0, 0, now, true), (None, None));
        assert_eq!(timeline_ms(5, 5, 5, 5, now, true), (None, None));
    }

    // ── change detection ────────────────────────────────────────────────────

    #[test]
    fn first_reading_is_always_sent_with_art() {
        let mut d = Dedup::default();
        let m = media("Song", Status::Playing, Some(1_000));
        let p = d.update(true, Some(&m), Instant::now(), false).unwrap();
        assert!(p.running && p.art_changed);
        assert_eq!(p.art.as_deref(), Some("data:image/png;base64,AAAA"));
        assert_eq!(p.title, "Song");
    }

    #[test]
    fn steady_playback_sends_nothing() {
        let mut d = Dedup::default();
        let t0 = Instant::now();
        let m0 = media("Song", Status::Playing, Some(10_000));
        assert!(d.update(true, Some(&m0), t0, false).is_some());
        for s in 1..=30u64 {
            let m = media("Song", Status::Playing, Some(10_000 + s * 1_000));
            let t = t0 + Duration::from_secs(s);
            assert!(d.update(true, Some(&m), t, false).is_none(), "tick {s} should be quiet");
        }
    }

    #[test]
    fn track_change_is_sent_and_art_follows() {
        let mut d = Dedup::default();
        let t0 = Instant::now();
        d.update(true, Some(&media("One", Status::Playing, Some(0))), t0, false);
        let mut next = media("Two", Status::Playing, Some(0));
        next.art = Some(Arc::from("data:image/jpeg;base64,BBBB"));
        next.art_rev = 2;
        let p = d.update(true, Some(&next), t0 + Duration::from_secs(1), false).unwrap();
        assert_eq!(p.title, "Two");
        assert!(p.art_changed);
        assert_eq!(p.art.as_deref(), Some("data:image/jpeg;base64,BBBB"));
    }

    #[test]
    fn same_art_is_not_resent_on_play_pause() {
        let mut d = Dedup::default();
        let t0 = Instant::now();
        d.update(true, Some(&media("Song", Status::Playing, Some(0))), t0, false);
        let p = d
            .update(true, Some(&media("Song", Status::Paused, Some(4_000))), t0 + Duration::from_secs(4), false)
            .unwrap();
        assert_eq!(p.status, Status::Paused);
        assert!(!p.art_changed);
        assert!(p.art.is_none());
    }

    #[test]
    fn late_arriving_art_is_sent_once() {
        let mut d = Dedup::default();
        let t0 = Instant::now();
        let mut no_art = media("Song", Status::Playing, Some(0));
        no_art.art = None;
        no_art.art_rev = 0;
        let first = d.update(true, Some(&no_art), t0, false).unwrap();
        assert!(first.art.is_none());
        let with_art = media("Song", Status::Playing, Some(1_000));
        let p = d.update(true, Some(&with_art), t0 + Duration::from_secs(1), false).unwrap();
        assert!(p.art_changed && p.art.is_some());
        assert!(d.update(true, Some(&media("Song", Status::Playing, Some(2_000))), t0 + Duration::from_secs(2), false).is_none());
    }

    #[test]
    fn a_seek_is_noticed() {
        let mut d = Dedup::default();
        let t0 = Instant::now();
        d.update(true, Some(&media("Song", Status::Playing, Some(10_000))), t0, false);
        let jump = media("Song", Status::Playing, Some(120_000));
        assert!(d.update(true, Some(&jump), t0 + Duration::from_secs(1), false).is_some());
    }

    #[test]
    fn small_clock_jitter_is_not_a_seek() {
        let mut d = Dedup::default();
        let t0 = Instant::now();
        d.update(true, Some(&media("Song", Status::Playing, Some(10_000))), t0, false);
        // reading is 1.2 s "ahead" of where extrapolation says we should be
        let jitter = media("Song", Status::Playing, Some(10_000 + 5_000 + 1_200));
        assert!(d.update(true, Some(&jitter), t0 + Duration::from_secs(5), false).is_none());
    }

    #[test]
    fn spotify_closing_and_session_loss_are_sent() {
        let mut d = Dedup::default();
        let t0 = Instant::now();
        d.update(true, Some(&media("Song", Status::Playing, Some(0))), t0, false);
        // session vanished while the process is still there → idle, no invented track
        let idle = d.update(true, None, t0 + Duration::from_secs(1), false).unwrap();
        assert!(idle.running);
        assert_eq!(idle.status, Status::Idle);
        assert!(idle.title.is_empty() && idle.artist.is_empty());
        // process gone
        let closed = d.update(false, None, t0 + Duration::from_secs(2), false).unwrap();
        assert!(!closed.running);
    }

    #[test]
    fn force_resends_everything_including_art() {
        let mut d = Dedup::default();
        let t0 = Instant::now();
        let m = media("Song", Status::Playing, Some(0));
        d.update(true, Some(&m), t0, false);
        assert!(d.update(true, Some(&m), t0, false).is_none());
        let p = d.update(true, Some(&m), t0, true).unwrap();
        assert!(p.art_changed);
        assert!(p.art.is_some());
    }

    #[test]
    fn reset_makes_the_next_reading_full_again() {
        let mut d = Dedup::default();
        let t0 = Instant::now();
        let m = media("Song", Status::Playing, Some(0));
        d.update(true, Some(&m), t0, false);
        d.reset();
        let p = d.update(true, Some(&m), t0, false).unwrap();
        assert!(p.art_changed && p.art.is_some());
    }

    #[test]
    fn friendly_source_names_common_players() {
        assert_eq!(friendly_source("Spotify.exe"), "Spotify");
        assert_eq!(friendly_source("Chrome"), "Chrome");
        assert_eq!(friendly_source("MSEdge"), "Edge");
        assert_eq!(friendly_source("VLC.exe"), "VLC");
        assert_eq!(friendly_source("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic"), "Media Player");
        assert_eq!(friendly_source("SomePlayer.exe"), "Someplayer");
        assert_eq!(friendly_source(""), "");
        assert_eq!(friendly_source("308046B0AF4A39CB"), "Browser");
    }

    #[test]
    fn payload_serialises_in_camel_case_for_the_page() {
        let mut d = Dedup::default();
        let p = d
            .update(true, Some(&media("Song", Status::Playing, Some(1_500))), Instant::now(), false)
            .unwrap();
        let json = serde_json::to_value(&p).unwrap();
        assert_eq!(json["status"], "playing");
        assert_eq!(json["positionMs"], 1_500);
        assert_eq!(json["durationMs"], 200_000);
        assert_eq!(json["canNext"], true);
        assert_eq!(json["artChanged"], true);
        assert_eq!(json["trackKey"], "Song\u{1}Artist\u{1}Album");
        assert_eq!(json["shuffle"], false);
        assert_eq!(json["canShuffle"], true);
    }

    #[test]
    fn shuffle_changes_are_sent() {
        let mut d = Dedup::default();
        let now = Instant::now();
        let mut m = media("Song", Status::Playing, Some(1_000));
        assert!(d.update(true, Some(&m), now, false).is_some());
        // Nothing changed: stays quiet.
        assert!(d.update(true, Some(&m), now, false).is_none());
        // Shuffle switched on: the page must hear about it.
        m.shuffle = Some(true);
        let p = d.update(true, Some(&m), now, false).expect("shuffle change is sent");
        assert_eq!(p.shuffle, Some(true));
        // A player that stops reporting shuffle also counts as a change.
        m.shuffle = None;
        m.can_shuffle = false;
        let p = d.update(true, Some(&m), now, false).expect("losing shuffle info is sent");
        assert_eq!(p.shuffle, None);
        assert!(!p.can_shuffle);
    }
}
