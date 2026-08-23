use serde_json::Value;
use std::{io::{BufRead, BufReader, Write}, path::PathBuf, process::{Child, ChildStdin, ChildStdout, Command, Stdio}, sync::{Arc, Mutex, atomic::{AtomicBool, AtomicU8, Ordering}}};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use btleplug::api::{Central, Characteristic, Manager as _, Peripheral as _, ScanFilter, WriteType};
use btleplug::platform::{Manager as BleManager, Peripheral as BlePeripheral};
use tokio::sync::Mutex as AsyncMutex;
use uuid::Uuid;
use tauri::{Manager, WindowEvent};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

fn bridge_script() -> Result<PathBuf, String> {
    [
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tauri_bridge.py"),
        std::env::current_exe().map_err(|error| error.to_string())?
            .parent().unwrap_or_else(|| std::path::Path::new("."))
            .join("resources/tauri_bridge.py"),
    ].into_iter().find(|path| path.is_file())
        .ok_or_else(|| "找不到 Python 后台桥接脚本，请重新安装客户端".to_string())
}

#[cfg(target_os = "macos")]
fn bundled_bridge() -> Option<PathBuf> {
    let name = if cfg!(target_arch = "aarch64") {
        "bridge-aarch64"
    } else {
        "bridge-x86_64"
    };
    let executable = std::env::current_exe().ok()?;
    let path = executable.parent()?.parent()?.join("Resources/binaries").join(name);
    path.is_file().then_some(path)
}

#[cfg(not(target_os = "macos"))]
fn bundled_bridge() -> Option<PathBuf> { None }

