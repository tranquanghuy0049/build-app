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

# ----------------------------------------------------------------------- icon
# macOS wants .icns and nothing else — a .png handed to BUNDLE is ignored, which
# is why the Dock showed the blank PyInstaller rocket while Windows had its
# proper icon. Built from the same 1024px source make_icon.py draws for Windows,
# so the two platforms cannot drift apart.
echo "==> Generating app icon"
python3 -m pip install --quiet pillow
python3 packaging/make_icon.py

ICONSET="$SRC_DIR/build/MeetingSummarizer.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size "$SRC_DIR/static/icon.png" \
    --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  # Retina variant of each: macOS picks the @2x file on every display Apple has
  # shipped for a decade, and its absence is what makes an icon look soft.
  sips -z $((size * 2)) $((size * 2)) "$SRC_DIR/static/icon.png" \
    --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$SCRIPT_DIR/icon.icns"
echo "    icon.icns: $(du -h "$SCRIPT_DIR/icon.icns" | cut -f1)"

# ---------------------------------------------------------------------- model
# Stage the speech model into models/ so PyInstaller can bundle it. This is what
# makes the shipped app work offline with no first-use download.
echo "==> Staging speech model (engine=${BUNDLE_ENGINE:-chunkformer})"
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

# Ship the user guide inside the dmg so it travels with the app rather than
# having to be sent separately.
if [ -f "$SRC_DIR/HUONG_DAN_SU_DUNG.txt" ]; then
  cp "$SRC_DIR/HUONG_DAN_SU_DUNG.txt" "$STAGING/"
fi

# ULMO (LZMA) rather than the usual UDZO (zlib): the payload is uncompressed
# dylibs and model weights, so the better ratio takes a real bite out of the
# download. Costs build time and needs macOS 10.15+, which our 11.0 minimum
# already exceeds.
create_dmg() {
  rm -f "$DMG_PATH"
  hdiutil create \
    -volname "Meeting Summarizer" \
    -srcfolder "$STAGING" \
    -ov -format "$1" \
    "$DMG_PATH"
}

# hdiutil intermittently fails "Resource busy" on the CI runners, several
# minutes into compressing a 2.5 GB image, with a diskimages-helper left
# running behind it. Nothing about the build causes it and nothing about the
# build can prevent it — so clean up after it and try again rather than throwing
# away a bundle that took nine minutes to produce and passed its signature
# check.
dmg_built=""
for attempt in 1 2 3; do
  if create_dmg ULMO; then
    dmg_built=1
    break
  fi
  echo "==> hdiutil hỏng (lần $attempt) — dọn dẹp rồi thử lại"
  hdiutil detach "/Volumes/Meeting Summarizer" -force 2>/dev/null || true
  pkill -f diskimages-helper 2>/dev/null || true
  sleep 20
done

# UDZO is the older, far more widely exercised code path. A dmg a couple of
# hundred megabytes larger beats no dmg at all.
if [ -z "$dmg_built" ]; then
  echo "==> ULMO không đóng gói được sau 3 lần; quay về UDZO"
  create_dmg UDZO
fi

echo
echo "==> Done"
du -sh "$APP_PATH" "$DMG_PATH"
