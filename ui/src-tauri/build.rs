fn main() {
    // Re-embed the exe / tray / window icon whenever the icon files change.
    println!("cargo:rerun-if-changed=icons");
    println!("cargo:rerun-if-changed=tauri.conf.json");
    tauri_build::build()
}
