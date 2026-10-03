// SPDX-License-Identifier: Apache-2.0
//! Rhizome desktop shell.
//!
//! Starts the bundled backend (`server/rhz.exe serve`, a PyInstaller one-folder build installed
//! next to the app), on a free localhost port with a token generated here, waits until it accepts
//! connections, then points the window at it. The backend serves both the REST API and the UI.
//! The backend is stopped when the app exits; a second launch focuses the running window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, File};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, Url, WebviewWindow};

struct Backend(Mutex<Option<Child>>);

const STARTUP_TIMEOUT: Duration = Duration::from_secs(90);

fn free_port() -> std::io::Result<u16> {
    let l = TcpListener::bind("127.0.0.1:0")?;
    Ok(l.local_addr()?.port())
}

fn new_token() -> String {
    let mut buf = [0u8; 32];
    getrandom::getrandom(&mut buf).expect("OS random source unavailable");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

fn server_exe(resource_dir: &Path) -> PathBuf {
    let name = if cfg!(windows) { "rhz.exe" } else { "rhz" };
    resource_dir.join("server").join(name)
}

/// Portable install: a file named `portable` next to Rhizome.exe keeps the library in `data\`.
fn portable_data_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    dir.join("portable").exists().then(|| dir.join("data"))
}

fn spawn_backend(exe: &Path, port: u16, token: &str, log_dir: &Path) -> std::io::Result<Child> {
    fs::create_dir_all(log_dir)?;
    let log = File::create(log_dir.join("backend-console.log"))?;
    let mut cmd = Command::new(exe);
    cmd.args(["serve", "--host", "127.0.0.1", "--port", &port.to_string()])
        .env("RHIZOME_API_TOKEN", token)
        .env("PYTHONUTF8", "1")
        // the backend exits by itself if this process disappears without a clean shutdown
        .env("RHIZOME_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));
    if let Some(data) = portable_data_dir() {
        cmd.env("RHIZOME_DATA_DIR", data);
    }
    if let Some(dir) = exe.parent() {
        cmd.current_dir(dir);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd.spawn()
}

fn show_error(win: &WebviewWindow, title: &str, detail: &str) {
    let js = format!(
        "document.body.innerHTML = '<main style=\"font-family:system-ui,sans-serif;max-width:640px;margin:15vh auto;padding:0 24px;color:#1c1f1a\"><h2>' + {t} + '</h2><pre style=\"white-space:pre-wrap;background:#f5f3ec;padding:12px;border-radius:8px\">' + {d} + '</pre></main>';",
        t = serde_json_string(title),
        d = serde_json_string(detail)
    );
    let _ = win.eval(&js);
}

/// Minimal JS string literal escaping (avoids pulling in serde_json for one call).
fn serde_json_string(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => {}
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn tail(path: &Path, max: usize) -> String {
    let text = fs::read_to_string(path).unwrap_or_default();
    let start = text.len().saturating_sub(max);
    text.get(start..).unwrap_or(&text).to_string()
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let win = app.get_webview_window("main").expect("main window");
            let resource_dir = app.path().resource_dir()?;
            let log_dir = app.path().app_log_dir()?;
            let exe = server_exe(&resource_dir);
            let port = free_port()?;
            let token = new_token();

            let child = match spawn_backend(&exe, port, &token, &log_dir) {
                Ok(c) => c,
                Err(e) => {
                    show_error(&win, "Rhizome backend could not start / 后台服务无法启动",
                               &format!("{}\n{}", exe.display(), e));
                    return Ok(());
                }
            };
            app.state::<Backend>().0.lock().unwrap().replace(child);

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let addr = SocketAddr::from(([127, 0, 0, 1], port));
                let started = Instant::now();
                loop {
                    if TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok() {
                        let url = format!("http://127.0.0.1:{port}/#token={token}");
                        if let Ok(u) = Url::parse(&url) {
                            let _ = win.navigate(u);
                        }
                        return;
                    }
                    let exited = handle
                        .state::<Backend>()
                        .0
                        .lock()
                        .unwrap()
                        .as_mut()
                        .and_then(|c| c.try_wait().ok().flatten());
                    if exited.is_some() || started.elapsed() > STARTUP_TIMEOUT {
                        let log = tail(&log_dir.join("backend-console.log"), 4000);
                        show_error(&win, "Rhizome backend stopped / 后台服务已退出",
                                   &format!("{}\n\n{}", log_dir.display(), log));
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(250));
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Rhizome");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Backend>().0.lock().unwrap().take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
