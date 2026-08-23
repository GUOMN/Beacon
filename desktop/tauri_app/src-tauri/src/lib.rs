use serde_json::Value;
use std::{io::{BufRead, BufReader, Write}, path::PathBuf, process::{Child, ChildStdin, ChildStdout, Command, Stdio}, sync::{Arc, Mutex}};
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

fn run_bridge(command: &str, payload: &Value) -> Result<Value, String> {
    if let Some(binary) = bundled_bridge() {
        let working_dir = binary.parent().ok_or("内置后台路径无效")?.to_path_buf();
        let mut process = Command::new(&binary);
        process.arg(command).current_dir(&working_dir)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = process.spawn().map_err(|error| format!("内置后台启动失败：{error}"))?;
        if let Some(stdin) = child.stdin.as_mut() { stdin.write_all(payload.to_string().as_bytes()).map_err(|e| e.to_string())?; }
        let output = child.wait_with_output().map_err(|e| e.to_string())?;
        if output.status.success() {
            return serde_json::from_slice(&output.stdout).map_err(|error| format!("后台返回了无效数据：{error}"));
        }
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    let script = bridge_script()?;
    let working_dir = script.parent().ok_or("后台脚本路径无效")?;
    let interpreters: &[(&str, &[&str])] = if cfg!(windows) {
        &[("pythonw", &[]), ("py", &["-3"]), ("python", &[])]
    } else { &[("python3", &[]), ("python", &[])] };
    for (program, prefix) in interpreters {
        let mut process = Command::new(program);
        process.args(*prefix).arg(&script).arg(command)
            .current_dir(working_dir).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        #[cfg(windows)]
        process.creation_flags(CREATE_NO_WINDOW);
        let child = process.spawn();
        let Ok(mut child) = child else { continue };
        if let Some(stdin) = child.stdin.as_mut() { stdin.write_all(payload.to_string().as_bytes()).map_err(|e| e.to_string())?; }
        let output = child.wait_with_output().map_err(|e| e.to_string())?;
        if output.status.success() {
            return serde_json::from_slice(&output.stdout)
                .map_err(|error| format!("后台返回了无效数据：{error}"));
        }
        let message = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if message.is_empty() { "Python 后台执行失败".into() } else { message });
    }
    Err("未找到 Python 3，请先安装桌面端依赖".into())
}

struct BridgeProcess {
    _child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

#[derive(Clone)]
struct BridgeState(Arc<Mutex<Option<BridgeProcess>>>);

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
async fn get_dashboard(state: tauri::State<'_, BridgeState>) -> Result<Value, String> {
    run_bridge_async(state.inner().clone(), "dashboard", serde_json::json!({})).await
}

#[tauri::command]
async fn scan_devices(state: tauri::State<'_, BridgeState>) -> Result<Value, String> {
    run_bridge_async(state.inner().clone(), "scan-devices", serde_json::json!({})).await
}

#[tauri::command]
async fn bridge_action(state: tauri::State<'_, BridgeState>, action: String, payload: Value) -> Result<Value, String> {
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || run_persistent_bridge(&state, &action, &payload))
        .await
        .map_err(|error| format!("后台任务异常：{error}"))?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BridgeState(Arc::new(Mutex::new(None))))
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().expect("缺少应用图标").clone())
                .tooltip("Codex Status Bridge")
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
        .invoke_handler(tauri::generate_handler![get_dashboard, scan_devices, bridge_action])
        .run(tauri::generate_context!())
        .expect("error while running Codex Status Bridge");
}
