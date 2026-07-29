"""Runtime configuration the user edits from the web UI rather than by hand.

Values live in a .env inside the per-user support directory. A packaged .app is
read-only once installed, so this is the only writable place for them, and it is
also what survives an app upgrade.
"""
import os
import sys

from dotenv import load_dotenv

from app_paths import resource_dir, support_dir

# name -> is_secret
FIELDS = {
    "OPENAI_API_KEY": True,
    "GROQ_API_KEY": True,
    "HF_TOKEN": True,
    "WHISPER_PROVIDER": False,
    "PHOWHISPER_MODEL": False,
    "PHOWHISPER_DEVICE": False,
}

DEFAULTS = {
    "WHISPER_PROVIDER": "openai",
    "PHOWHISPER_MODEL": "vinai/PhoWhisper-small",
    "PHOWHISPER_DEVICE": "auto",
}

CHOICES = {
    "WHISPER_PROVIDER": ("openai", "groq", "phowhisper"),
    "PHOWHISPER_DEVICE": ("auto", "mps", "cuda", "cpu"),
    "PHOWHISPER_MODEL": (
        "vinai/PhoWhisper-tiny",
        "vinai/PhoWhisper-base",
        "vinai/PhoWhisper-small",
        "vinai/PhoWhisper-medium",
    ),
}

# Values shipped in .env.example. Treated as "not configured" so a user who never
# edited the template does not get a confusing auth error from the API instead.
PLACEHOLDERS = {
    "",
    "sk-your-api-key-here",
    "your-groq-api-key-here",
    "gsk-your-groq-key-here",
    "your-hf-token-here",
}

_HEADER = """# Meeting Summarizer configuration
# Managed by the app's Settings screen. Hand edits are preserved, but comments
# and ordering are rewritten whenever settings are saved from the UI.
"""

_COMMENTS = {
    "OPENAI_API_KEY": "# Required for meeting summaries, and for transcription when\n"
                      "# WHISPER_PROVIDER=openai. https://platform.openai.com/api-keys",
    "GROQ_API_KEY": "# Only for WHISPER_PROVIDER=groq. Free: https://console.groq.com",
    "HF_TOKEN": "# Optional. Only needed for gated HuggingFace models.",
    "WHISPER_PROVIDER": "# openai | groq | phowhisper (phowhisper runs locally, no API key)",
    "PHOWHISPER_MODEL": "# Larger is more accurate but slower.",
    "PHOWHISPER_DEVICE": "# auto picks Metal (mps) on Apple Silicon.",
}


def config_path():
    return os.path.join(support_dir(), ".env")


def load():
    """Populate os.environ from disk. Safe to call repeatedly."""
    # A .env beside the source is the legacy location; the writable one wins.
    legacy = os.path.join(resource_dir(), ".env")
    if os.path.exists(legacy):
        load_dotenv(legacy, override=False)
    path = config_path()
    if os.path.exists(path):
        load_dotenv(path, override=True)


def get(name):
    value = os.getenv(name, "")
    if value in PLACEHOLDERS:
        return DEFAULTS.get(name, "")
    return value


def is_set(name):
    return os.getenv(name, "") not in PLACEHOLDERS


def mask(value):
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:3]}{'•' * 8}{value[-4:]}"


def public_state():
    """What the UI may see. Secrets are masked — the browser never receives a
    usable key back, only enough to recognise which one is stored."""
    state = {}
    for name, secret in FIELDS.items():
        if secret:
            state[name] = {
                "configured": is_set(name),
                "preview": mask(os.getenv(name, "")) if is_set(name) else "",
            }
        else:
            state[name] = {"value": get(name)}
    return {
        "fields": state,
        "choices": {k: list(v) for k, v in CHOICES.items()},
        "config_path": config_path(),
        "ready": readiness(),
    }


def readiness():
    """Whether the app can actually run with the current configuration."""
    problems = []
    provider = get("WHISPER_PROVIDER")

    if not is_set("OPENAI_API_KEY"):
        problems.append("Thiếu OPENAI_API_KEY — phần tóm tắt cuộc họp cần khoá này.")
    if provider == "openai" and not is_set("OPENAI_API_KEY"):
        problems.append("Nhận dạng giọng nói đang dùng OpenAI nhưng chưa có OPENAI_API_KEY.")
    if provider == "groq" and not is_set("GROQ_API_KEY"):
        problems.append("Nhận dạng giọng nói đang dùng Groq nhưng chưa có GROQ_API_KEY.")

    return {"ok": not problems, "problems": problems}


def validate(updates):
    errors = []
    for name, value in updates.items():
        if name not in FIELDS:
            errors.append(f"Trường không hợp lệ: {name}")
            continue
        if name in CHOICES and value and value not in CHOICES[name]:
            errors.append(f"{name} phải là một trong: {', '.join(CHOICES[name])}")
        if "\n" in str(value) or "\r" in str(value):
            errors.append(f"{name} không được chứa xuống dòng")
    return errors


def save(updates):
    """Merge updates into the stored config and reload them into os.environ.

    A blank secret means "leave what is already stored" — the UI only ever shows
    a masked preview, so it cannot echo the real value back to us.
    """
    errors = validate(updates)
    if errors:
        raise ValueError("; ".join(errors))

    merged = {name: os.getenv(name, "") for name in FIELDS}
    for name, value in updates.items():
        value = str(value).strip()
        if FIELDS[name] and not value:
            continue
        merged[name] = value

    for name, default in DEFAULTS.items():
        if merged.get(name, "") in PLACEHOLDERS:
            merged[name] = default

    path = config_path()
    lines = [_HEADER]
    for name in FIELDS:
        comment = _COMMENTS.get(name)
        if comment:
            lines.append(comment)
        lines.append(f"{name}={merged.get(name, '')}")
        lines.append("")

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)

    if sys.platform != "win32":
        # The file holds API keys; keep it out of other users' reach.
        os.chmod(path, 0o600)

    for name, value in merged.items():
        os.environ[name] = value

    return public_state()
