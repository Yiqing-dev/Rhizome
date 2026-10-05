// SPDX-License-Identifier: Apache-2.0
//! Rhizome desktop shell.
//!
//! Starts the bundled backend (`server/rhz.exe serve`, a PyInstaller one-folder build installed
//! next to the app), on a free localhost port with a token generated here, waits until it accepts
//! connections, then points the window at it. The backend serves both the REST API and the UI.
//!
//! The backend is watched for its whole life, not only at startup: if it stops, the window goes
//! back to the bundled local page and says why (with the end of its log), offering Retry; a
//! library that cannot be found (exit code 4) also offers "use the default location"; exit code
//! 75 is the backend asking to be restarted (after moving the library). A slow start (a database
//! upgrade, an antivirus scan of the frozen backend) shows a notice but is never killed.
//! A second launch focuses the running window; the backend stops when the app exits.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewWindow};

const SLOW_START: Duration = Duration::from_secs(60);
const EXIT_LIBRARY_MISSING: i32 = 4;
const EXIT_SCHEMA_TOO_NEW: i32 = 3;
const EXIT_RESTART: i32 = 75;

#[derive(Default)]
struct Shell {
    child: Mutex<Option<Child>>,
    /// bumped on every (re)start, so the monitor of a replaced backend stops quietly
    generation: AtomicU64,
    /// the bundled local page (error pages are shown there: it can call the commands below)
    local_url: Mutex<Option<Url>>,
    /// (title, detail, offer_reset) waiting to be shown once the local page has loaded
    pending_error: Mutex<Option<(String, String, bool)>>,
}

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

/// The file that remembers a library moved to another folder (same rule as the backend).
fn location_pointer() -> Option<PathBuf> {
    let base = if cfg!(windows) {
        PathBuf::from(std::env::var_os("APPDATA")?).join("Rhizome")
    } else if cfg!(target_os = "macos") {
        PathBuf::from(std::env::var_os("HOME")?).join("Library/Application Support/Rhizome")
    } else if let Some(x) = std::env::var_os("XDG_DATA_HOME") {
        PathBuf::from(x).join("rhizome")
    } else {
        PathBuf::from(std::env::var_os("HOME")?).join(".local/share/rhizome")
    };
    Some(base.join("location.json"))
}

fn log_dir(app: &AppHandle) -> PathBuf {
    app.path().app_log_dir().unwrap_or_else(|_| std::env::temp_dir().join("Rhizome"))
}

/// Shell problems go to shell.log next to backend-console.log (the window may not exist yet).
fn shell_log(app: &AppHandle, msg: &str) {
    let dir = log_dir(app);
    let _ = fs::create_dir_all(&dir);
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(dir.join("shell.log")) {
        let _ = writeln!(f, "{:?} {msg}", std::time::SystemTime::now());
    }
}

