"""Entry point for the packaged macOS and Windows builds.

Starts the FastAPI server on a free local port and puts a UI in front of it.

That is a native window (pywebview) on both, borrowing whichever engine the OS
already has — WebView2 on Windows, WKWebView on macOS. Neither bundles a
browser: what ships is a wrapper of a few hundred kilobytes. Both backends
leave microphone permission unimplemented, so the app answers for itself in
each case; the two mechanisms have nothing in common beyond the intent.

The browser remains the fallback, and is opened rather than a window whenever
permission could not be arranged, the window failed to start, or
MEETING_SUMMARIZER_BROWSER=1 asks for it. It works — it is just visibly a web
page, with an address bar reading 127.0.0.1 and a tab that can be closed while
the server keeps running.

Audio capture still happens in the page (getUserMedia), so the bundle itself
never touches CoreAudio or WASAPI.
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
    # Always log it: the dialog is best-effort, the log is not. Both builds are
    # windowed, so a bare print would reach nobody.
    print(f"{title}: {message}")

    if sys.platform == "win32":
        try:
            import ctypes
            MB_ICONERROR = 0x10
            MB_SETFOREGROUND = 0x10000
            ctypes.windll.user32.MessageBoxW(
                None, message[:900], title, MB_ICONERROR | MB_SETFOREGROUND
            )
        except Exception:
            pass
        return

    if sys.platform != "darwin":
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


def _wait_healthy(url, timeout=180):
    """Poll /api/health rather than sleeping a fixed amount — importing torch can
    take 30s+ on a cold start."""
    health = f"{url}/api/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


class _Server:
    """uvicorn on a background thread, with somewhere for its errors to land.

    It used to own the main thread. The window loop needs that instead — on
    macOS the UI *must* be on the main thread — so the roles are swapped. The
    thread is a daemon: closing the window ends the process and takes the
    server with it, which is what stops the old habit of leaving uvicorn
    running after the user thinks they have quit.
    """

    def __init__(self, port):
        self.port = port
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            import uvicorn
            from web import app
            uvicorn.run(app, host=HOST, port=self.port, log_level="info")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error = f"{type(e).__name__}: {e}"

    def start(self):
        self.thread.start()
        return self


# pythonnet does not keep our delegates alive; a handler that gets collected
# stops firing and WebView2 silently falls back to asking the user again.
_PERMISSION_HANDLERS = []


def _allow_microphone(window, url):
    """Grant the page the microphone without ever asking.

    pywebview only implements permission handling for its Qt backend; the
    Windows one leaves it unhandled, so WebView2 puts up its own prompt — every
    launch, because nothing records the answer. For an app whose whole purpose
    is recording a meeting, a prompt that can be dismissed by mistake is a
    failure mode, not a safeguard.

    Two mechanisms, deliberately: the event handler covers whatever port the
    server happened to get, and the profile write clears a Deny that an earlier
    version may already have stored.
    """
    if sys.platform != "win32":
        return False, "chi ap dung cho Windows"

    from System import Action
    from Microsoft.Web.WebView2.Core import (
        CoreWebView2PermissionKind,
        CoreWebView2PermissionState,
    )
    from webview.platforms.winforms import BrowserView

    control = None
    for _ in range(100):
        browser = BrowserView.instances.get(window.uid)
        if browser is not None and getattr(browser, "webview", None) is not None:
            control = browser.webview
            break
        time.sleep(0.1)
    if control is None:
        return False, "khong tim thay control WebView2"

    state = {}

    def setup():
        # Every CoreWebView2 member is UI-thread-only; touching it from here
        # without the Invoke below raises InvalidOperationException.
        try:
            core = control.CoreWebView2
            if core is None:
                state["err"] = "CoreWebView2 chua khoi tao xong"
                return

            def on_permission(sender, args):
                if args.PermissionKind == CoreWebView2PermissionKind.Microphone:
                    args.State = CoreWebView2PermissionState.Allow
                    try:
                        args.SavesInProfile = True
                    except Exception:
                        pass

            core.PermissionRequested += on_permission
            _PERMISSION_HANDLERS.append(on_permission)

            # Overrides a stored Deny. Permission is keyed by origin, and the
            # origin includes the port, so this only covers the current run —
            # the handler above is what makes it reliable.
            try:
                core.Profile.SetPermissionStateAsync(
                    CoreWebView2PermissionKind.Microphone, url,
                    CoreWebView2PermissionState.Allow,
                )
            except Exception as e:
                state["profile_err"] = str(e)

            state["ok"] = True
        except Exception as e:
            state["err"] = f"{type(e).__name__}: {e}"

    for _ in range(30):
        control.Invoke(Action(setup))
        if state.get("ok"):
            return True, state.get("profile_err", "")
        time.sleep(0.3)
    return False, state.get("err", "khong ro")


# The macOS half of the same problem. WKWebView asks the application before it
# lets a page reach the microphone and denies by default when nothing answers,
# and pywebview's cocoa backend never implements that delegate method — exactly
# the gap its Windows backend has, with a different fix.
_MEDIA_SELECTOR_NAME = (
    "webView_requestMediaCapturePermissionForOrigin_"
    "initiatedByFrame_type_decisionHandler_"
)


def _install_macos_media_permission():
    """Teach pywebview's WKWebView delegate to grant the microphone.

    Added to the delegate class, not to one instance, and before any window
    exists: whether this worked is then known *before* the user is looking at a
    window that cannot record. main() falls back to the browser when it did
    not, which is the path every shipped macOS build has used so far.

    Returns (ok, detail).
    """
    try:
        import objc
        from webview.platforms.cocoa import BrowserView
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    delegate = BrowserView.BrowserDelegate
    if hasattr(delegate, _MEDIA_SELECTOR_NAME):
        return True, "pywebview đã tự xử lý"

    # WKPermissionDecisionGrant. Written out rather than imported: it lives in a
    # WebKit enum that PyObjC exposes under different names across versions, and
    # an import error here would cost the window for the sake of a constant.
    grant_decision = 1

    def grant(self, webview, origin, frame, kind, decision_handler):
        decision_handler(grant_decision)

    try:
        objc.classAddMethods(delegate, [objc.selector(
            grant,
            selector=(b"webView:requestMediaCapturePermissionForOrigin:"
                      b"initiatedByFrame:type:decisionHandler:"),
            # void; self, _cmd, webView, origin, frame, WKMediaCaptureType
            # (NSInteger), and the completion block.
            signature=b"v@:@@@q@?",
        )])
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, ""


def _run_native_window(url):
    """Show the app in its own window. Returns when the user closes it."""
    import webview

    # Sized against the actual display, not a fixed guess. A hard 860px is taller
    # than the usable area of a 1080p screen at 125% or 150% scaling — the window
    # opened with its bottom off-screen and the buttons out of reach.
    try:
        screen = webview.screens[0]
        sw, sh = int(screen.width), int(screen.height)
    except Exception:
        sw, sh = 1440, 900
    width = max(760, min(1060, int(sw * 0.78)))
    # 0.82 leaves room for the title bar and the taskbar, which the reported
    # screen height includes.
    height = max(560, min(820, int(sh * 0.82)))
    print(f"window: {width}x{height} (man hinh {sw}x{sh})")

    window = webview.create_window(
        "Biên bản cuộc họp", url,
        width=width, height=height,
        min_size=(720, 540),
    )

    # macOS has nothing to do here: its permission handler was installed on the
    # delegate class before this function was ever called, and it applies to
    # whatever window comes after. Windows has to reach into a live WebView2
    # control, which does not exist until the window has started.
    if sys.platform != "win32":
        webview.start(window)
        return

    # pywebview hands the window to this callback, so it takes an argument even
    # though the closure already has one.
    def on_start(win):
        ok, detail = _allow_microphone(win, url)
        print(f"microphone permission: {'granted' if ok else 'NOT granted'} {detail}".strip())

    webview.start(on_start, window)


def _selftest():
    """Import every heavyweight dependency and report versions.

    Exists because the bundle is built on CI by someone with no Mac to test on:
    a missing torch dylib or an uncollected chunkformer data file shows up here
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
        available = []
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            available.append("mps")
        if torch.cuda.is_available():
            available.append("cuda")
        return f"{torch.__version__} ({'+'.join(available) or 'cpu only'})"

    def _chunkformer():
        import chunkformer
        # Touch the class the app actually constructs, not just the package.
        from chunkformer import ChunkFormerModel  # noqa: F401
        return getattr(chunkformer, "__version__", "ok")

    def _templates():
        from app_paths import resource_dir
        path = os.path.join(resource_dir(), "templates", "index.html")
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return f"{os.path.getsize(path)} bytes"

    def _bundled_model():
        import settings
        from app_paths import bundled_model_dir
        settings.load()
        model_id = _configured_model()
        path = bundled_model_dir(model_id)
        if not path:
            raise FileNotFoundError(f"{model_id} not bundled")
        size = sum(
            os.path.getsize(os.path.join(root, f))
            for root, _, files in os.walk(path) for f in files
        )
        return f"{model_id} ({size / (1024 * 1024):.0f} MB)"

    check("torch", _torch)
    check("chunkformer", _chunkformer)
    check("numpy", lambda: __import__("numpy").__version__)
    check("fastapi", lambda: __import__("fastapi").__version__)
    check("uvicorn", lambda: __import__("uvicorn").__version__)
    check("openai", lambda: __import__("openai").__version__)
    check("web app", lambda: __import__("web").app.title)
    check("settings", lambda: f"{len(__import__('settings').FIELDS)} fields")
    check("google.genai", lambda: __import__("google.genai", fromlist=["Client"]).__name__)
    check("templates", _templates)
    check("bundled model", _bundled_model)

    if sys.platform == "darwin":
        # Worth a check of its own because it is the one thing CI can prove
        # about the window: the runner has no microphone and nobody can answer
        # a permission dialog there, but a PyObjC selector whose signature is
        # wrong, or a cocoa backend PyInstaller failed to collect, fails right
        # here — on a real Mac, inside the real bundle.
        def _window():
            import webview
            ok, detail = _install_macos_media_permission()
            if not ok:
                raise RuntimeError(detail)
            return f"pywebview {webview.__version__}, quyền micro cài được"

        check("cửa sổ ứng dụng", _window)

    if failures:
        print(f"\nSELFTEST FAILED: {', '.join(failures)}")
        return 1
    print("\nSELFTEST OK")
    return 0


