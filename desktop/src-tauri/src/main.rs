// SPDX-License-Identifier: Apache-2.0
//! Desktop shell: starts the bundled Python backend (PyInstaller sidecar `rhizome-server`), waits
//! for it to print the tokenised UI URL, then points the main window at it. The backend serves both
//! the REST API and the UI, so the window talks to the same origin as `rhz serve`.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use regex::Regex;
use tauri::{Manager, Url};
use tauri_plugin_shell::{process::CommandEvent, ShellExt};

const PORT: &str = "8765";

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let handle = app.handle().clone();
            let (mut rx, child) = app
                .shell()
                .sidecar("rhizome-server")?
                .args(["serve", "--port", PORT])
                .spawn()?;
            app.manage(SidecarChild(std::sync::Mutex::new(Some(child))));
            let url_re = Regex::new(r"(http://127\.0\.0\.1:\d+/#token=[A-Za-z0-9_\-]+)").unwrap();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stdout(line) = event {
                        let line = String::from_utf8_lossy(&line);
                        if let Some(m) = url_re.captures(&line) {
                            if let (Some(win), Ok(url)) = (handle.get_webview_window("main"), Url::parse(&m[1])) {
                                let _ = win.navigate(url);
                            }
                        }
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.try_state::<SidecarChild>() {
                    if let Some(child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Rhizome");
}

struct SidecarChild(std::sync::Mutex<Option<tauri_plugin_shell::process::CommandChild>>);
