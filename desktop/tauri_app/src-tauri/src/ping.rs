//! Connects the lolPing input helper to Beacon's overlay and MQTT bridge.
use super::{run_persistent_bridge, BridgeState};
use serde_json::{json, Value};
use std::{
    fs,
    io::{BufRead, BufReader, Write},
    process::{Command, Stdio},
    sync::{mpsc, Arc, Mutex},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{Emitter, Manager, Monitor, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

#[cfg(target_os = "macos")]
const HELPER_NAME: &str = "beacon-ping-hook";
#[cfg(target_os = "macos")]
const HELPER: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/beacon-ping-hook"));
#[cfg(target_os = "windows")]
const HELPER_NAME: &str = "beacon-ping-hook.exe";
#[cfg(target_os = "windows")]
const HELPER: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/beacon-ping-hook.exe"));

// lolPing v0.6.0 default compass order, clockwise from north.
const KINDS: [&str; 8] = ["danger", "push", "omw", "allin", "assist", "needvision", "missing", "enemyvision"];

#[derive(Default)]
pub struct PingRuntime(Mutex<bool>);

pub fn setup(app: &tauri::App) -> tauri::Result<()> {
    let window = WebviewWindowBuilder::new(app, "ping-overlay", WebviewUrl::App("index.html?view=ping".into()))
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .visible_on_all_workspaces(true)
        .skip_taskbar(true)
        .shadow(false)
        .visible(false)
        .focused(false)
        .focusable(false)
        .build()?;
    window.set_ignore_cursor_events(true)
}

fn helper_path(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    let dir = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join(HELPER_NAME);
    if fs::read(&path).ok().as_deref() != Some(HELPER) {
        let temp = dir.join(format!("{HELPER_NAME}.tmp"));
        fs::write(&temp, HELPER).map_err(|e| e.to_string())?;
        fs::rename(temp, &path).map_err(|e| e.to_string())?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    }
    Ok(path)
}

#[derive(Clone, Copy)]
struct Point {
    x: f64,
    y: f64,
}

fn normalized(monitor: &Monitor, point: Point) -> Point {
    let origin = monitor.position();
    let size = monitor.size();
    let scale = if cfg!(target_os = "macos") { monitor.scale_factor() } else { 1.0 };
    Point {
        x: (point.x * scale - f64::from(origin.x)) / f64::from(size.width),
        y: (point.y * scale - f64::from(origin.y)) / f64::from(size.height),
    }
}

#[derive(Default)]
struct Overlay {
    wheel: Option<(Point, Monitor)>,
    screen: Option<Monitor>,
    paused: bool,
}

impl Overlay {
    fn show(&mut self, window: &WebviewWindow, monitor: Monitor) -> tauri::Result<()> {
        if self.screen.as_ref().map(|m| (m.position(), m.size())) != Some((monitor.position(), monitor.size())) {
            window.emit("beacon-ping-overlay", json!({"type":"clear"}))?;
            #[cfg(target_os = "macos")]
            {
                let scale = monitor.scale_factor();
                window.set_position(monitor.position().to_logical::<f64>(scale))?;
                window.set_size(monitor.size().to_logical::<f64>(scale))?;
            }
            #[cfg(target_os = "windows")]
            {
                window.set_position(*monitor.position())?;
                window.set_size(*monitor.size())?;
            }
            self.screen = Some(monitor);
        }
        window.show()
    }

    fn event(&mut self, window: &WebviewWindow, event: Value, outgoing: &mpsc::SyncSender<Value>) {
        let event_type = event["type"].as_str().unwrap_or_default();
        if event_type == "toggled" {
            self.paused = event["enabled"] != true;
            if self.paused {
                let _ = window.emit("beacon-ping-overlay", json!({"type":"clear"}));
                let _ = window.hide();
            }
        }
        if matches!(event_type, "cancel" | "toggled" | "closed") {
            self.wheel = None;
            let _ = window.emit("beacon-ping-overlay", json!({"type":"close"}));
            return;
        }
        if event["wheel"] != "ping" {
            return;
        }
        let (Some(x), Some(y)) = (event["x"].as_f64(), event["y"].as_f64()) else { return };
        let point = Point { x, y };
        if event_type == "wheelOpen" {
            let Ok(monitors) = window.available_monitors() else { return };
            let Some(monitor) = monitors.into_iter().find(|monitor| {
                let p = normalized(monitor, point);
                (0.0..1.0).contains(&p.x) && (0.0..1.0).contains(&p.y)
            }) else { return };
            if self.show(window, monitor.clone()).is_ok() {
                self.wheel = Some((point, monitor.clone()));
                let p = normalized(&monitor, point);
                let _ = window.emit("beacon-ping-overlay", json!({"type":"open","x":p.x,"y":p.y}));
            }
            return;
        }

        let Some((origin, monitor)) = &self.wheel else { return };
        let dx = point.x - origin.x;
        let dy = point.y - origin.y;
        let scale = if cfg!(target_os = "macos") { 1.0 } else { monitor.scale_factor() };
        let kind = (dx.hypot(dy) >= 36.0 * scale).then(|| {
            let sector = ((dx.atan2(-dy) / std::f64::consts::FRAC_PI_4 + 0.5).floor() as i32).rem_euclid(8);
            KINDS[sector as usize]
        });
        if event_type == "wheelMove" {
            let _ = window.emit("beacon-ping-overlay", json!({"type":"select","kind":kind}));
        } else if event_type == "wheelRelease" {
            let p = normalized(monitor, *origin);
            self.wheel = None;
            let _ = window.emit("beacon-ping-overlay", json!({"type":"close"}));
            if let Some(kind) = kind {
                let ping = json!({"kind":kind,"x":p.x,"y":p.y});
                let _ = window.emit("beacon-ping-overlay", json!({"type":"marker","ping":ping}));
                if let Ok(elapsed) = SystemTime::now().duration_since(UNIX_EPOCH) {
                    let _ = outgoing.try_send(json!({"kind":kind,"x":p.x,"y":p.y,"time":elapsed.as_secs_f64()}));
                }
            }
        }
    }

    fn remote(&mut self, window: &WebviewWindow, pings: &Value) {
        if self.paused {
            return;
        }
        let monitor = window.primary_monitor().ok().flatten();
        let Some(monitor) = monitor else { return };
        if self.wheel.as_ref().is_some_and(|(_, current)| current.position() != monitor.position()) {
            return;
        }
        if self.show(window, monitor).is_ok() {
            for ping in pings.as_array().into_iter().flatten() {
                let _ = window.emit("beacon-ping-overlay", json!({"type":"marker","ping":ping}));
            }
        }
    }
}

#[tauri::command]
pub fn start_ping_overlay(window: WebviewWindow, runtime: tauri::State<'_, PingRuntime>) -> Result<(), String> {
    if window.label() != "ping-overlay" {
        return Err("无效的 Ping 覆盖层".into());
    }
    let mut started = runtime.0.lock().map_err(|e| e.to_string())?;
    if *started {
        return Ok(());
    }
    let app = window.app_handle().clone();
    let mut command = Command::new(helper_path(&app)?);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().map_err(|e| e.to_string())?;
    let mut stdin = child.stdin.take().expect("piped stdin");
    let stdout = child.stdout.take().expect("piped stdout");
    let (send, receive) = mpsc::sync_channel(16);
    let overlay = Arc::new(Mutex::new(Overlay::default()));
    let local_overlay = overlay.clone();
    let local_window = window.clone();
    let bridge = app.state::<BridgeState>().inner().clone();

    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let Ok(event) = serde_json::from_str::<Value>(&line) else { continue };
            if event["type"] == "ready" {
                let config = json!({"type":"config","trigger":"alt","clickPing":false,"gameCheck":true,
                    "emoteTrigger":"off","askTrigger":"off","textVk":0,"panelVk":0,"enabled":true});
                if writeln!(stdin, "{config}").and_then(|_| stdin.flush()).is_err() {
                    break;
                }
            } else if event["type"] == "error" {
                eprintln!("Beacon Ping helper: {}", event["message"]);
            } else {
                let (window, overlay, send) = (local_window.clone(), local_overlay.clone(), send.clone());
                let _ = local_window.run_on_main_thread(move || overlay.lock().unwrap().event(&window, event, &send));
            }
        }
        drop(stdin);
        let _ = child.wait();
        let window = local_window.clone();
        let _ = local_window.run_on_main_thread(move || local_overlay.lock().unwrap().event(&window, json!({"type":"closed"}), &send));
    });

    std::thread::spawn(move || {
        loop {
            match receive.recv_timeout(Duration::from_millis(150)) {
                Ok(ping) => { let _ = run_persistent_bridge(&bridge, "send-ping", &ping); }
                Err(mpsc::RecvTimeoutError::Disconnected) => std::thread::sleep(Duration::from_millis(150)),
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
            if let Ok(pings) = run_persistent_bridge(&bridge, "take-pings", &json!({})) {
                if pings.as_array().is_some_and(|list| !list.is_empty()) {
                    let (target, overlay) = (window.clone(), overlay.clone());
                    let _ = window.run_on_main_thread(move || overlay.lock().unwrap().remote(&target, &pings));
                }
            }
        }
    });
    *started = true;
    Ok(())
}
