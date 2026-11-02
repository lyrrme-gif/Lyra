// Notification-area icon: Open Lyra, Settings, Quit. (Mochi show / hide and Pause were removed: the Mochi is
// controlled from Settings -> Features and by dragging it.)

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter};

use crate::island::WINDOW_LABEL;

pub fn build(app: &AppHandle) -> tauri::Result<()> {
    let title = MenuItem::with_id(app, "title", format!("Lyra  ·  v{}", app.package_info().version), false, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "Open Lyra", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Lyra", true, None::<&str>)?;
    let sep1 = PredefinedMenuItem::separator(app)?;
    let sep2 = PredefinedMenuItem::separator(app)?;

    let menu = Menu::with_items(app, &[&title, &sep1, &open, &settings, &sep2, &quit])?;

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