struct BridgeProcess {
    _child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

#[derive(Clone)]
struct BridgeState(Arc<Mutex<Option<BridgeProcess>>>);

#[derive(Clone)]
struct NativeBleState {
    peripheral: Arc<AsyncMutex<Option<BlePeripheral>>>,
    write_lock: Arc<AsyncMutex<()>>,
    ota_progress: Arc<AsyncMutex<Value>>,
    last_dashboard: Arc<AsyncMutex<Option<String>>>,
    preview_active: Arc<AsyncMutex<bool>>,
    heartbeat_running: Arc<AtomicBool>,
    sequence: Arc<AtomicU8>,
    connected_device_id: Arc<AsyncMutex<Option<String>>>,
}

impl NativeBleState {
    fn new() -> Self {
        Self {
            peripheral: Arc::new(AsyncMutex::new(None)),
            write_lock: Arc::new(AsyncMutex::new(())),
            ota_progress: Arc::new(AsyncMutex::new(serde_json::json!({
                "state": "idle", "progress": 0, "message": ""
            }))),
            last_dashboard: Arc::new(AsyncMutex::new(None)),
            preview_active: Arc::new(AsyncMutex::new(false)),
            heartbeat_running: Arc::new(AtomicBool::new(false)),
            sequence: Arc::new(AtomicU8::new(0)),
            connected_device_id: Arc::new(AsyncMutex::new(None)),
        }
    }
}

const DEVICE_PREFIX: &str = "Codex-Light-";
const CONTROL_UUID: &str = "0200c310-7625-819e-934c-32b8e4177d6a";
const OTA_UUID: &str = "0300c310-7625-819e-934c-32b8e4177d6a";

fn characteristic(peripheral: &BlePeripheral, uuid: &str) -> Result<Characteristic, String> {
    let uuid = Uuid::parse_str(uuid).map_err(|error| error.to_string())?;
    peripheral.characteristics().into_iter().find(|item| item.uuid == uuid)
        .ok_or_else(|| "灯板固件缺少所需蓝牙服务".to_string())
}

async fn connect_peripheral(address: Option<&str>, device_id: Option<&str>) -> Result<BlePeripheral, String> {
    let manager = BleManager::new().await.map_err(|error| format!("蓝牙初始化失败：{error}"))?;
    let adapters = manager.adapters().await.map_err(|error| format!("读取蓝牙适配器失败：{error}"))?;
    for adapter in &adapters {
        adapter.start_scan(ScanFilter::default()).await.map_err(|error| format!("启动蓝牙扫描失败：{error}"))?;
    }
    let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(10);
    loop {
        for adapter in &adapters {
            for peripheral in adapter.peripherals().await.map_err(|error| error.to_string())? {
                let id_matches = address.is_some_and(|value| peripheral.id().to_string() == value);
                let name_matches = if let Some(expected) = device_id {
                    peripheral.properties().await.map_err(|error| error.to_string())?
                        .and_then(|props| props.local_name)
                        .and_then(|name| name.strip_prefix(DEVICE_PREFIX).map(str::to_owned))
                        .is_some_and(|value| value.eq_ignore_ascii_case(expected))
                } else { false };
                if id_matches || name_matches {
                    for item in &adapters { let _ = item.stop_scan().await; }
                    if !peripheral.is_connected().await.unwrap_or(false) {
                        peripheral.connect().await.map_err(|error| format!("连接灯板失败：{error}"))?;
                    }
                    peripheral.discover_services().await.map_err(|error| format!("读取灯板服务失败：{error}"))?;
                    return Ok(peripheral);
                }
            }
        }
        if tokio::time::Instant::now() >= deadline { break; }
        tokio::time::sleep(std::time::Duration::from_millis(250)).await;
    }
    for adapter in &adapters { let _ = adapter.stop_scan().await; }
    Err("没有发现目标灯板".into())
}

fn start_bridge_process() -> Result<BridgeProcess, String> {
    if let Some(binary) = bundled_bridge() {
        let working_dir = binary.parent().ok_or("内置后台路径无效")?.to_path_buf();
        let mut command = Command::new(&binary);
        command.arg("serve").current_dir(&working_dir)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        let mut child = command.spawn().map_err(|error| format!("内置后台启动失败：{error}"))?;
        let stdin = child.stdin.take().ok_or("内置后台输入管道创建失败")?;
        let stdout = child.stdout.take().ok_or("内置后台输出管道创建失败")?;
        return Ok(BridgeProcess { _child: child, stdin, stdout: BufReader::new(stdout) });
    }
    let script = bridge_script()?;
    let working_dir = script.parent().ok_or("后台脚本路径无效")?;
    let interpreters: &[(&str, &[&str])] = if cfg!(windows) {
        &[("pythonw", &[]), ("py", &["-3"]), ("python", &[])]
    } else { &[("python3", &[]), ("python", &[])] };
    for (program, prefix) in interpreters {
        let mut command = Command::new(program);
        command.args(*prefix).arg(&script).arg("serve").current_dir(working_dir)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        if let Ok(mut child) = command.spawn() {
            let Some(stdin) = child.stdin.take() else { continue };
            let Some(stdout) = child.stdout.take() else { continue };
            return Ok(BridgeProcess { _child: child, stdin, stdout: BufReader::new(stdout) });
        }
    }
    Err("未找到 Python 3，请先安装桌面端依赖".into())
}

fn run_persistent_bridge(state: &BridgeState, command: &str, payload: &Value) -> Result<Value, String> {
    let mut guard = state.0.lock().map_err(|_| "后台服务锁定失败".to_string())?;
    if guard.is_none() { *guard = Some(start_bridge_process()?); }
    let process = guard.as_mut().expect("后台进程应已启动");
    let request = serde_json::json!({"command": command, "payload": payload});
    if writeln!(process.stdin, "{}", request).is_err() || process.stdin.flush().is_err() {
        *guard = None;
        return Err("后台服务已退出，请重试".into());
    }
    let mut line = String::new();
    if process.stdout.read_line(&mut line).map_err(|error| error.to_string())? == 0 {
        *guard = None;
        return Err("后台服务没有返回数据".into());
    }
    let envelope: Value = serde_json::from_str(&line).map_err(|error| format!("后台返回了无效数据：{error}"))?;
    if let Some(error) = envelope.get("error").and_then(Value::as_str) { return Err(error.to_string()); }
    Ok(envelope.get("ok").cloned().unwrap_or(Value::Null))
}

async fn run_bridge_async(state: BridgeState, command: &'static str, payload: Value) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || run_persistent_bridge(&state, command, &payload))
        .await
        .map_err(|error| format!("后台任务异常：{error}"))?
}

#[tauri::command]
async fn get_dashboard(
    bridge: tauri::State<'_, BridgeState>,
    ble: tauri::State<'_, NativeBleState>,
) -> Result<Value, String> {
    let result = run_bridge_async(bridge.inner().clone(), "dashboard", serde_json::json!({})).await?;
    let signature = serde_json::to_string(&result).map_err(|error| error.to_string())?;
    let should_push = !*ble.preview_active.lock().await && {
        let mut previous = ble.last_dashboard.lock().await;
        if previous.as_ref() == Some(&signature) { false } else {
            *previous = Some(signature);
            true
        }
    };
    if should_push {
        // Startup remains responsive even when the bound board is asleep. A
        // failed background sync is retried only after the dashboard changes
        // or the user explicitly saves/applies again.
        let bridge_state = bridge.inner().clone();
        let ble_state = ble.inner().clone();
        tauri::async_runtime::spawn(async move {
            if native_apply(bridge_state, ble_state.clone(), serde_json::json!({})).await.is_err() {
                *ble_state.last_dashboard.lock().await = None;
            }
        });
    }
    Ok(result)
}

#[tauri::command]
async fn scan_devices(ble: tauri::State<'_, NativeBleState>) -> Result<Value, String> {
    // The Tauri process owns Bluetooth on both platforms. On macOS this makes
    // the permission request belong to the signed Beacon app instead of a
    // transient Python helper, so scanning cannot finish before authorization.
    if let (Some(peripheral), Some(device_id)) = (
        ble.peripheral.lock().await.clone(),
        ble.connected_device_id.lock().await.clone(),
    ) {
        if peripheral.is_connected().await.unwrap_or(false) {
            let properties = peripheral.properties().await.ok().flatten();
            return Ok(serde_json::json!({"devices": [{
                "name": format!("{DEVICE_PREFIX}{device_id}"),
                "device_id": device_id,
                "address": peripheral.id().to_string(),
                "rssi": properties.and_then(|value| value.rssi),
                "connected": true,
            }]}));
        }
    }
    let manager = BleManager::new().await.map_err(|error| format!("蓝牙初始化失败：{error}"))?;
    let adapters = manager.adapters().await.map_err(|error| format!("读取蓝牙适配器失败：{error}"))?;
    if adapters.is_empty() {
        return Err("未找到可用的蓝牙适配器".into());
    }
    for adapter in &adapters {
        adapter.start_scan(ScanFilter::default()).await
            .map_err(|error| format!("启动蓝牙扫描失败：{error}"))?;
    }
    // Keep the first scan alive while macOS presents and records the one-time
    // Bluetooth permission sheet. The same signed app process continues the
    // scan after approval instead of returning an early empty result.
    tokio::time::sleep(std::time::Duration::from_secs(12)).await;

    let mut devices: Vec<Value> = Vec::new();
    for adapter in &adapters {
        let peripherals = adapter.peripherals().await
            .map_err(|error| format!("读取蓝牙设备失败：{error}"))?;
        for peripheral in peripherals {
            let Some(properties) = peripheral.properties().await
                .map_err(|error| format!("读取设备属性失败：{error}"))? else { continue };
            let Some(name) = properties.local_name else { continue };
            let Some(device_id) = name.strip_prefix(DEVICE_PREFIX) else { continue };
            let device_id = device_id.to_ascii_uppercase();
            if device_id.len() != 6 || !device_id.chars().all(|value| value.is_ascii_hexdigit()) {
                continue;
            }
            if devices.iter().any(|item| item.get("device_id").and_then(Value::as_str) == Some(&device_id)) {
                continue;
            }
            devices.push(serde_json::json!({
                "name": name,
                "device_id": device_id,
                "address": peripheral.id().to_string(),
                "rssi": properties.rssi,
                "connected": peripheral.is_connected().await.unwrap_or(false),
            }));
        }
        let _ = adapter.stop_scan().await;
    }
    devices.sort_by(|left, right| {
        right.get("rssi").and_then(Value::as_i64)
            .cmp(&left.get("rssi").and_then(Value::as_i64))
    });
    Ok(serde_json::json!({"devices": devices}))
}

#[tauri::command]
async fn identify_device(state: tauri::State<'_, NativeBleState>, address: String) -> Result<Value, String> {
    let peripheral = {
        let held = state.peripheral.lock().await;
        match held.as_ref() {
            Some(item) if item.is_connected().await.unwrap_or(false) => item.clone(),
            _ => connect_peripheral(Some(&address), None).await?,
        }
    };
    let control = characteristic(&peripheral, CONTROL_UUID)?;
    let _guard = state.write_lock.lock().await;
    peripheral.write(&control, &[0xC3, 1, 4, 0], WriteType::WithResponse).await
        .map_err(|error| format!("发送识别动画失败：{error}"))?;
    *state.peripheral.lock().await = Some(peripheral);
    start_heartbeat(state.inner().clone());
    Ok(serde_json::json!({"ok": true}))
}

#[tauri::command]
async fn ota_start(
    state: tauri::State<'_, NativeBleState>,
    address: String,
    firmware_base64: String,
) -> Result<Value, String> {
    let firmware = BASE64.decode(firmware_base64).map_err(|_| "固件文件格式无效".to_string())?;
    if firmware.is_empty() || firmware.len() > 2 * 1024 * 1024 {
        return Err("固件大小无效".into());
    }
    let firmware_len = firmware.len();
    let peripheral = {
        let held = state.peripheral.lock().await;
        match held.as_ref() {
            Some(item) if item.is_connected().await.unwrap_or(false) => item.clone(),
            _ => connect_peripheral(Some(&address), None).await?,
        }
    };
    let ota = characteristic(&peripheral, OTA_UUID)?;
    *state.ota_progress.lock().await = serde_json::json!({"state":"running","progress":0,"message":"正在写入固件"});
    let progress = state.ota_progress.clone();
    let held = state.peripheral.clone();
    let write_lock = state.write_lock.clone();
    tauri::async_runtime::spawn(async move {
        let _guard = write_lock.lock().await;
        let result: Result<(), String> = async {
            let size = firmware.len() as u32;
            let start = [1, size as u8, (size >> 8) as u8, (size >> 16) as u8, (size >> 24) as u8];
            peripheral.write(&ota, &start, WriteType::WithResponse).await.map_err(|error| error.to_string())?;
            let mut sent = 0usize;
            while sent < firmware.len() {
                let end = (sent + 240).min(firmware.len());
                let mut packet = Vec::with_capacity(end - sent + 1);
                packet.push(2);
                packet.extend_from_slice(&firmware[sent..end]);
                peripheral.write(&ota, &packet, WriteType::WithResponse).await.map_err(|error| error.to_string())?;
                sent = end;
                let percent = (sent * 100 / firmware.len()) as u64;
                *progress.lock().await = serde_json::json!({"state":"running","progress":percent,"message":"正在写入固件"});
            }
            peripheral.write(&ota, &[3], WriteType::WithResponse).await.map_err(|error| error.to_string())?;
            Ok(())
        }.await;
        match result {
            Ok(()) => *progress.lock().await = serde_json::json!({"state":"success","progress":100,"message":"固件校验完成，灯板正在重启"}),
            Err(error) => {
                let _ = peripheral.write(&ota, &[4], WriteType::WithResponse).await;
                *progress.lock().await = serde_json::json!({"state":"error","progress":0,"message":format!("固件升级失败：{error}")});
            }
        }
        *held.lock().await = Some(peripheral);
    });
    Ok(serde_json::json!({"ok":true,"bytes":firmware_len}))
}

#[tauri::command]
async fn ota_progress(state: tauri::State<'_, NativeBleState>) -> Result<Value, String> {
    Ok(state.ota_progress.lock().await.clone())
}

async fn native_apply(bridge: BridgeState, ble: NativeBleState, mut payload: Value) -> Result<Value, String> {
    if let Some(preview) = payload.get("preview").and_then(Value::as_bool) {
        *ble.preview_active.lock().await = preview;
    }
    payload["_native_transport"] = Value::Bool(true);
    let prepared = tauri::async_runtime::spawn_blocking(move || run_persistent_bridge(&bridge, "apply-device", &payload))
        .await.map_err(|error| format!("后台任务异常：{error}"))??;
    let device_id = prepared.get("device_id").and_then(Value::as_str).ok_or("没有已绑定灯板")?;
    let packets = prepared.get("packets").and_then(Value::as_array).ok_or("配置数据生成失败")?;
    let peripheral = {
        let held = ble.peripheral.lock().await;
        match held.as_ref() {
            Some(item) if item.is_connected().await.unwrap_or(false) => item.clone(),
            _ => connect_peripheral(None, Some(device_id)).await?,
        }
    };
    let control = characteristic(&peripheral, CONTROL_UUID)?;
    let _guard = ble.write_lock.lock().await;
    for encoded in packets {
        let bytes = BASE64.decode(encoded.as_str().ok_or("配置数据格式无效")?)
            .map_err(|_| "配置数据格式无效".to_string())?;
        peripheral.write(&control, &bytes, WriteType::WithResponse).await
            .map_err(|error| format!("配置下发失败：{error}"))?;
    }
    *ble.peripheral.lock().await = Some(peripheral);
    *ble.connected_device_id.lock().await = Some(device_id.to_string());
    start_heartbeat(ble.clone());
    Ok(prepared)
}

fn start_heartbeat(ble: NativeBleState) {
    if ble.heartbeat_running.swap(true, Ordering::SeqCst) { return; }
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(5)).await;
            let peripheral = ble.peripheral.lock().await.clone();
            let Some(peripheral) = peripheral else { break };
            if !peripheral.is_connected().await.unwrap_or(false) {
                *ble.peripheral.lock().await = None;
                *ble.last_dashboard.lock().await = None;
                break;
            }
            let Ok(control) = characteristic(&peripheral, CONTROL_UUID) else { break };
            let sequence = ble.sequence.fetch_add(1, Ordering::Relaxed).wrapping_add(1);
            let _guard = ble.write_lock.lock().await;
            if peripheral.write(&control, &[0xC3, 1, 1, sequence], WriteType::WithoutResponse).await.is_err() {
                *ble.peripheral.lock().await = None;
                *ble.last_dashboard.lock().await = None;
                break;
            }
        }
        ble.heartbeat_running.store(false, Ordering::SeqCst);
    });
}

#[tauri::command]
async fn bridge_action(
    bridge: tauri::State<'_, BridgeState>,
    ble: tauri::State<'_, NativeBleState>,
    action: String,
    payload: Value,
) -> Result<Value, String> {
    if action == "apply-device" {
        return native_apply(bridge.inner().clone(), ble.inner().clone(), payload).await;
    }
    let bridge_state = bridge.inner().clone();
    tauri::async_runtime::spawn_blocking(move || run_persistent_bridge(&bridge_state, &action, &payload))
        .await.map_err(|error| format!("后台任务异常：{error}"))?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BridgeState(Arc::new(Mutex::new(None))))
        .manage(NativeBleState::new())
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().expect("缺少应用图标").clone())
                .tooltip("Beacon · 信标")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![get_dashboard, scan_devices, identify_device, ota_start, ota_progress, bridge_action])
        .run(tauri::generate_context!())
        .expect("error while running Beacon");
}
