#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TAURI_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
WIDGET_DIR="$TAURI_DIR/widget"

if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  set -- CODE_SIGNING_ALLOWED=YES "CODE_SIGN_IDENTITY=$APPLE_SIGNING_IDENTITY"
  if [ -n "${APPLE_DEVELOPMENT_TEAM:-}" ]; then
    set -- "$@" "DEVELOPMENT_TEAM=$APPLE_DEVELOPMENT_TEAM"
  fi
else
  set -- CODE_SIGNING_ALLOWED=NO
fi

xcodebuild \
  -quiet \
  -project "$WIDGET_DIR/BeaconQuickActions.xcodeproj" \
  -scheme BeaconQuickActions \
  -configuration Release \
  -destination 'platform=macOS' \
  -derivedDataPath "$WIDGET_DIR/DerivedData" \
  SYMROOT="$WIDGET_DIR/build" \
  "$@" \
  build

# Tauri's local ad-hoc bundle pass does not add extension entitlements. Sign
# the WidgetKit payload here so macOS can register it without an Apple team ID.
if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
  codesign \
    --force \
    --sign - \
    --entitlements "$TAURI_DIR/../widget/BeaconQuickActions/BeaconQuickActions.entitlements" \
    "$WIDGET_DIR/build/Release/BeaconQuickActions.appex"
fi
