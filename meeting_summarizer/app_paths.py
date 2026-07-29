"""Filesystem paths that differ between a source checkout and a frozen bundle.

A macOS .app is launched by LaunchServices with the working directory set to "/",
and its own directory is read-only once installed under /Applications. Anything
that used os.getcwd() or wrote next to the executable has to go through here.
"""
import os
import sys

APP_NAME = "MeetingSummarizer"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_dir() -> str:
    """Read-only files shipped with the app (templates, bundled defaults)."""
    if is_frozen():
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def support_dir() -> str:
    """Writable per-user directory for config, caches and logs."""
    if sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~/Library/Application Support"), APP_NAME)
    elif sys.platform == "win32":
        base = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), APP_NAME)
    else:
        base = os.path.join(os.path.expanduser("~/.local/share"), APP_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def output_dir() -> str:
    """Where transcripts and summaries land.

    Only the frozen macOS app relocates these; a source checkout keeps writing to
    ./meeting_outputs so the existing Windows workflow is unchanged.
    """
    if is_frozen() and sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~/Documents"), APP_NAME)
    else:
        base = os.path.join(os.getcwd(), "meeting_outputs")
    os.makedirs(base, exist_ok=True)
    return base


def hf_cache_dir() -> str:
    """HuggingFace cache. Its default (~/.cache/huggingface) is fine, but pinning
    it keeps the several-hundred-MB PhoWhisper download with the rest of our state.
    """
    path = os.path.join(support_dir(), "hf_cache")
    os.makedirs(path, exist_ok=True)
    return path
