use std::{env, path::PathBuf};

fn main() {
    let os = env::var("CARGO_CFG_TARGET_OS").unwrap();
    if os == "macos" {
        let source = PathBuf::from("native/ping-hook");
        println!("cargo:rerun-if-changed={}", source.display());
        cc::Build::new()
            .cpp(true)
            .std("c++20")
            .files(["hooks_mac.cpp", "macinput.cpp", "commands.cpp", "decision.cpp", "json.cpp", "output.cpp"].map(|name| source.join(name)))
            .compile("beacon_ping");
        println!("cargo:rustc-link-lib=framework=ApplicationServices");
        println!("cargo:rustc-link-lib=framework=CoreFoundation");
    } else if os == "windows" {
        build_ping_helper();
    }
    tauri_build::build();
}

fn build_ping_helper() {
    let source = PathBuf::from("native/ping-hook");
    println!("cargo:rerun-if-changed={}", source.display());
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let compiler = cc::Build::new().cpp(true).get_compiler();
    let mut command = compiler.to_command();
    command.current_dir(&out);
    let root = env::current_dir().unwrap().join(source);
    let files = ["commands.cpp", "decision.cpp", "json.cpp", "output.cpp", "simulate.cpp", "main_win.cpp", "hooks_win.cpp"];
    command.args(["/std:c++20", "/EHsc", "/O2", "/MT", "/DUNICODE", "/D_UNICODE", "/Fe:beacon-ping-hook.exe"]);
    command.args(files.iter().map(|name| root.join(name)));
    command.args(["/link", "user32.lib"]);
    assert!(command.status().expect("start ping helper compiler").success(), "compile ping helper");
}
