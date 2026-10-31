// Lyra UI runs without a console window: Lyra UI is the desktop interface.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    lyra_lib::run()
}