def _configured_model():
    """Repo id of the local speech model the current settings would use."""
    import settings
    return settings.get("CHUNKFORMER_MODEL")


def _synthesise_speech(wav_path, phrase):
    """Write `phrase` to a 16 kHz mono WAV using whatever TTS the OS ships.

    Both engines are built in, so the transcribe selftest needs no fixture audio
    committed to the repo. Note that Windows reads the Vietnamese phrase with an
    English voice unless a Vietnamese one is installed — good enough, since what
    is under test is the model, not the pronunciation.
    """
    if sys.platform == "darwin":
        subprocess.run(
            ["say", "-o", wav_path, "--data-format=LEI16@16000", phrase],
            check=True, capture_output=True, timeout=120,
        )
    elif sys.platform == "win32":
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
            "16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
            "[System.Speech.AudioFormat.AudioChannel]::Mono); "
            f"$s.SetOutputToWaveFile('{wav_path}', $fmt); "
            f"$s.Speak('{phrase}'); $s.Dispose()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True, capture_output=True, timeout=120,
        )
    else:
        raise RuntimeError(f"no TTS wired up for {sys.platform}")

    if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
        raise RuntimeError("TTS produced no audio")


def _selftest_transcribe():
    """Transcribe real audio with the bundled model, with the network shut off.

    The import-only selftest proves libraries are present; this proves the app
    actually works. Audio is synthesised by the OS (see _synthesise_speech), and
    HF_HUB_OFFLINE is forced on so a pass also proves the weights really shipped
    inside the bundle rather than being quietly downloaded.
    """
    import subprocess
    import tempfile

    import settings
    from app_paths import bundled_model_dir

    settings.load()
    provider = settings.get("WHISPER_PROVIDER")
    model_id = _configured_model()
    print(f"  engine: {provider}")

    bundled = bundled_model_dir(model_id)
    if not bundled:
        print(f"  FAIL: {model_id} was not bundled into the app")
        return 1
    print(f"  bundled weights: {bundled}")

    # If anything reaches for the network after this, it fails loudly instead of
    # silently masking a missing-weights bug.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    tmp = tempfile.mkdtemp(prefix="selftest_")
    wav = os.path.join(tmp, "sample.wav")
    phrase = "Xin chào, đây là bản ghi thử nghiệm cho cuộc họp hôm nay."
    try:
        _synthesise_speech(wav, phrase)
    except Exception as e:
        print(f"  FAIL: could not synthesise test audio: {e}")
        return 1
    print(f"  input: {os.path.getsize(wav)} bytes of 16kHz PCM")

    from transcriber import Transcriber

    started = time.time()
    transcriber = Transcriber(None, language="vi", model=model_id, mode="chunkformer")
    text = transcriber.transcribe_file(wav)
    elapsed = time.time() - started

    selected = Transcriber._select_device()
    actual = "cpu" if transcriber._force_cpu else selected
    print(f"  device selected: {selected}")
    print(f"  device used:     {actual}"
          + ("  (fell back from " + selected + ")" if actual != selected else ""))
    print(f"  elapsed: {elapsed:.1f}s")
    print(f"  transcript: {text!r}")

    if not text.strip():
        print("  FAIL: transcription returned nothing (see errors above)")
        return 1
    print("\nTRANSCRIBE SELFTEST OK")
    return 0


