# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_dir = Path(SPECPATH)

analysis = Analysis(
    [str(project_dir / "macos_app" / "main.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[],
    hiddenimports=["bleak.backends.corebluetooth"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["winrt"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="CodexStatusBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch="arm64",
)
app = BUNDLE(
    exe,
    name="Codex Status Bridge.app",
    bundle_identifier="com.guomn.codex-status-bridge",
    info_plist={
        "CFBundleDisplayName": "Codex 状态灯",
        "CFBundleName": "Codex 状态灯",
        "CFBundleShortVersionString": "0.1.0-test",
        "NSBluetoothAlwaysUsageDescription": "用于连接并控制已绑定的 ESP32 状态灯板。",
        "NSBluetoothPeripheralUsageDescription": "用于连接并控制已绑定的 ESP32 状态灯板。",
        "LSMinimumSystemVersion": "12.0",
    },
)
