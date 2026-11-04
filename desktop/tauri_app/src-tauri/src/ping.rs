//! Connects the lolPing input helper to Beacon's overlay and MQTT bridge.
use super::{run_persistent_bridge, BridgeState};
use serde_json::{json, Value};
use std::{
    fs,
    io::{BufRead, BufReader, Write},
    process::Command,
    sync::{Arc, Mutex},
    time::Duration,
};
use tauri::{Emitter, Manager, Monitor, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

#[cfg(target_os = "macos")]
use tauri_nspanel::{tauri_panel, CollectionBehavior, ManagerExt as _, PanelLevel, StyleMask, WebviewWindowExt as _};

#[cfg(target_os = "macos")]
tauri_panel! {
    panel!(PingOverlayPanel {
        config: {
            can_become_key_window: false,
            can_become_main_window: false,
            is_floating_panel: true
        }
    })
}

#[cfg(target_os = "macos")]
use std::os::{fd::IntoRawFd, unix::net::UnixStream};
#[cfg(target_os = "windows")]
use std::process::{ChildStdin, Stdio};

#[cfg(target_os = "macos")]
type InputWriter = UnixStream;
#[cfg(target_os = "windows")]
type InputWriter = ChildStdin;

#[cfg(target_os = "macos")]
extern "C" {
    fn beacon_ping_run(socket: i32);
    fn beacon_ping_prompt_accessibility();
}

#[cfg(target_os = "windows")]
const HELPER_NAME: &str = "beacon-ping-hook.exe";
#[cfg(target_os = "windows")]
const HELPER: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/beacon-ping-hook.exe"));

#[derive(Default)]
pub struct PingRuntime {
    helper: Arc<Mutex<HelperState>>,
    overlay: Arc<Mutex<Overlay>>,
    settings_write: Mutex<()>,
}

#[derive(Default)]
struct HelperState {
    input: Option<InputWriter>,
    running: bool,
    ready: bool,
    status: &'static str,
}

fn preferences_path(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    Ok(app.path().app_local_data_dir().map_err(|e| e.to_string())?.join("ping-preferences.json"))
}

fn preferences(app: &tauri::AppHandle) -> Value {
    preferences_path(app)
        .ok()
        .and_then(|path| fs::read(path).ok())
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_else(|| json!({}))
}

fn configure(input: &mut InputWriter, prefs: &Value) -> std::io::Result<()> {
    let trigger_vk = prefs["triggerVk"].as_u64().filter(|vk| {
        (1..=254).contains(vk) && !matches!(vk, 0x08 | 0x09 | 0x0d | 0x1b | 0x20 | 0x25..=0x28)
    });
    let mapped_key = prefs["trigger"] == "logi-f13";
    let trigger = match prefs["trigger"].as_str() {
        Some("logi-f13") => "vk",
        Some(value @ ("alt" | "ctrl" | "shift" | "win" | "capslock" | "mouse4" | "mouse5")) => value,
        Some("vk") if trigger_vk.is_some() => "vk",
        _ => "alt",
    };
    let toggle = &prefs["toggleHotkey"];
    let toggle_mods = toggle["mods"].as_u64().filter(|mods| (1..=15).contains(mods) && mods & 0x0b != 0);
    let toggle_vk = toggle["vk"].as_u64().filter(|vk| (1..=254).contains(vk));
    let (toggle_mods, toggle_vk) = toggle_mods.zip(toggle_vk).unwrap_or((3, 0x50));
    let config = json!({
        "type":"config", "trigger":trigger, "triggerVk":if mapped_key { trigger_vk.filter(|vk| (0x70..=0x83).contains(vk)).unwrap_or(0x7c) } else { trigger_vk.unwrap_or(0) }, "mappedKey":mapped_key,
        "toggleMods":toggle_mods, "toggleVk":toggle_vk,
        "clickPing":prefs["clickPing"] == true || prefs["emoteClick"] == true, "gameCheck":false,
        "emoteTrigger":"off", "emoteClick":false,
        "askTrigger":"off", "textVk":0, "panelVk":0, "enabled":prefs["enabled"] != false,
        "dragThresholdPx":prefs["dragThreshold"].as_u64().unwrap_or(8).clamp(1, 50),
    });
    writeln!(input, "{config}")?;
    input.flush()
}

#[tauri::command]
pub fn ping_preferences(app: tauri::AppHandle) -> Value {
    preferences(&app)
}

#[tauri::command]
pub fn save_ping_preferences(app: tauri::AppHandle, runtime: tauri::State<'_, PingRuntime>, settings: Value) -> Result<(), String> {
    let _write = runtime.settings_write.lock().map_err(|e| e.to_string())?;
    let bytes = serde_json::to_vec(&settings).map_err(|e| e.to_string())?;
    if !settings.is_object() || bytes.len() > 12 * 1024 * 1024 {
        return Err("Ping 配置或表情库过大（上限 12 MB）".into());
    }
    let path = preferences_path(&app)?;
    let parent = path.parent().ok_or("Ping 配置路径无效")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, bytes).map_err(|e| e.to_string())?;
    fs::rename(temporary, path).map_err(|e| e.to_string())?;
    if let Some(window) = app.get_webview_window("ping-overlay") {
        window.emit("beacon-ping-overlay", json!({"type":"preferences"})).map_err(|e| e.to_string())?;
        runtime.overlay.lock().unwrap().event(&window, json!({"type":"toggled", "enabled":settings["enabled"] != false}));
    }
    let mut helper = runtime.helper.lock().map_err(|e| e.to_string())?;
    if helper.ready {
        if let Some(input) = helper.input.as_mut() {
            configure(input, &settings).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[tauri::command]
pub fn suspend_ping_input(runtime: tauri::State<'_, PingRuntime>, on: bool) -> Result<(), String> {
    let mut helper = runtime.helper.lock().map_err(|e| e.to_string())?;
    if helper.ready {
        if let Some(input) = helper.input.as_mut() {
            let command = json!({"type":"suspend", "on":on});
            writeln!(input, "{command}").and_then(|_| input.flush()).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[tauri::command]
pub fn ping_input_status(runtime: tauri::State<'_, PingRuntime>) -> Result<Value, String> {
    let state = runtime.helper.lock().map_err(|e| e.to_string())?;
    Ok(json!({"status": state.status}))
}

#[cfg(target_os = "macos")]
#[tauri::command]
pub fn open_ping_accessibility() -> Result<(), String> {
    // The event tap and the prompt share the Beacon.app process identity.
    unsafe { beacon_ping_prompt_accessibility() };
    Command::new("open")
        .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
        .spawn().map_err(|e| e.to_string())?;
    Ok(())
}

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
    #[cfg(target_os = "macos")]
    {
        let panel = window.to_panel::<PingOverlayPanel>()?;
        panel.set_style_mask(StyleMask::empty().nonactivating_panel().into());
        panel.set_level(PanelLevel::ScreenSaver.value());
        panel.set_collection_behavior(
            CollectionBehavior::new()
                .can_join_all_spaces()
                .full_screen_auxiliary()
                .transient()
                .ignores_cycle()
                .into(),
        );
        panel.set_hides_on_deactivate(false);
    }
    window.set_ignore_cursor_events(true)?;
    let app_handle = app.handle().clone();
    let runtime = app.state::<PingRuntime>();
    let overlay = runtime.overlay.clone();
    overlay.lock().unwrap().paused = preferences(&app_handle)["enabled"] == false;
    let bridge = app.state::<BridgeState>().inner().clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(150));
        if let Ok(pings) = run_persistent_bridge(&bridge, "take-pings", &json!({})) {
            if pings.as_array().is_some_and(|list| !list.is_empty()) {
                let (target, overlay) = (window.clone(), overlay.clone());
                let _ = window.run_on_main_thread(move || overlay.lock().unwrap().remote(&target, &pings));
            }
        }
    });
    Ok(())
}

#[cfg(target_os = "windows")]
fn helper_path(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    let dir = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join(HELPER_NAME);
    if fs::read(&path).ok().as_deref() != Some(HELPER) {
        let temp = dir.join(format!("{HELPER_NAME}.tmp"));
        fs::write(&temp, HELPER).map_err(|e| e.to_string())?;
        fs::rename(temp, &path).map_err(|e| e.to_string())?;
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
    wheel: Option<Monitor>,
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
        #[cfg(target_os = "macos")]
        {
            window.app_handle().get_webview_panel(window.label())
                .map_err(|_| tauri::Error::WindowNotFound)?
                .order_front_regardless();
            Ok(())
        }
        #[cfg(target_os = "windows")]
        window.show()
    }

    fn event(&mut self, window: &WebviewWindow, event: Value) {
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
        let monitor = if matches!(event_type, "wheelOpen" | "click") {
            let Ok(monitors) = window.available_monitors() else { return };
            let Some(monitor) = monitors.into_iter().find(|monitor| {
                let p = normalized(monitor, point);
                (0.0..1.0).contains(&p.x) && (0.0..1.0).contains(&p.y)
            }) else { return };
            if self.show(window, monitor.clone()).is_err() {
                return;
            }
            if event_type == "wheelOpen" {
                self.wheel = Some(monitor.clone());
            }
            monitor
        } else if matches!(event_type, "wheelMove" | "wheelRelease") {
            let Some(monitor) = self.wheel.clone() else { return };
            if event_type == "wheelRelease" {
                self.wheel = None;
            }
            monitor
        } else {
            return;
        };
        let p = normalized(&monitor, point);
        let _ = window.emit("beacon-ping-overlay", json!({
            "type":event_type, "x":p.x,"y":p.y
        }));
    }

    fn remote(&mut self, window: &WebviewWindow, pings: &Value) {
        if self.paused {
            return;
        }
        let monitor = window.primary_monitor().ok().flatten();
        let Some(monitor) = monitor else { return };
        if self.wheel.as_ref().is_some_and(|current| current.position() != monitor.position()) {
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
pub fn start_ping_overlay(app: tauri::AppHandle, runtime: tauri::State<'_, PingRuntime>) -> Result<(), String> {
    let window = app.get_webview_window("ping-overlay").ok_or("Ping 覆盖层尚未启动")?;
    let mut state = runtime.helper.lock().map_err(|e| e.to_string())?;
    if state.running {
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    let (input, output) = {
        let (stream, native) = UnixStream::pair().map_err(|e| e.to_string())?;
        let output = stream.try_clone().map_err(|e| e.to_string())?;
        std::thread::spawn(move || {
            // The native function owns the socket and closes it after removing the event tap.
            unsafe { beacon_ping_run(native.into_raw_fd()) };
        });
        (Some(stream), output)
    };
    #[cfg(target_os = "windows")]
    let (input, output, mut child) = {
        use std::os::windows::process::CommandExt;
        let mut child = Command::new(helper_path(&app)?)
            .creation_flags(0x08000000)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn().map_err(|e| e.to_string())?;
        (child.stdin.take(), child.stdout.take().expect("piped stdout"), child)
    };
    state.input = input;
    state.running = true;
    state.ready = false;
    state.status = "starting";
    let helper = runtime.helper.clone();
    let overlay = runtime.overlay.clone();

    std::thread::spawn(move || {
        for line in BufReader::new(output).lines().map_while(Result::ok) {
            let Ok(event) = serde_json::from_str::<Value>(&line) else { continue };
            match event["type"].as_str() {
                Some("ready") => {
                    let mut state = helper.lock().unwrap();
                    if let Some(input) = state.input.as_mut() {
                        if configure(input, &preferences(&app)).is_err() {
                            break;
                        }
                    }
                    state.status = "ready";
                    state.ready = true;
                }
                Some("error") => {
                    helper.lock().unwrap().status = if event["code"] == "noAccess" { "noAccess" } else { "failed" };
                }
                _ => {
                    let (target, overlay) = (window.clone(), overlay.clone());
                    let _ = window.run_on_main_thread(move || overlay.lock().unwrap().event(&target, event));
                }
            }
        }
        helper.lock().unwrap().input.take();
        #[cfg(target_os = "windows")]
        let _ = child.wait();
        {
            let mut state = helper.lock().unwrap();
            state.ready = false;
            state.running = false;
            if state.status != "noAccess" {
                state.status = "failed";
            }
        }
        let target = window.clone();
        let _ = window.run_on_main_thread(move || overlay.lock().unwrap().event(&target, json!({"type":"closed"})));
    });
    Ok(())
}
