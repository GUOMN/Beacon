use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
#[cfg(not(target_os = "macos"))]
use btleplug::api::{
    Central, CentralState, Characteristic, Manager as _, Peripheral as _, ScanFilter, WriteType,
};
#[cfg(not(target_os = "macos"))]
use btleplug::platform::{
    Adapter as BleAdapter, Manager as BleManager, Peripheral as BlePeripheral,
};
use serde_json::Value;
use std::{
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU8, AtomicUsize, Ordering},
        Arc, Mutex,
    },
};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Manager, WindowEvent};
use tokio::sync::Mutex as AsyncMutex;
#[cfg(not(target_os = "macos"))]
use uuid::Uuid;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

fn bridge_script() -> Result<PathBuf, String> {
    [
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tauri_bridge.py"),
        std::env::current_exe()
            .map_err(|error| error.to_string())?
            .parent()
            .unwrap_or_else(|| std::path::Path::new("."))
            .join("resources/tauri_bridge.py"),
    ]
    .into_iter()
    .find(|path| path.is_file())
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
    let path = executable
        .parent()?
        .parent()?
        .join("Resources/binaries")
        .join(name);
    path.is_file().then_some(path)
}

#[cfg(windows)]
fn bundled_bridge() -> Option<PathBuf> {
    let development = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/bridge-x86_64.exe");
    let executable = std::env::current_exe().ok()?;
    let directory = executable.parent()?;
    [
        development,
        directory.join("binaries/bridge-x86_64.exe"),
        directory.join("resources/binaries/bridge-x86_64.exe"),
        directory.join("bridge-x86_64.exe"),
    ]
    .into_iter()
    .find(|path| path.is_file())
}

#[cfg(all(not(target_os = "macos"), not(windows)))]
fn bundled_bridge() -> Option<PathBuf> {
    None
}

#[cfg(target_os = "macos")]
fn macos_ble_library() -> Result<PathBuf, String> {
    let development =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries/libbeacon_macos_ble.dylib");
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let bundled = executable
        .parent()
        .and_then(|path| path.parent())
        .map(|path| path.join("Resources/binaries/libbeacon_macos_ble.dylib"));
    development
        .is_file()
        .then_some(development)
        .or_else(|| bundled.filter(|path| path.is_file()))
        .ok_or_else(|| "找不到 macOS 原生蓝牙模块，请重新安装客户端".to_string())
}

#[cfg(target_os = "macos")]
static MACOS_BLE_LIBRARY: std::sync::OnceLock<libloading::Library> = std::sync::OnceLock::new();

#[cfg(target_os = "macos")]
fn macos_ble_library_handle() -> Result<&'static libloading::Library, String> {
    if MACOS_BLE_LIBRARY.get().is_none() {
        let library = unsafe { libloading::Library::new(macos_ble_library()?) }
            .map_err(|error| format!("加载 macOS 原生蓝牙模块失败：{error}"))?;
        let _ = MACOS_BLE_LIBRARY.set(library);
    }
    MACOS_BLE_LIBRARY
        .get()
        .ok_or_else(|| "macOS 原生蓝牙模块初始化失败".to_string())
}

#[cfg(target_os = "macos")]
fn run_macos_ble_request(request: Value) -> Result<Value, String> {
    use std::ffi::{c_char, CStr, CString};
    type RequestFunction = unsafe extern "C" fn(*const c_char) -> *mut c_char;
    type FreeFunction = unsafe extern "C" fn(*mut c_char);

    let library = macos_ble_library_handle()?;
    let invoke: libloading::Symbol<RequestFunction> =
        unsafe { library.get(b"beacon_macos_request_json") }
            .map_err(|error| format!("原生蓝牙模块缺少请求入口：{error}"))?;
    let free: libloading::Symbol<FreeFunction> =
        unsafe { library.get(b"beacon_macos_free_string") }
            .map_err(|error| format!("原生蓝牙模块缺少内存释放入口：{error}"))?;
    let encoded = CString::new(request.to_string())
        .map_err(|_| "macOS 原生蓝牙请求包含无效字符".to_string())?;
    let pointer = unsafe { invoke(encoded.as_ptr()) };
    if pointer.is_null() {
        return Err("macOS 原生蓝牙没有返回数据".into());
    }
    let json = unsafe { CStr::from_ptr(pointer) }
        .to_string_lossy()
        .into_owned();
    unsafe { free(pointer) };
    let value: Value = serde_json::from_str(&json)
        .map_err(|error| format!("macOS 原生蓝牙返回无效数据：{error}"))?;
    if let Some(error) = value.get("error").and_then(Value::as_str) {
        return Err(error.to_string());
    }
    Ok(value)
}

