# Beacon desktop client

Beacon uses a Tauri + React desktop shell on Windows and macOS. The Python core
is a local business service for SQLite task data, event ingestion, resource
metrics, and task-to-light state calculation.

## Active structure

- `tauri_app/`: shared React UI, Tauri shell, tray, packaging, and native device transport.
- `tauri_bridge.py`: JSON boundary between the native shell and Python business core.
- `codex_status_core/`: SQLite event store, task models, protocol encoding, and Hook ingestion.
- `tests/`: protocol and event-pipeline tests.

Custom local tools can be connected through an external protocol adapter. See
[`CUSTOM_DATA_SOURCES.md`](CUSTOM_DATA_SOURCES.md) for the provider-neutral
adapter boundary. Private socket and protocol details remain in per-user local
configuration and must not be committed.

Bluetooth permission, scanning, persistent connection, reconnect, GATT writes,
identification, and OTA are owned by the shared Tauri/Rust process on both
Windows and macOS. The Python service never opens a Bluetooth connection.

The legacy Tk UI, Python macOS wrapper, Python tray, old PyInstaller/IExpress
packages, and the optional Reporter Skill have been removed. Task capture uses
the supported Hook/event adapters and writes application-owned SQLite data.

## Development

```powershell
cd desktop/tauri_app
pnpm install
pnpm tauri dev
```

## Verification

```powershell
cd desktop
python -m unittest discover -s tests -v

cd tauri_app
pnpm build
cargo check --manifest-path src-tauri/Cargo.toml
```

Release artifacts are generated outside the source tree and collected under
the repository-level `artifacts/` directory.