def main():
    from app_paths import hf_cache_dir, output_dir, support_dir

    log_path = os.path.join(support_dir(), "launcher.log")
    _setup_output(log_path)

    if "--selftest" in sys.argv:
        return _selftest()
    if "--selftest-transcribe" in sys.argv:
        return _selftest_transcribe()

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

    server = _Server(port).start()
    if not _wait_healthy(url):
        _alert(
            "Meeting Summarizer - Lỗi",
            "Ứng dụng không khởi động được:\n\n"
            + (server.error or "máy chủ nội bộ không phản hồi")
            + f"\n\nChi tiết trong log:\n{log_path}",
        )
        return 1

    # Headless: used by the smoke tests and by anyone who would rather drive the
    # app from their own browser.
    if os.getenv("MEETING_SUMMARIZER_NO_BROWSER") == "1":
        print("MEETING_SUMMARIZER_NO_BROWSER=1 — chỉ chạy máy chủ, không mở cửa sổ")
        server.thread.join()
        return 0

    def _use_browser(why):
        print(f"Mở giao diện bằng trình duyệt mặc định ({why})")
        webbrowser.open(url)
        server.thread.join()
        return 0

    # An escape hatch that needs no rebuild: the browser is the path this app
    # shipped on for months, so anyone whose window misbehaves has somewhere to
    # go while the cause is being found.
    if os.getenv("MEETING_SUMMARIZER_BROWSER") == "1":
        return _use_browser("MEETING_SUMMARIZER_BROWSER=1")

    if sys.platform == "darwin":
        # Deliberately before the window is created rather than inside it. A
        # window whose microphone is silently denied looks like a working app
        # right up until the meeting comes back empty; the browser, which asks
        # for permission itself, is strictly better than that.
        ok, detail = _install_macos_media_permission()
        print(f"microphone permission: {'granted' if ok else 'NOT granted'} {detail}".strip())
        if not ok:
            return _use_browser(f"không cấp được quyền micro cho cửa sổ: {detail}")
    elif sys.platform != "win32":
        return _use_browser(f"chưa hỗ trợ cửa sổ riêng trên {sys.platform}")

    try:
        _run_native_window(url)
        return 0
    except Exception as e:
        # A machine without WebView2, or a Mac whose WebKit refused to start,
        # should still get a working app rather than an error dialog.
        import traceback
        traceback.print_exc()
        return _use_browser(f"không mở được cửa sổ ứng dụng: {e}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
