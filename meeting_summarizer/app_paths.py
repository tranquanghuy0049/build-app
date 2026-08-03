"""Filesystem paths that differ between a source checkout and a frozen bundle.

A macOS .app is launched by LaunchServices with the working directory set to "/",
and its own directory is read-only once installed under /Applications. Anything
that used os.getcwd() or wrote next to the executable has to go through here.
"""
import os
import sys

APP_NAME = "MeetingSummarizer"

# What the folder of finished meetings is called inside Documents.
#
# Windows gets a Vietnamese name: it is the one path an ordinary user has to
# find by eye, and an English folder name among their own Vietnamese ones is the
# hardest thing to spot. macOS deliberately keeps the original ASCII name — the
# builds already shipped write there, and renaming it would strand the meetings
# of anyone who updates.
OUTPUT_FOLDER_NAME = {
    "darwin": APP_NAME,
    "win32": "Biên bản cuộc họp",
}.get(sys.platform, APP_NAME)


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

    Only a frozen bundle relocates these; a source checkout keeps writing to
    ./meeting_outputs so the existing developer workflow is unchanged.

    A frozen app cannot use the working directory: a .app is launched with it set
    to "/", and an installed .exe inherits whatever directory the shortcut or the
    shell happened to pass — Program Files, where the user cannot write, or
    System32 if launched from an elevated prompt.
    """
    if is_frozen() and sys.platform in ("darwin", "win32"):
        # Not expanduser("~/Documents"): on Windows that yields a path with
        # mixed separators, which works but is printed to the log and shown to
        # users looking for their transcripts.
        base = os.path.join(os.path.expanduser("~"), "Documents", OUTPUT_FOLDER_NAME)
    else:
        base = os.path.join(os.getcwd(), "meeting_outputs")
    os.makedirs(base, exist_ok=True)
    return base


def resolve_output_path(rel: str = None) -> str:
    """Turn a browser-supplied relative path into a real one under output_dir().

    Raises ValueError for anything that would escape the folder — the browser is
    the only caller today, but a path from a request is untrusted either way.
    """
    root = os.path.realpath(output_dir())
    if not rel:
        return root
    target = os.path.realpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        raise ValueError("Đường dẫn nằm ngoài thư mục lưu")
    return target


def reveal_output_dir(rel: str = None):
    """Open the output folder — or one meeting inside it — in Explorer / Finder.

    Printing the path is not the same as being able to reach it: a packaged app
    lands its files somewhere the user never chose and, on macOS, somewhere the
    Finder sidebar does not show. Opening it is the only reliable answer to
    "where did my meeting go".
    """
    import subprocess

    path = resolve_output_path(rel)
    if not os.path.isdir(path):
        path = os.path.dirname(path)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - validated to sit under output_dir()
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
    return path


def bundled_model_dir(repo_id: str):
    """Path to a speech model shipped inside the app, or None if not bundled.

    Build-time staging writes each checkpoint to models/<repo_id with / as __>,
    so a requested size that was not bundled falls through to a normal download
    instead of silently loading the wrong weights.
    """
    path = os.path.join(resource_dir(), "models", repo_id.replace("/", "__"))
    return path if os.path.isdir(path) else None


def hf_cache_dir() -> str:
    """HuggingFace cache. Its default (~/.cache/huggingface) is fine, but pinning
    it keeps the several-hundred-MB PhoWhisper download with the rest of our state.
    """
    path = os.path.join(support_dir(), "hf_cache")
    os.makedirs(path, exist_ok=True)
    return path
