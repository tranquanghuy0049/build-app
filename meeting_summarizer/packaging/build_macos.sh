#!/usr/bin/env bash
#
# Builds MeetingSummarizer.app and a .dmg. Runs unchanged on a GitHub Actions
# macOS runner or on a real Mac.
#
#   ./packaging/build_macos.sh
#
# Set APP_VERSION to stamp the bundle (default 1.0.0).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ARCH="$(uname -m)"

# CFBundleVersion must be 1-3 dot-separated integers, so strip a leading "v"
# from tag-derived versions and fall back if we were handed a branch name.
APP_VERSION="${APP_VERSION:-1.0.0}"
APP_VERSION="${APP_VERSION#v}"
if ! [[ "$APP_VERSION" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]]; then
  echo "==> '$APP_VERSION' is not a valid bundle version; using 1.0.0"
  APP_VERSION="1.0.0"
fi
export APP_VERSION

APP_PATH="$SRC_DIR/dist/MeetingSummarizer.app"
DMG_PATH="$SRC_DIR/dist/MeetingSummarizer-${APP_VERSION}-${ARCH}.dmg"

echo "==> Building Meeting Summarizer $APP_VERSION for $ARCH"
cd "$SRC_DIR"

# ---------------------------------------------------------------- dependencies
python3 -m pip install --upgrade pip wheel

if [ "$ARCH" = "x86_64" ]; then
  # PyTorch's last macOS Intel wheel is 2.2.2, and it is built against the
  # numpy 1.x ABI.
  echo "==> Intel Mac: pinning torch==2.2.2 / numpy<2"
  python3 -m pip install "torch==2.2.2" "numpy<2"
else
  python3 -m pip install "torch>=2.2.0"
fi

python3 -m pip install -r requirements-mac.txt
python3 -m pip install "pyinstaller>=6.6.0"

python3 - <<'PY'
import torch, platform
print(f"torch {torch.__version__} on {platform.machine()}, mps={torch.backends.mps.is_available()}")
PY

# ---------------------------------------------------------------------- model
# Stage the speech model into models/ so PyInstaller can bundle it. This is what
# makes the shipped app work offline with no first-use download.
echo "==> Staging PhoWhisper weights (${PHOWHISPER_BUNDLE_MODEL:-vinai/PhoWhisper-small})"
python3 packaging/fetch_model.py

# --------------------------------------------------------------------- bundle
rm -rf build dist
python3 -m PyInstaller --noconfirm --clean packaging/MeetingSummarizer.spec

[ -d "$APP_PATH" ] || { echo "ERROR: $APP_PATH was not produced"; exit 1; }

# ------------------------------------------------------------------- signing
# Ad-hoc signature. Without any signature at all, macOS on Apple Silicon refuses
# to launch the bundle outright ("is damaged and can't be opened"). This does not
# replace notarisation — see MACOS.md for what users still have to do on first
# launch.
echo "==> Ad-hoc signing"
codesign --force --deep --sign - "$APP_PATH"
codesign --verify --verbose=2 "$APP_PATH" || echo "WARNING: signature verification reported issues"

# ----------------------------------------------------------------------- dmg
echo "==> Building dmg"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
# ditto, not cp: it preserves the extended attributes that carry the signature.
ditto "$APP_PATH" "$STAGING/MeetingSummarizer.app"
ln -s /Applications "$STAGING/Applications"

rm -f "$DMG_PATH"
hdiutil create \
  -volname "Meeting Summarizer" \
  -srcfolder "$STAGING" \
  -ov -format UDZO \
  "$DMG_PATH"

echo
echo "==> Done"
du -sh "$APP_PATH" "$DMG_PATH"
