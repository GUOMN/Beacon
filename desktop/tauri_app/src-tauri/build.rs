use std::{env, path::PathBuf};

fn main() {
    let os = env::var("CARGO_CFG_TARGET_OS").unwrap();
    if matches!(os.as_str(), "macos" | "windows") {
        build_ping_helper(&os);
    }
    tauri_build::build();
}

fn build_ping_helper(os: &str) {
    let source = PathBuf::from("native/ping-hook");
    println!("cargo:rerun-if-changed={}", source.display());
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let compiler = cc::Build::new().cpp(true).get_compiler();
    let mut command = compiler.to_command();
    command.current_dir(&out);
    let root = env::current_dir().unwrap().join(source);
    let mut files = vec!["commands.cpp", "decision.cpp", "json.cpp", "output.cpp", "simulate.cpp"];
    if os == "windows" {
        files.extend(["main_win.cpp", "hooks_win.cpp"]);
        command.args(["/std:c++20", "/EHsc", "/O2", "/MT", "/DUNICODE", "/D_UNICODE", "/Fe:beacon-ping-hook.exe"]);
    } else {
        files.extend(["main_posix.cpp", "hooks_mac.cpp", "macinput.cpp"]);
        command.args(["-std=c++20", "-O2", "-o", "beacon-ping-hook"]);
    }
    command.args(files.iter().map(|name| root.join(name)));
    if os == "windows" {
        command.args(["/link", "user32.lib"]);
    } else {
        command.args(["-framework", "ApplicationServices", "-framework", "CoreFoundation"]);
    }
    assert!(command.status().expect("start ping helper compiler").success(), "compile ping helper");
}