#[cfg(target_os = "macos")]
async fn run_macos_ble_request_async(request: Value) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || run_macos_ble_request(request))
        .await
        .map_err(|error| format!("macOS 原生蓝牙任务异常：{error}"))?
}

struct BridgeProcess {
    _child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

#[derive(Clone)]
struct BridgeState(Arc<Mutex<Option<BridgeProcess>>>);

#[derive(Clone)]
struct NativeBleState {
    #[cfg(not(target_os = "macos"))]
    adapter: Arc<AsyncMutex<Option<BleAdapter>>>,
    #[cfg(not(target_os = "macos"))]
    peripheral: Arc<AsyncMutex<Option<BlePeripheral>>>,
    write_lock: Arc<AsyncMutex<()>>,
    ota_progress: Arc<AsyncMutex<Value>>,
    last_dashboard: Arc<AsyncMutex<Option<String>>>,
    preview_active: Arc<AsyncMutex<bool>>,
    heartbeat_running: Arc<AtomicBool>,
    sequence: Arc<AtomicU8>,
    connected_device_id: Arc<AsyncMutex<Option<String>>>,
    manual_disconnect: Arc<AtomicBool>,
    foreground_operations: Arc<AtomicUsize>,
}

impl NativeBleState {
    fn new() -> Self {
        Self {
            #[cfg(not(target_os = "macos"))]
            adapter: Arc::new(AsyncMutex::new(None)),
            #[cfg(not(target_os = "macos"))]
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
            manual_disconnect: Arc::new(AtomicBool::new(false)),
            foreground_operations: Arc::new(AtomicUsize::new(0)),
        }
    }
}

struct ForegroundGuard(Arc<AtomicUsize>);

impl ForegroundGuard {
    fn new(state: &NativeBleState) -> Self {
        state.foreground_operations.fetch_add(1, Ordering::SeqCst);
        Self(state.foreground_operations.clone())
    }
}

impl Drop for ForegroundGuard {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

#[cfg(not(target_os = "macos"))]
const DEVICE_PREFIX: &str = "Codex-Light-";
#[cfg(not(target_os = "macos"))]
const SERVICE_UUID: &str = "0100c310-7625-819e-934c-32b8e4177d6a";
#[cfg(not(target_os = "macos"))]
const CONTROL_UUID: &str = "0200c310-7625-819e-934c-32b8e4177d6a";
#[cfg(not(target_os = "macos"))]
const OTA_UUID: &str = "0300c310-7625-819e-934c-32b8e4177d6a";
#[cfg(not(target_os = "macos"))]
const GAP_DEVICE_NAME_UUID: &str = "00002a00-0000-1000-8000-00805f9b34fb";

#[cfg(not(target_os = "macos"))]
fn status_scan_filter() -> Result<ScanFilter, String> {
    Ok(ScanFilter {
        services: vec![Uuid::parse_str(SERVICE_UUID).map_err(|error| error.to_string())?],
    })
}

#[cfg(not(target_os = "macos"))]
fn device_id_from_name(name: &str) -> Option<String> {
    let start = name.find(DEVICE_PREFIX)? + DEVICE_PREFIX.len();
    let device_id: String = name[start..].chars().take(6).collect();
    (device_id.len() == 6 && device_id.chars().all(|value| value.is_ascii_hexdigit()))
        .then(|| device_id.to_ascii_uppercase())
}

#[cfg(not(target_os = "macos"))]
fn characteristic(peripheral: &BlePeripheral, uuid: &str) -> Result<Characteristic, String> {
    let uuid = Uuid::parse_str(uuid).map_err(|error| error.to_string())?;
    peripheral
        .characteristics()
        .into_iter()
        .find(|item| item.uuid == uuid)
        .ok_or_else(|| "灯板固件缺少所需蓝牙服务".to_string())
}

#[cfg(not(target_os = "macos"))]
async fn wait_for_macos_adapter(
    adapter: &BleAdapter,
    deadline: tokio::time::Instant,
) -> Result<(), String> {
    if !cfg!(target_os = "macos") {
        return Ok(());
    }
    loop {
        let state = adapter
            .adapter_state()
            .await
            .map_err(|error| format!("读取蓝牙状态失败：{error}"))?;
        if state == CentralState::PoweredOn {
            return Ok(());
        }
        if tokio::time::Instant::now() >= deadline {
            return Err(format!("Mac 蓝牙尚未就绪，当前状态：{state:?}"));
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
}

#[cfg(not(target_os = "macos"))]
async fn native_adapter(state: &NativeBleState) -> Result<BleAdapter, String> {
    let mut held = state.adapter.lock().await;
    if let Some(adapter) = held.as_ref() {
        return Ok(adapter.clone());
    }
    let manager = BleManager::new()
        .await
        .map_err(|error| format!("蓝牙初始化失败：{error}"))?;
    let adapter = manager
        .adapters()
        .await
        .map_err(|error| format!("读取蓝牙适配器失败：{error}"))?
        .into_iter()
        .next()
        .ok_or("未找到可用的蓝牙适配器")?;
    wait_for_macos_adapter(
        &adapter,
        tokio::time::Instant::now() + std::time::Duration::from_secs(30),
    )
    .await?;
    *held = Some(adapter.clone());
    Ok(adapter)
}

#[cfg(not(target_os = "macos"))]
async fn connect_peripheral(
    state: &NativeBleState,
    address: Option<&str>,
    device_id: Option<&str>,
) -> Result<BlePeripheral, String> {
    let adapters = vec![native_adapter(state).await?];
    let ready_deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(30);
    for adapter in &adapters {
        wait_for_macos_adapter(adapter, ready_deadline).await?;
        adapter
            .start_scan(status_scan_filter()?)
            .await
            .map_err(|error| format!("启动蓝牙扫描失败：{error}"))?;
    }
    let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(10);
    let fallback_at = tokio::time::Instant::now() + std::time::Duration::from_secs(4);
    let mut fallback_started = false;
    loop {
        for adapter in &adapters {
            for peripheral in adapter
                .peripherals()
                .await
                .map_err(|error| error.to_string())?
            {
                let id_matches = address.is_some_and(|value| peripheral.id().to_string() == value);
                let properties = peripheral
                    .properties()
                    .await
                    .map_err(|error| error.to_string())?;
                let mut name_matches = device_id.is_some_and(|expected| {
                    properties
                        .as_ref()
                        .and_then(|props| props.local_name.as_deref())
                        .and_then(device_id_from_name)
                        .is_some_and(|value| value.eq_ignore_ascii_case(expected))
                });
                // CoreBluetooth can report the advertised service before it
                // reports the scan-response local name. Connect only to this
                // service-filtered candidate and read the standard GAP name.
                if !id_matches && !name_matches && device_id.is_some() {
                    if !peripheral.is_connected().await.unwrap_or(false)
                        && peripheral.connect().await.is_err()
                    {
                        continue;
                    }
                    if peripheral.discover_services().await.is_ok() {
                        if let Ok(name_uuid) = Uuid::parse_str(GAP_DEVICE_NAME_UUID) {
                            if let Some(name_characteristic) = peripheral
                                .characteristics()
                                .into_iter()
                                .find(|item| item.uuid == name_uuid)
                            {
                                if let Ok(bytes) = peripheral.read(&name_characteristic).await {
                                    name_matches = std::str::from_utf8(&bytes)
                                        .ok()
                                        .and_then(device_id_from_name)
                                        .is_some_and(|value| {
                                            value.eq_ignore_ascii_case(device_id.unwrap())
                                        });
                                }
                            }
                        }
                    }
                    if !name_matches {
                        let _ = peripheral.disconnect().await;
                    }
                }
                if id_matches || name_matches {
                    for item in &adapters {
                        let _ = item.stop_scan().await;
                    }
                    if !peripheral.is_connected().await.unwrap_or(false) {
                        peripheral
                            .connect()
                            .await
                            .map_err(|error| format!("连接灯板失败：{error}"))?;
                    }
                    peripheral
                        .discover_services()
                        .await
                        .map_err(|error| format!("读取灯板服务失败：{error}"))?;
                    return Ok(peripheral);
                }
            }
        }
        if tokio::time::Instant::now() >= deadline {
            break;
        }
        if cfg!(target_os = "macos")
            && !fallback_started
            && tokio::time::Instant::now() >= fallback_at
        {
            for adapter in &adapters {
                let _ = adapter.stop_scan().await;
                let _ = adapter.start_scan(ScanFilter::default()).await;
            }
            fallback_started = true;
        }
        tokio::time::sleep(std::time::Duration::from_millis(250)).await;
    }
    for adapter in &adapters {
        let _ = adapter.stop_scan().await;
    }
    Err("没有发现目标灯板".into())
}

#[cfg(not(target_os = "macos"))]
async fn discovered_status_boards(
    adapters: &[BleAdapter],
) -> Result<(Vec<Value>, usize, usize, usize), String> {
    let mut devices: Vec<Value> = Vec::new();
    let mut observed = 0usize;
    let mut candidates = 0usize;
    let mut unresolved = 0usize;
    let service_uuid = Uuid::parse_str(SERVICE_UUID).map_err(|error| error.to_string())?;
    let name_uuid = Uuid::parse_str(GAP_DEVICE_NAME_UUID).map_err(|error| error.to_string())?;
    for adapter in adapters {
        let peripherals = adapter
            .peripherals()
            .await
            .map_err(|error| format!("读取蓝牙设备失败：{error}"))?;
        for peripheral in peripherals {
            observed += 1;
            let Some(properties) = peripheral
                .properties()
                .await
                .map_err(|error| format!("读取设备属性失败：{error}"))?
            else {
                continue;
            };
            let advertised_name = properties.local_name.clone();
            let service_matches = properties
                .services
                .iter()
                .any(|value| *value == service_uuid);
            let mut device_id = advertised_name.as_deref().and_then(device_id_from_name);
            if service_matches || device_id.is_some() {
                candidates += 1;
            }
            let mut resolved_name = advertised_name;
            if device_id.is_none() && service_matches {
                let was_connected = peripheral.is_connected().await.unwrap_or(false);
                if (was_connected || peripheral.connect().await.is_ok())
                    && peripheral.discover_services().await.is_ok()
                {
                    if let Some(name_characteristic) = peripheral
                        .characteristics()
                        .into_iter()
                        .find(|item| item.uuid == name_uuid)
                    {
                        if let Ok(bytes) = peripheral.read(&name_characteristic).await {
                            if let Ok(name) = String::from_utf8(bytes) {
                                device_id = device_id_from_name(&name);
                                resolved_name = Some(name);
                            }
                        }
                    }
                }
                if !was_connected {
                    let _ = peripheral.disconnect().await;
                }
            }
            let Some(device_id) = device_id else {
                if service_matches {
                    unresolved += 1;
                }
                continue;
            };
            if devices
                .iter()
                .any(|item| item.get("device_id").and_then(Value::as_str) == Some(&device_id))
            {
                continue;
            }
            devices.push(serde_json::json!({
                "name": resolved_name.unwrap_or_else(|| format!("{DEVICE_PREFIX}{device_id}")),
                "device_id": device_id,
                "address": peripheral.id().to_string(),
                "rssi": properties.rssi,
                "connected": peripheral.is_connected().await.unwrap_or(false),
            }));
        }
    }
    devices.sort_by(|left, right| {
        right
            .get("rssi")
            .and_then(Value::as_i64)
            .cmp(&left.get("rssi").and_then(Value::as_i64))
    });
    Ok((devices, observed, candidates, unresolved))
}

fn start_bridge_process() -> Result<BridgeProcess, String> {
    if let Some(binary) = bundled_bridge() {
        let working_dir = binary.parent().ok_or("内置后台路径无效")?.to_path_buf();
        let mut command = Command::new(&binary);
        command
            .arg("serve")
            .current_dir(&working_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let mut child = command
            .spawn()
            .map_err(|error| format!("内置后台启动失败：{error}"))?;
        let stdin = child.stdin.take().ok_or("内置后台输入管道创建失败")?;
        let stdout = child.stdout.take().ok_or("内置后台输出管道创建失败")?;
        return Ok(BridgeProcess {
            _child: child,
            stdin,
            stdout: BufReader::new(stdout),
        });
    }
    let script = bridge_script()?;
    let working_dir = script.parent().ok_or("后台脚本路径无效")?;
    let interpreters: &[(&str, &[&str])] = if cfg!(windows) {
        &[("pythonw", &[]), ("py", &["-3"]), ("python", &[])]
    } else {
        &[("python3", &[]), ("python", &[])]
    };
    for (program, prefix) in interpreters {
        let mut command = Command::new(program);
        command
            .args(*prefix)
            .arg(&script)
            .arg("serve")
            .current_dir(working_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        if let Ok(mut child) = command.spawn() {
            let Some(stdin) = child.stdin.take() else {
                continue;
            };
            let Some(stdout) = child.stdout.take() else {
                continue;
            };
            return Ok(BridgeProcess {
                _child: child,
                stdin,
                stdout: BufReader::new(stdout),
            });
        }
    }
    Err("未找到 Python 3，请先安装桌面端依赖".into())
}

fn run_persistent_bridge(
    state: &BridgeState,
    command: &str,
    payload: &Value,
) -> Result<Value, String> {
    let mut guard = state.0.lock().map_err(|_| "后台服务锁定失败".to_string())?;
    if guard.is_none() {
        *guard = Some(start_bridge_process()?);
    }
    let process = guard.as_mut().expect("后台进程应已启动");
    let request = serde_json::json!({"command": command, "payload": payload});
    if writeln!(process.stdin, "{}", request).is_err() || process.stdin.flush().is_err() {
        *guard = None;
        return Err("后台服务已退出，请重试".into());
    }
    let mut line = String::new();
    if process
        .stdout
        .read_line(&mut line)
        .map_err(|error| error.to_string())?
        == 0
    {
        *guard = None;
        return Err("后台服务没有返回数据".into());
    }
    let envelope: Value =
        serde_json::from_str(&line).map_err(|error| format!("后台返回了无效数据：{error}"))?;
    if let Some(error) = envelope.get("error").and_then(Value::as_str) {
        return Err(error.to_string());
    }
    Ok(envelope.get("ok").cloned().unwrap_or(Value::Null))
}

async fn run_bridge_async(
    state: BridgeState,
    command: &'static str,
    payload: Value,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || run_persistent_bridge(&state, command, &payload))
        .await
        .map_err(|error| format!("后台任务异常：{error}"))?
}

#[tauri::command]
async fn get_dashboard(
    bridge: tauri::State<'_, BridgeState>,
    ble: tauri::State<'_, NativeBleState>,
) -> Result<Value, String> {
    let result =
        run_bridge_async(bridge.inner().clone(), "dashboard", serde_json::json!({})).await?;
    let signature = serde_json::to_string(&result).map_err(|error| error.to_string())?;
    let should_push = ble.foreground_operations.load(Ordering::SeqCst) == 0
        && !ble.manual_disconnect.load(Ordering::SeqCst)
        && !*ble.preview_active.lock().await
        && {
            let mut previous = ble.last_dashboard.lock().await;
            if previous.as_ref() == Some(&signature) {
                false
            } else {
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
            if native_apply(
                bridge_state,
                ble_state.clone(),
                serde_json::json!({}),
                false,
            )
            .await
            .is_err()
            {
                *ble_state.last_dashboard.lock().await = None;
            }
        });
    }
    Ok(result)
}

#[tauri::command]
async fn scan_devices(ble: tauri::State<'_, NativeBleState>) -> Result<Value, String> {
    let _foreground = ForegroundGuard::new(ble.inner());
    let _connection = ble.write_lock.lock().await;
    #[cfg(target_os = "macos")]
    {
        return run_macos_ble_request_async(serde_json::json!({
            "command": "scan",
            "seconds": 15.0,
        }))
        .await;
    }

    #[cfg(not(target_os = "macos"))]
    {
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
        let adapters = vec![native_adapter(ble.inner()).await?];
        let started_at = tokio::time::Instant::now();
        let deadline = started_at + std::time::Duration::from_secs(4);
        let mut restart_at = tokio::time::Instant::now();
        let mut last_start_error: Option<String> = None;
        let devices = loop {
            if tokio::time::Instant::now() >= restart_at {
                for adapter in &adapters {
                    if let Err(error) = adapter.start_scan(status_scan_filter()?).await {
                        last_start_error = Some(format!("启动蓝牙扫描失败：{error}"));
                    }
                }
                restart_at = deadline + std::time::Duration::from_secs(1);
            }
            tokio::time::sleep(std::time::Duration::from_millis(500)).await;
            let found = discovered_status_boards(&adapters).await?;
            if !found.0.is_empty() || tokio::time::Instant::now() >= deadline {
                break found;
            }
        };
        for adapter in &adapters {
            let _ = adapter.stop_scan().await;
        }
        let (devices, observed_count, candidate_count, unresolved_count) = devices;
        if devices.is_empty() {
            if let Some(error) = last_start_error {
                return Err(error);
            }
        }
        Ok(serde_json::json!({
            "devices": devices,
            "observed_count": observed_count,
            "candidate_count": candidate_count,
            "unresolved_count": unresolved_count,
        }))
    }
}

#[tauri::command]
async fn identify_device(
    state: tauri::State<'_, NativeBleState>,
    address: String,
) -> Result<Value, String> {
    let _foreground = ForegroundGuard::new(state.inner());
    let _connection = state.write_lock.lock().await;
    #[cfg(target_os = "macos")]
    {
        return run_macos_ble_request_async(serde_json::json!({
            "command": "identify",
            "address": address,
        }))
        .await;
    }
    #[cfg(not(target_os = "macos"))]
    {
        let (peripheral, reused_connection) = {
            let held = state.peripheral.lock().await;
            match held.as_ref() {
                Some(item) if item.is_connected().await.unwrap_or(false) => (item.clone(), true),
                _ => (
                    connect_peripheral(state.inner(), Some(&address), None).await?,
                    false,
                ),
            }
        };
        let control = characteristic(&peripheral, CONTROL_UUID)?;
        peripheral
            .write(&control, &[0xC3, 1, 4, 0], WriteType::WithResponse)
            .await
            .map_err(|error| format!("发送识别动画失败：{error}"))?;
        if reused_connection {
            *state.peripheral.lock().await = Some(peripheral);
            start_heartbeat(state.inner().clone());
        } else {
            peripheral
                .disconnect()
                .await
                .map_err(|error| format!("识别完成但断开失败：{error}"))?;
        }
        Ok(serde_json::json!({"ok": true}))
    }
}

#[tauri::command]
async fn disconnect_device(state: tauri::State<'_, NativeBleState>) -> Result<Value, String> {
    let _foreground = ForegroundGuard::new(state.inner());
    state.manual_disconnect.store(true, Ordering::SeqCst);
    let _guard = state.write_lock.lock().await;
    #[cfg(target_os = "macos")]
    {
        run_macos_ble_request_async(serde_json::json!({"command": "disconnect"})).await?;
        *state.connected_device_id.lock().await = None;
        *state.last_dashboard.lock().await = None;
        return Ok(serde_json::json!({"ok":true}));
    }
    #[cfg(not(target_os = "macos"))]
    {
        let peripheral = state.peripheral.lock().await.take();
        if let Some(peripheral) = peripheral {
            if peripheral.is_connected().await.unwrap_or(false) {
                peripheral
                    .disconnect()
                    .await
                    .map_err(|error| format!("断开灯板失败：{error}"))?;
            }
        }
        *state.connected_device_id.lock().await = None;
        *state.last_dashboard.lock().await = None;
        Ok(serde_json::json!({"ok":true}))
    }
}

#[tauri::command]
async fn connection_status(state: tauri::State<'_, NativeBleState>) -> Result<Value, String> {
    #[cfg(target_os = "macos")]
    {
        let mut status =
            run_macos_ble_request_async(serde_json::json!({"command": "status"})).await?;
        if let Some(object) = status.as_object_mut() {
            object.insert(
                "manually_disconnected".into(),
                Value::Bool(state.manual_disconnect.load(Ordering::SeqCst)),
            );
        }
        return Ok(status);
    }
    #[cfg(not(target_os = "macos"))]
    {
        let peripheral = state.peripheral.lock().await.clone();
        let connected = match peripheral {
            Some(item) => item.is_connected().await.unwrap_or(false),
            None => false,
        };
        Ok(serde_json::json!({
            "connected": connected,
            "device_id": state.connected_device_id.lock().await.clone(),
            "manually_disconnected": state.manual_disconnect.load(Ordering::SeqCst),
        }))
    }
}

#[tauri::command]
async fn ota_start(
    state: tauri::State<'_, NativeBleState>,
    address: String,
    firmware_base64: String,
) -> Result<Value, String> {
    let foreground = ForegroundGuard::new(state.inner());
    let connection = state.write_lock.clone().lock_owned().await;
    let firmware = BASE64
        .decode(firmware_base64.as_bytes())
        .map_err(|_| "固件文件格式无效".to_string())?;
    if firmware.is_empty() || firmware.len() > 2 * 1024 * 1024 {
        return Err("固件大小无效".into());
    }
    let firmware_len = firmware.len();
    *state.ota_progress.lock().await =
        serde_json::json!({"state":"running","progress":0,"message":"正在写入固件"});
    #[cfg(target_os = "macos")]
    {
        let progress = state.ota_progress.clone();
        tauri::async_runtime::spawn(async move {
            let _foreground = foreground;
            let _connection = connection;
            let result = run_macos_ble_request_async(serde_json::json!({
                "command": "ota",
                "address": address,
                "firmware": firmware_base64,
            }))
            .await;
            match result {
                Ok(_) => {
                    *progress.lock().await = serde_json::json!({"state":"success","progress":100,"message":"固件校验完成，灯板正在重启"})
                }
                Err(error) => {
                    *progress.lock().await = serde_json::json!({"state":"error","progress":0,"message":format!("固件升级失败：{error}")})
                }
            }
        });
        return Ok(serde_json::json!({"ok":true,"bytes":firmware_len}));
    }
    #[cfg(not(target_os = "macos"))]
    {
        let peripheral = {
            let held = state.peripheral.lock().await;
            match held.as_ref() {
                Some(item) if item.is_connected().await.unwrap_or(false) => item.clone(),
                _ => connect_peripheral(state.inner(), Some(&address), None).await?,
            }
        };
        let ota = characteristic(&peripheral, OTA_UUID)?;
        let progress = state.ota_progress.clone();
        let held = state.peripheral.clone();
        tauri::async_runtime::spawn(async move {
            let _foreground = foreground;
            let _connection = connection;
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
                Ok(()) => {
                    *progress.lock().await = serde_json::json!({"state":"success","progress":100,"message":"固件校验完成，灯板正在重启"})
                }
                Err(error) => {
                    let _ = peripheral.write(&ota, &[4], WriteType::WithResponse).await;
                    *progress.lock().await = serde_json::json!({"state":"error","progress":0,"message":format!("固件升级失败：{error}")});
                }
            }
            *held.lock().await = Some(peripheral);
        });
        Ok(serde_json::json!({"ok":true,"bytes":firmware_len}))
    }
}

#[tauri::command]
async fn ota_progress(state: tauri::State<'_, NativeBleState>) -> Result<Value, String> {
    Ok(state.ota_progress.lock().await.clone())
}

async fn native_apply(
    bridge: BridgeState,
    ble: NativeBleState,
    mut payload: Value,
    explicit: bool,
) -> Result<Value, String> {
    let _foreground = explicit.then(|| ForegroundGuard::new(&ble));
    if explicit {
        ble.manual_disconnect.store(false, Ordering::SeqCst);
    } else if ble.manual_disconnect.load(Ordering::SeqCst)
        || ble.foreground_operations.load(Ordering::SeqCst) > 0
    {
        return Err("设备已被主动断开".into());
    }
    if let Some(preview) = payload.get("preview").and_then(Value::as_bool) {
        *ble.preview_active.lock().await = preview;
    }
    // A manual save owns the connection for the whole prepare/write transaction.
    // A background refresh prepares without the lock, then yields if any foreground
    // operation arrived while it was working or waiting for the connection.
    let explicit_connection = if explicit {
        Some(ble.write_lock.clone().lock_owned().await)
    } else {
        None
    };
    payload["_native_transport"] = Value::Bool(true);
    let prepared = tauri::async_runtime::spawn_blocking(move || {
        run_persistent_bridge(&bridge, "apply-device", &payload)
    })
    .await
    .map_err(|error| format!("后台任务异常：{error}"))??;
    let device_id = prepared
        .get("device_id")
        .and_then(Value::as_str)
        .ok_or("没有已绑定灯板")?
        .to_string();
    let packets = prepared
        .get("packets")
        .and_then(Value::as_array)
        .ok_or("配置数据生成失败")?
        .clone();
    if !explicit
        && (ble.manual_disconnect.load(Ordering::SeqCst)
            || ble.foreground_operations.load(Ordering::SeqCst) > 0)
    {
        return Err("设备已被主动断开".into());
    }
    let background_connection = if explicit {
        None
    } else {
        Some(ble.write_lock.clone().lock_owned().await)
    };
    if !explicit
        && (ble.manual_disconnect.load(Ordering::SeqCst)
            || ble.foreground_operations.load(Ordering::SeqCst) > 0)
    {
        return Err("前台正在使用蓝牙连接".into());
    }
    #[cfg(target_os = "macos")]
    {
        let request = serde_json::json!({
            "command": "apply",
            "device_id": device_id,
            "packets": packets,
        });
        let _connection = (explicit_connection, background_connection);
        run_macos_ble_request_async(request).await?;
        let applied_device_id = prepared
            .get("device_id")
            .and_then(Value::as_str)
            .ok_or("没有已绑定灯板")?
            .to_string();
        *ble.connected_device_id.lock().await = Some(applied_device_id);
        start_heartbeat(ble.clone());
        return Ok(prepared);
    }
    #[cfg(not(target_os = "macos"))]
    {
        let peripheral = {
            let held = ble.peripheral.lock().await;
            match held.as_ref() {
                Some(item) if item.is_connected().await.unwrap_or(false) => item.clone(),
                _ => connect_peripheral(&ble, None, Some(&device_id)).await?,
            }
        };
        let control = characteristic(&peripheral, CONTROL_UUID)?;
        let _connection = (explicit_connection, background_connection);
        let decoded = packets
            .iter()
            .map(|encoded| {
                let value = encoded.as_str().ok_or("配置数据格式无效")?;
                BASE64
                    .decode(value)
                    .map_err(|_| "配置数据格式无效".to_string())
            })
            .collect::<Result<Vec<_>, _>>()?;
        for bytes in &decoded {
            peripheral
                .write(&control, bytes, WriteType::WithResponse)
                .await
                .map_err(|error| format!("配置下发失败：{error}"))?;
        }
        *ble.peripheral.lock().await = Some(peripheral);
        *ble.connected_device_id.lock().await = Some(device_id);
        start_heartbeat(ble.clone());
        Ok(prepared)
    }
}

fn start_heartbeat(ble: NativeBleState) {
    if ble.heartbeat_running.swap(true, Ordering::SeqCst) {
        return;
    }
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(5)).await;
            if ble.foreground_operations.load(Ordering::SeqCst) > 0 {
                continue;
            }
            #[cfg(target_os = "macos")]
            {
                let sequence = ble.sequence.fetch_add(1, Ordering::Relaxed).wrapping_add(1);
                let _guard = ble.write_lock.lock().await;
                if ble.foreground_operations.load(Ordering::SeqCst) > 0 {
                    continue;
                }
                if run_macos_ble_request_async(serde_json::json!({
                    "command": "heartbeat",
                    "sequence": sequence,
                }))
                .await
                .is_err()
                {
                    *ble.connected_device_id.lock().await = None;
                    *ble.last_dashboard.lock().await = None;
                    break;
                }
                continue;
            }
            #[cfg(not(target_os = "macos"))]
            {
                let peripheral = ble.peripheral.lock().await.clone();
                let Some(peripheral) = peripheral else { break };
                if !peripheral.is_connected().await.unwrap_or(false) {
                    *ble.peripheral.lock().await = None;
                    *ble.last_dashboard.lock().await = None;
                    break;
                }
                let Ok(control) = characteristic(&peripheral, CONTROL_UUID) else {
                    break;
                };
                let sequence = ble.sequence.fetch_add(1, Ordering::Relaxed).wrapping_add(1);
                let _guard = ble.write_lock.lock().await;
                if ble.foreground_operations.load(Ordering::SeqCst) > 0 {
                    continue;
                }
                if peripheral
                    .write(
                        &control,
                        &[0xC3, 1, 1, sequence],
                        WriteType::WithoutResponse,
                    )
                    .await
                    .is_err()
                {
                    *ble.peripheral.lock().await = None;
                    *ble.last_dashboard.lock().await = None;
                    break;
                }
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
        return native_apply(bridge.inner().clone(), ble.inner().clone(), payload, true).await;
    }
    let bridge_state = bridge.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        run_persistent_bridge(&bridge_state, &action, &payload)
    })
    .await
    .map_err(|error| format!("后台任务异常：{error}"))?
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
                    } = event
                    {
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
        .invoke_handler(tauri::generate_handler![
            get_dashboard,
            scan_devices,
            identify_device,
            disconnect_device,
            connection_status,
            ota_start,
            ota_progress,
            bridge_action
        ])
        .run(tauri::generate_context!())
        .expect("error while running Beacon");
}