fn spawn_backend(exe: &Path, port: u16, token: &str, log_dir: &Path) -> std::io::Result<Child> {
    fs::create_dir_all(log_dir)?;
    let console = log_dir.join("backend-console.log");
    // keep the previous run's output (a crash is usually diagnosed from the launch before)
    let _ = fs::rename(&console, log_dir.join("backend-console.prev.log"));
    let log = File::create(&console)?;
    let mut cmd = Command::new(exe);
    cmd.args(["serve", "--host", "127.0.0.1", "--port", &port.to_string()])
        .env("RHIZOME_API_TOKEN", token)
        .env("RHIZOME_CONSOLE_LOG", &console) // rhz diag bundles it
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

/// Minimal JS string literal escaping (avoids pulling in serde_json for one call).
fn js_string(s: &str) -> String {
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

fn error_js(title: &str, detail: &str, offer_reset: bool) -> String {
    let reset = if offer_reset {
        "<button onclick=\"window.__TAURI_INTERNALS__.invoke('reset_library_location')\">改用默认位置 / Use the default location</button>"
    } else {
        ""
    };
    format!(
        "document.body.innerHTML = '<main style=\"font-family:system-ui,sans-serif;max-width:720px;margin:10vh auto;padding:0 24px;text-align:left\"><h2>' + {t} + '</h2><pre style=\"white-space:pre-wrap;background:rgba(127,127,127,.12);padding:12px;border-radius:8px;max-height:50vh;overflow:auto\">' + {d} + '</pre><p style=\"display:flex;gap:12px\"><button onclick=\"window.__TAURI_INTERNALS__.invoke(\\'retry_backend\\')\">重试 / Retry</button>{r}</p></main>';",
        t = js_string(title),
        d = js_string(detail),
        r = reset.replace('\'', "\\'")
    )
}

fn tail(path: &Path, max: usize) -> String {
    let text = fs::read_to_string(path).unwrap_or_default();
    let mut start = text.len().saturating_sub(max);
    while !text.is_char_boundary(start) {
        start += 1;
    }
    text[start..].to_string()
}

/// Scheme + host (Url::origin is opaque, never equal, for the tauri:// scheme on macOS / Linux).
fn same_site(a: &Url, b: &Url) -> bool {
    a.scheme() == b.scheme() && a.host_str() == b.host_str()
}

fn main_window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("main")
}

fn on_local_page(app: &AppHandle) -> bool {
    let local = app.state::<Shell>().local_url.lock().unwrap().clone();
    match (main_window(app).and_then(|w| w.url().ok()), local) {
        (Some(cur), Some(local)) => same_site(&cur, &local),
        _ => true, // nothing navigated yet: still the bundled page
    }
}

/// Show an error on the bundled page (navigating back to it first if the window shows the
/// backend's UI, which cannot call the shell's commands).
fn report(app: &AppHandle, title: &str, detail: &str, offer_reset: bool) {
    shell_log(app, &format!("{title}: {}", detail.lines().last().unwrap_or("")));
    app.state::<Shell>()
        .pending_error
        .lock()
        .unwrap()
        .replace((title.to_string(), detail.to_string(), offer_reset));
    let Some(win) = main_window(app) else { return };
    if on_local_page(app) {
        let _ = win.eval(&error_js(title, detail, offer_reset));
    } else if let Some(local) = app.state::<Shell>().local_url.lock().unwrap().clone() {
        let _ = win.navigate(local); // the error is shown when the page has loaded
    }
}

/// (Re)start the backend and follow it. Never fails: problems are shown in the window.
fn start_backend(app: &AppHandle) {
    let shell = app.state::<Shell>();
    if let Some(mut old) = shell.child.lock().unwrap().take() {
        let _ = old.kill();
        let _ = old.wait();
    }
    shell.pending_error.lock().unwrap().take();
    let generation = shell.generation.fetch_add(1, Ordering::SeqCst) + 1;
    if !on_local_page(app) {
        if let (Some(win), Some(local)) = (main_window(app), shell.local_url.lock().unwrap().clone()) {
            let _ = win.navigate(local); // back to the spinner while it starts
        }
    }
    let logs = log_dir(app);
    let resource_dir = match app.path().resource_dir() {
        Ok(d) => d,
        Err(e) => return report(app, "Rhizome is damaged / 安装不完整", &format!("{e}"), false),
    };
    let exe = server_exe(&resource_dir);
    let port = match free_port() {
        Ok(p) => p,
        Err(e) => return report(app, "No free local port / 无法分配本地端口", &format!("{e}"), false),
    };
    let token = new_token();
    let child = match spawn_backend(&exe, port, &token, &logs) {
        Ok(c) => c,
        Err(e) => {
            return report(
                app,
                "Rhizome backend could not start / 后台服务无法启动",
                &format!(
                    "{}\n{e}\n\n杀毒软件可能隔离了这个文件；请检查隔离区并重新安装。\nAn antivirus may have quarantined this file; check its quarantine and reinstall.",
                    exe.display()
                ),
                false,
            )
        }
    };
    shell.child.lock().unwrap().replace(child);
    let app = app.clone();
    std::thread::spawn(move || monitor(app, generation, port, token, logs));
}

fn monitor(app: AppHandle, generation: u64, port: u16, token: String, logs: PathBuf) {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let started = Instant::now();
    let mut navigated = false;
    let mut slow_notice = false;
    loop {
        let shell = app.state::<Shell>();
        if shell.generation.load(Ordering::SeqCst) != generation {
            return; // replaced by a newer start
        }
        if !navigated && TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok() {
            if let (Some(win), Ok(u)) = (main_window(&app), Url::parse(&format!("http://127.0.0.1:{port}/#token={token}"))) {
                // replace the loading page in the history instead of pushing the app after it:
                // mouse Back / Alt+Left must not land on the dead spinner
                let js = format!("location.replace({})", js_string(u.as_str()));
                if win.eval(&js).is_err() {
                    let _ = win.navigate(u);
                }
            }
            navigated = true;
        }
        let exited = shell.child.lock().unwrap().as_mut().and_then(|c| c.try_wait().ok().flatten());
        if let Some(status) = exited {
            let code = status.code().unwrap_or(-1);
            if code == EXIT_RESTART {
                shell_log(&app, "backend asked for a restart");
                return start_backend(&app);
            }
            let log = tail(&logs.join("backend-console.log"), 4000);
            let (title, offer_reset) = match code {
                EXIT_LIBRARY_MISSING => ("Library not found / 找不到资料库", true),
                EXIT_SCHEMA_TOO_NEW => ("Library is from a newer Rhizome / 资料库来自更新的版本", false),
                _ if navigated => ("Rhizome backend stopped / 后台服务意外退出", false),
                _ => ("Rhizome backend could not start / 后台服务无法启动", false),
            };
            return report(&app, title, &format!("{}\n\n{log}", logs.display()), offer_reset);
        }
        if !navigated && !slow_notice && started.elapsed() > SLOW_START {
            // still starting: a library upgrade or an antivirus scan; never kill it
            slow_notice = true;
            if let Some(win) = main_window(&app) {
                let _ = win.eval("var p=document.querySelector('p');if(p)p.textContent='仍在启动：可能正在升级资料库或被杀毒软件扫描，请勿关闭。 · Still starting: the library may be upgrading or an antivirus is scanning. Please keep it open.';");
            }
        }
        std::thread::sleep(if navigated { Duration::from_secs(2) } else { Duration::from_millis(250) });
    }
}

#[tauri::command]
fn retry_backend(app: AppHandle) {
    start_backend(&app);
}

#[tauri::command]
fn reset_library_location(app: AppHandle) {
    if let Some(p) = location_pointer() {
        let _ = fs::remove_file(p);
    }
    start_backend(&app);
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .manage(Shell::default())
        .invoke_handler(tauri::generate_handler![retry_backend, reset_library_location])
        .on_page_load(|webview, payload| {
            if payload.event() != PageLoadEvent::Finished || webview.label() != "main" {
                return;
            }
            let app = webview.app_handle();
            let shell = app.state::<Shell>();
            let local = shell.local_url.lock().unwrap().clone();
            if local.as_ref().map(|l| same_site(l, payload.url())).unwrap_or(false) {
                if let Some((t, d, r)) = shell.pending_error.lock().unwrap().clone() {
                    let _ = webview.eval(&error_js(&t, &d, r));
                }
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            if let Some(win) = main_window(&handle) {
                if let Ok(u) = win.url() {
                    handle.state::<Shell>().local_url.lock().unwrap().replace(u);
                }
            }
            start_backend(&handle);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Rhizome");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let shell = handle.state::<Shell>();
            shell.generation.fetch_add(1, Ordering::SeqCst); // monitors stop reporting
            let child = shell.child.lock().unwrap().take();
            if let Some(mut child) = child {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
