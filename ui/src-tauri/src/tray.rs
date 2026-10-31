// Notification-area icon: Open, Settings, Pause, Quit.

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter};

use crate::island::WINDOW_LABEL;

pub fn build(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Lyra", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "Pause", true, None::<&str>)?;
    let mochi_show = MenuItem::with_id(app, "mochi_show", "Show Mochi on screen", true, None::<&str>)?;
    let mochi_home = MenuItem::with_id(app, "mochi_home", "Mochi: back to island", true, None::<&str>)?;
    let mochi_hide = MenuItem::with_id(app, "mochi_hide", "Hide Mochi", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let sep1 = PredefinedMenuItem::separator(app)?;
    let sep2 = PredefinedMenuItem::separator(app)?;
    let sep3 = PredefinedMenuItem::separator(app)?;

    let menu = Menu::with_items(
        app,
        &[&open, &sep1, &mochi_show, &mochi_home, &mochi_hide, &sep3, &settings, &pause, &sep2, &quit],
    )?;

    let mut builder = TrayIconBuilder::with_id("lyra")
        .tooltip("Lyra")
        .menu(&menu)
        .on_menu_event(|app: &AppHandle, event| match event.id.as_ref() {
            "quit" => {
                // Let the UI play its closing animation first; force-quit if it never answers.
                let _ = app.emit_to(WINDOW_LABEL, "tray", "quit".to_string());
                let handle = app.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(1500));
                    handle.exit(0);
                });
            }
            // Mochi: these only flip backend prefs; the mascot window and the notch Mochi
            // follow them within ~2 s (same path as the Settings toggles / right-click menu).
            "mochi_show" => crate::mascot::save_pref(r#"{"feature_mascot":true,"feature_mascot_stay":true}"#),
            "mochi_home" => crate::mascot::save_pref(r#"{"feature_mascot":true,"feature_mascot_stay":false}"#),
            "mochi_hide" => crate::mascot::save_pref(r#"{"feature_mascot":false}"#),
            id => {
                let _ = app.emit_to(WINDOW_LABEL, "tray", id.to_string());
            }
        });

    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon);
    }

    builder.build(app)?;
    Ok(())
}
