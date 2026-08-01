#!/usr/bin/env bash
#
# Validates a freshly built MeetingSummarizer.app without a human in front of it.
# Catches the two failure modes that a Windows developer cannot reproduce
# locally: a dependency PyInstaller failed to collect, and a bundle macOS
# refuses to execute.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_BIN="$SRC_DIR/dist/MeetingSummarizer.app/Contents/MacOS/MeetingSummarizer"
SUPPORT_DIR="$HOME/Library/Application Support/MeetingSummarizer"
LOG="$SUPPORT_DIR/launcher.log"

[ -x "$APP_BIN" ] || { echo "ERROR: $APP_BIN missing or not executable"; exit 1; }

# The launcher shows a blocking osascript dialog the first time it runs with no
# config. Seed one so the smoke test does not hang forever.
mkdir -p "$SUPPORT_DIR"
cat > "$SUPPORT_DIR/.env" <<'ENV'
GEMINI_API_KEY=AIza-smoketest-not-a-real-key
WHISPER_PROVIDER=chunkformer
CHUNKFORMER_MODEL=khanhld/chunkformer-ctc-large-vie
LOCAL_ASR_DEVICE=auto
ENV
# Deliberately "auto", the shipped default. The CI runner reports Metal as
# available but cannot allocate on it, which is precisely the condition the
# mps->cpu fallback exists for — so this configuration tests that fallback
# instead of sidestepping it.

echo "==> 1/3 Gatekeeper assessment"
# Ad-hoc signed and unnotarised, so spctl is *expected* to reject it. What
# matters is that a signature exists at all.
codesign --display --verbose=2 "$SRC_DIR/dist/MeetingSummarizer.app" 2>&1 | head -20
spctl --assess --type execute --verbose "$SRC_DIR/dist/MeetingSummarizer.app" 2>&1 || \
  echo "(expected: rejected because the build is not notarised)"

echo
echo "==> 2/4 Dependency selftest"
"$APP_BIN" --selftest

echo
echo "==> 3/4 Real transcription with the bundled model, network disabled"
# The one check that proves the app does its job rather than merely starting.
"$APP_BIN" --selftest-transcribe

echo
echo "==> 4/4 Server starts and answers /api/health"
MEETING_SUMMARIZER_NO_BROWSER=1 "$APP_BIN" &
APP_PID=$!
trap 'kill "$APP_PID" 2>/dev/null || true' EXIT

ok=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "ERROR: process exited before becoming healthy"
    break
  fi
  sleep 1
done

if [ "$ok" != "1" ]; then
  echo "ERROR: /api/health never responded"
  [ -f "$LOG" ] && { echo "--- launcher.log ---"; tail -50 "$LOG"; }
  exit 1
fi

echo "    /api/health OK"
curl -fsS "http://127.0.0.1:8000/" | head -c 200
echo
echo
echo "==> Smoke test passed"
