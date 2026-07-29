"""Entry point for the macOS .app bundle.

Starts the FastAPI server on a free local port and opens the default browser at
it. Audio capture happens in the browser (getUserMedia), so the bundle itself
never touches CoreAudio.
"""
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
PORT_ATTEMPTS = 50

def _setup_output(log_path):
    """Make print() safe before anything else runs.

    Two hazards: a windowed bundle has no stdout at all (every print raises), and
    an inherited console may be on a codepage that cannot encode the Vietnamese
    strings below.
    """
    if sys.stdout is None or sys.stderr is None:
        stream = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
        return log_path

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    return None


def _alert(title, message):
    if sys.platform != "darwin":
        print(f"{title}: {message}")
        return
    safe = message.replace("\\", "\\\\").replace('"', '\\"')[:900]
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display dialog "{safe}" with title "{title}" buttons {{"OK"}} '
             f'default button "OK" with icon stop'],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass


def _find_free_port(start=DEFAULT_PORT, attempts=PORT_ATTEMPTS):
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port in {start}-{start + attempts - 1}")


def _open_browser_when_ready(url, timeout=180):
    """Poll /api/health rather than sleeping a fixed amount — importing torch can
    take 30s+ on a cold start."""
    health = f"{url}/api/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=2) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    print(f"Server did not become healthy within {timeout}s; not opening browser.")


def _selftest():
    """Import every heavyweight dependency and report versions.

    Exists because the bundle is built on CI by someone with no Mac to test on:
    a missing torch dylib or an uncollected transformers data file shows up here
    instead of as a silent failure on a user's machine.
    """
    failures = []

    def check(label, fn):
        try:
            print(f"  {label}: {fn()}")
        except Exception as e:
            print(f"  {label}: FAILED - {type(e).__name__}: {e}")
            failures.append(label)

    print("Meeting Summarizer selftest")
    print(f"  python: {sys.version.split()[0]}")
    print(f"  frozen: {getattr(sys, 'frozen', False)}")

    def _torch():
        import torch
        return f"{torch.__version__} (mps={torch.backends.mps.is_available()})"

    def _transformers():
        import transformers
        # Touch the whisper classes specifically; the pipeline resolves them by
        # name at runtime, which is exactly what PyInstaller cannot see.
        from transformers import WhisperForConditionalGeneration, WhisperProcessor  # noqa: F401
        return transformers.__version__

    def _templates():
        from app_paths import resource_dir
        path = os.path.join(resource_dir(), "templates", "index.html")
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return f"{os.path.getsize(path)} bytes"

    check("torch", _torch)
    check("transformers", _transformers)
    check("tokenizers", lambda: __import__("tokenizers").__version__)
    check("soundfile", lambda: __import__("soundfile").__version__)
    check("soxr", lambda: __import__("soxr").__version__)
    check("numpy", lambda: __import__("numpy").__version__)
    check("fastapi", lambda: __import__("fastapi").__version__)
    check("uvicorn", lambda: __import__("uvicorn").__version__)
    check("openai", lambda: __import__("openai").__version__)
    check("web app", lambda: __import__("web").app.title)
    check("settings", lambda: f"{len(__import__('settings').FIELDS)} fields")
    check("templates", _templates)

    if failures:
        print(f"\nSELFTEST FAILED: {', '.join(failures)}")
        return 1
    print("\nSELFTEST OK")
    return 0


def main():
    from app_paths import hf_cache_dir, output_dir, support_dir

    log_path = os.path.join(support_dir(), "launcher.log")
    _setup_output(log_path)

    if "--selftest" in sys.argv:
        return _selftest()

    os.environ.setdefault("HF_HOME", hf_cache_dir())
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    print(f"--- Meeting Summarizer starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    print(f"config:  {os.path.join(support_dir(), '.env')}")
    print(f"outputs: {output_dir()}")
    print(f"log:     {log_path}")

    # No first-run gate here: API keys are entered in the web UI's Settings
    # screen, so the server has to come up even with no configuration at all.

    try:
        port = _find_free_port()
    except RuntimeError as e:
        _alert("Meeting Summarizer", str(e))
        return 1

    url = f"http://{HOST}:{port}"
    print(f"Serving on {url}")

    if os.getenv("MEETING_SUMMARIZER_NO_BROWSER") != "1":
        threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    try:
        import uvicorn
        from web import app
        uvicorn.run(app, host=HOST, port=port, log_level="info")
    except Exception as e:
        import traceback
        traceback.print_exc()
        _alert(
            "Meeting Summarizer - Lỗi",
            f"Ứng dụng không khởi động được:\n\n{e}\n\nChi tiết trong log:\n{log_path}",
        )
        return 1
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
