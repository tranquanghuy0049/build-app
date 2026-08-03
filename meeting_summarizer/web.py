import os
import re
import sys
import json
import uuid
import time
import wave
import tempfile
import shutil
import asyncio
import numpy as np
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from starlette.requests import Request
from openai import OpenAI

import settings
from app_paths import output_dir, resource_dir, reveal_output_dir, resolve_output_path

# A windowed .app bundle has no console, so sys.stdout/stderr are None there.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

settings.load()

from transcriber import Transcriber
import summarizer as summarizer_mod
from summarizer import Summarizer

SAMPLE_RATE = 16000
OVERLAP_SAMPLES = SAMPLE_RATE * 2
# Longest we ever let audio sit unrecognised, for someone who talks without
# pausing. Most chunks are cut earlier than this, at the first pause.
FLUSH_INTERVAL = 6
# A pause only cuts a chunk once there is at least this much audio, so a
# hesitation mid-sentence does not produce a two-word line.
MIN_FLUSH_INTERVAL = 2.5
# How much quiet at the end of the buffer counts as "they stopped talking".
SILENCE_TAIL = 0.6
# Off-topic checking is paced by the clock, not by chunk count. Tied to a count
# it silently sped up when chunks got shorter, and it is the heaviest consumer
# of the Gemini free tier's per-minute quota.
TOPIC_CHECK_INTERVAL = 30
NOISE_FLOOR = None

app = FastAPI(title="Meeting Summarizer")


class ConfigError(Exception):
    """Configuration the user can fix in Settings, as opposed to a bug."""


def get_gemini_client():
    """Client for everything text-generation: minutes and topic tracking."""
    if not settings.is_set("GEMINI_API_KEY"):
        raise ConfigError("Chưa có Gemini API Key. Mở Cài đặt (⚙) để nhập khoá.")
    from google import genai
    return genai.Client(api_key=settings.get("GEMINI_API_KEY"))


def get_openai_client():
    if not settings.is_set("OPENAI_API_KEY"):
        raise ConfigError("Chưa có OPENAI_API_KEY. Mở Cài đặt (⚙) để nhập khoá.")
    return OpenAI(api_key=settings.get("OPENAI_API_KEY"))

def get_whisper_client(provider: str = None):
    # Read on every call: the user can switch providers from Settings without
    # restarting the app.
    provider = provider or settings.get("WHISPER_PROVIDER")
    if provider == "chunkformer":
        return None, settings.get("CHUNKFORMER_MODEL")
    if provider == "openai":
        return get_openai_client(), "whisper-1"
    if not settings.is_set("GROQ_API_KEY"):
        raise ConfigError("Chưa có GROQ_API_KEY. Mở Cài đặt (⚙) để nhập khoá.")
    client = OpenAI(api_key=settings.get("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1")
    return client, "whisper-large-v3"

def transcriber_mode():
    """Which engine Transcriber should use: the bundled model or a hosted API."""
    return "chunkformer" if settings.get("WHISPER_PROVIDER") == "chunkformer" else "api"


def _explain_summary_error(exc):
    """Turn a Gemini failure into something the user can act on.

    The raw SDK message is English and full of API jargon; what a user needs to
    know is which of the four fixable things went wrong.
    """
    msg = str(exc).lower()
    model = settings.get("GEMINI_MODEL")

    if "api key" in msg or "api_key_invalid" in msg or "unauthenticated" in msg:
        return "Mã khoá Gemini không hợp lệ. Mở Cài đặt (⚙) và dán lại khoá."
    if "permission" in msg or "403" in msg:
        return "Mã khoá Gemini không có quyền dùng model này. Thử tạo khoá mới tại aistudio.google.com/apikey."
    if "not found" in msg or "404" in msg:
        # Almost always the key rather than the name: Google closes older models
        # to newly created projects, so the same id keeps working for a colleague
        # whose key is a few months older.
        return (f"Khoá Gemini này không dùng được model '{model}' — Google chỉ cấp "
                f"model cũ cho khoá tạo từ trước. Mở Cài đặt (⚙) và chọn model khác "
                f"trong danh sách.")
    if "quota" in msg or "resource_exhausted" in msg or "429" in msg or "rate limit" in msg:
        return "Đã vượt hạn mức miễn phí của Gemini. Đợi vài phút rồi thử lại, hoặc đổi sang Flash Lite trong Cài đặt (⚙)."
    if "deadline" in msg or "timeout" in msg or "connection" in msg or "network" in msg or "resolve" in msg:
        return "Không kết nối được tới Gemini. Kiểm tra mạng internet rồi thử lại."
    if "safety" in msg or "blocked" in msg or "không trả về nội dung" in str(exc):
        return "Gemini từ chối trả lời nội dung này (bộ lọc an toàn)."
    return "Không tạo được biên bản. Bản ghi lời nói vẫn được lưu lại."

# ---------------------------------------------------------------- file output
# Plain .txt rather than .md: these get opened in Notepad and TextEdit, mailed
# and pasted into Word. Markdown is a format for something that renders it, and
# nothing in that chain does — it only showed up as literal asterisks.
_MD_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)"), r"\1"),
    (re.compile(r"`([^`]+?)`"), r"\1"),
]


def _to_plain_text(markdown: str) -> str:
    """Flatten the model's Markdown into text that reads correctly unrendered."""
    out = []
    for line in (markdown or "").split("\n"):
        stripped = line.strip()
        heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if heading:
            # A blank line before a heading, so sections stay visibly separate
            # once the '#' that used to mark them is gone.
            if out and out[-1].strip():
                out.append("")
            line = heading.group(1)
        else:
            line = re.sub(r"^(\s*)[*\-+]\s+", r"\1- ", line)
        for pattern, repl in _MD_INLINE:
            line = pattern.sub(repl, line)
        out.append(line.rstrip())
    return "\n".join(out).strip()


def _safe_filename(text: str, fallback: str) -> str:
    """A meeting title turned into something every filesystem accepts."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return (cleaned[:60].strip() or fallback)


def _write_text(path: str, body: str):
    """UTF-8 with a BOM and CRLF line endings.

    Both are concessions to Windows: without the BOM, Notepad on older builds
    and Excel read Vietnamese as mojibake, and without CRLF some Windows text
    viewers run the whole file onto one line. macOS ignores both.
    """
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(body)


# English suffixes on a Vietnamese name: the stem identifies the meeting, the
# suffix says which half of it this is, and it stays readable once the file has
# been downloaded, renamed by a mail client or opened on someone else's machine.
SUMMARY_SUFFIX = " - summary.txt"
TRANSCRIPT_SUFFIX = " - content.txt"


def _save_outputs(title: str, transcript: str, summary: str = None, folder: str = None):
    """Write one meeting into its own folder.

    Returns (folder_name, transcript_rel, summary_rel) — the file paths are
    relative to output_dir(), which is what the browser sends back to download
    them. A folder per meeting keeps the two halves of a meeting together; loose
    files went straight to being an undifferentiated pile after a few weeks.

    Pass `folder` to write into a meeting that already exists: the transcript is
    saved before the summary is attempted, and the second call must land beside
    the first rather than in a new folder a minute later.
    """
    root = output_dir()
    if not folder:
        stamp = datetime.now().strftime("%Y-%m-%d %H%M")
        folder = _safe_filename(f"{stamp} - {title}", stamp)
    meeting_dir = os.path.join(root, folder)
    os.makedirs(meeting_dir, exist_ok=True)

    # The files carry the full meeting name too, not just "summary.txt". Once
    # downloaded they sit in a folder full of other people's files, where a name
    # that only made sense inside its own folder identifies nothing.
    transcript_name = folder + TRANSCRIPT_SUFFIX
    summary_name = folder + SUMMARY_SUFFIX

    header = f"{title}\n{datetime.now().strftime('%d/%m/%Y %H:%M')}\n{'-' * 40}\n\n"

    transcript_rel = f"{folder}/{transcript_name}"
    try:
        _write_text(os.path.join(meeting_dir, transcript_name),
                    header + "NỘI DUNG GHI LẠI\n\n" + transcript)
    except OSError as e:
        print(f"Transcript save error: {e}")
        transcript_rel = None

    if summary is None:
        return folder, transcript_rel, None

    _write_text(os.path.join(meeting_dir, summary_name),
                header + "BIÊN BẢN\n\n" + _to_plain_text(summary))
    return folder, transcript_rel, f"{folder}/{summary_name}"


def _noise_reduce(samples, noise_floor=None):
    if samples is None or len(samples) == 0:
        return samples, noise_floor
    audio = samples.astype(np.float64)
    if noise_floor is None:
        bottom = np.sort(np.abs(audio))[:max(1, len(audio)//10)]
        noise_floor = float(np.mean(bottom)) * 2.0
    audio = np.where(np.abs(audio) > noise_floor * 0.3, audio, 0)
    return np.clip(audio, -32768, 32767).astype(np.int16), noise_floor

def _has_speech(audio_data, threshold=30):
    return np.sqrt(np.mean(audio_data.astype(np.float64) ** 2)) > threshold

def _tail_is_silent(audio_data, seconds=SILENCE_TAIL, threshold=25):
    """True when the buffer ends in quiet, i.e. the speaker just finished a
    sentence. Cutting there both puts the text on screen sooner and gives the
    recogniser a whole utterance instead of one sliced mid-word."""
    n = int(SAMPLE_RATE * seconds)
    if audio_data is None or len(audio_data) < n:
        return False
    tail = audio_data[-n:].astype(np.float64)
    return np.sqrt(np.mean(tail ** 2)) <= threshold


def _trim_silence(audio_data, threshold=15):
    if audio_data is None or len(audio_data) == 0:
        return audio_data
    abs_audio = np.abs(audio_data.astype(np.float64))
    above_threshold = np.where(abs_audio > threshold)[0]
    if len(above_threshold) == 0:
        return audio_data
    return audio_data[above_threshold[0]:above_threshold[-1] + 1]

def _save_wav(filepath, data):
    with wave.open(filepath, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(data.tobytes())

def _trim_overlap(new_text, prev_tail):
    if not prev_tail or not new_text:
        return new_text
    min_overlap = 10
    max_overlap = min(len(prev_tail), len(new_text))
    for i in range(max_overlap, min_overlap - 1, -1):
        if new_text[:i] == prev_tail[-i:]:
            return new_text[i:].strip()
    return new_text

async def check_topic(chunk, goals, client, model=None):
    """Off-topic detection. Fires roughly every 30s of meeting, so it is by far
    the heaviest consumer of the Gemini free tier's per-minute quota — keep it on
    a Flash model and never let a failure here interrupt recording."""
    if not goals.strip():
        return None

    model = model or settings.get("GEMINI_MODEL")
    prompt = f"""You are a meeting topic tracker. The meeting's main topic/goal is: {goals}

The latest thing someone said: "{chunk}"

Respond in JSON only:
{{"on_topic": true/false, "topic": "what they are talking about", "suggestion": "brief one-line suggestion if off-topic, empty string if on-topic"}}"""
    try:
        # The SDK call is synchronous; awaited on the event loop it would stop
        # the socket being read for as long as Gemini takes to answer.
        # summarizer.generate carries the retries for a retired model and for a
        # thinking option this model's generation does not accept.
        response, _ = await asyncio.to_thread(
            lambda: summarizer_mod.generate(
                client, model, prompt,
                response_mime_type="application/json",
                temperature=0,
                max_output_tokens=512,
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Topic check skipped: {type(e).__name__}: {e}")
        return None

@app.get("/favicon.ico")
async def favicon():
    """The window and any browser tab both ask for this on every load; without
    it the log fills with 404s and the tab shows a blank page icon."""
    path = os.path.join(resource_dir(), "static", "icon.png")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/health")
async def health():
    """Polled by the desktop launcher before it opens a browser window."""
    return {"status": "ok"}


@app.get("/api/settings")
async def read_settings():
    """Secrets come back masked — the browser never receives a usable key."""
    return settings.public_state()


# Names that answer generateContent but are no use for writing minutes.
_MODEL_NOISE = (
    "embedding", "image", "tts", "audio", "veo", "lyria", "nano-banana",
    "computer-use", "deep-research", "live", "vision", "aqa", "learnlm",
)


@app.get("/api/gemini-models")
async def gemini_models():
    """Which models this particular key can actually reach.

    Not a fixed list, because the answer differs per key: Google closes older
    models to newly created projects, so a name that works for one user 404s for
    the person next to them. Asking is the only way to be right, and it keeps a
    shipped build usable after the catalogue moves on.
    """
    if not settings.is_set("GEMINI_API_KEY"):
        return {"models": [], "error": "Chưa có Gemini API Key"}
    try:
        client = get_gemini_client()
        names = []
        for m in await asyncio.to_thread(lambda: list(client.models.list())):
            name = (getattr(m, "name", "") or "").replace("models/", "")
            actions = getattr(m, "supported_actions", None)
            if actions and "generateContent" not in actions:
                continue
            if not name or any(bad in name for bad in _MODEL_NOISE):
                continue
            if "flash" not in name and "pro" not in name:
                continue
            names.append(name)
    except Exception as e:
        return {"models": [], "error": str(e)}

    # Aliases first — they are the ones that keep working — then newest first,
    # which for Gemini's naming is plain reverse alphabetical.
    names.sort(key=lambda n: (0 if n.endswith("-latest") else 1, [-ord(c) for c in n]))
    return {"models": names}


@app.post("/api/settings")
async def write_settings(payload: dict):
    try:
        return settings.save(payload or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Không ghi được file cấu hình: {e}")


_WARMUP = None


def start_warmup():
    """Load the local model the moment the page is opened.

    It takes the best part of ten seconds. Paid when Record is pressed, it ate
    the opening of the meeting: nothing appeared on screen until it finished,
    and everything said meanwhile arrived as one late lump. Paid on page load,
    it happens while the user is still typing the meeting title.
    """
    global _WARMUP
    if _WARMUP is not None or transcriber_mode() != "chunkformer":
        return
    warm = Transcriber(None, model=settings.get("CHUNKFORMER_MODEL"), mode="chunkformer")

    def done(task):
        exc = task.exception()
        # Not fatal: the recording path loads the model itself if this failed.
        print(f"Speech model warmup failed: {exc}" if exc else "Speech model ready")

    _WARMUP = asyncio.create_task(asyncio.to_thread(warm.warmup))
    _WARMUP.add_done_callback(done)


# The three screens are real URLs now, not #fragments, so the browser asks the
# server for them on a refresh. Registered explicitly rather than as a catch-all
# so a typo still 404s instead of silently returning the app.
@app.get("/", response_class=HTMLResponse)
@app.get("/home", response_class=HTMLResponse)
@app.get("/recording", response_class=HTMLResponse)
@app.get("/result", response_class=HTMLResponse)
async def index():
    start_warmup()
    html_path = os.path.join(resource_dir(), "templates", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.post("/api/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form("vi"),
    meeting_title: str = Form(""),
):
    session_id = str(uuid.uuid4())[:8]
    temp_dir = tempfile.mkdtemp(prefix=f"meeting_web_{session_id}_")

    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty audio file")

        ext = os.path.splitext(file.filename or "audio.webm")[1] or ".webm"
        audio_path = os.path.join(temp_dir, f"recording{ext}")
        with open(audio_path, "wb") as f:
            f.write(content)

        whisper_client, whisper_model = get_whisper_client()
        gemini_client = get_gemini_client()

        mode = transcriber_mode()
        transcriber = Transcriber(whisper_client, language=language or None, prompt=None, model=whisper_model, mode=mode)
        summarizer = Summarizer(gemini_client, model=settings.get("GEMINI_MODEL"))

        text = transcriber.transcribe_file(audio_path)
        if not text.strip():
            raise HTTPException(status_code=400, detail="No speech detected in recording")

        summary = summarizer.summarize(text)
        title = meeting_title.strip() or f"Cuộc họp {datetime.now().strftime('%d-%m-%Y')}"

        _folder, transcript_filename, summary_filename = _save_outputs(title, text, summary)

        return {
            "session_id": session_id,
            "transcript": text,
            "summary": summary,
            "transcript_file": transcript_filename,
            "summary_file": summary_filename,
            "title": title,
        }
    except HTTPException:
        raise
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

async def _recognise(transcriber, samples, prev_tail):
    """Run one chunk through the recogniser off the event loop.

    Inference is seconds of blocking CPU work. Done inline it froze the whole
    server: the socket stopped being read, so audio arriving during inference
    piled up unread and every chunk reached the screen later than the last.
    """
    def work():
        tmp = tempfile.mkdtemp()
        try:
            audio_path = os.path.join(tmp, "chunk.wav")
            _save_wav(audio_path, samples)
            # Only the rolling context changes between chunks; the loaded model
            # is reused.
            transcriber.prompt = prev_tail or None
            return transcriber.transcribe_file(audio_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return await asyncio.to_thread(work)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    language = "vi"
    meeting_title = ""
    meeting_goals = ""
    buffer = np.array([], dtype=np.int16)
    prev_tail = ""
    all_transcripts = []
    settings_received = False
    last_topic_check = time.time()
    alive = True
    stop = asyncio.Event()

    # Resolve credentials before recording starts. Previously a missing key
    # raised out of the handler and the browser saw only an opaque disconnect.
    try:
        whisper_client, whisper_model = get_whisper_client()
        gemini_client = get_gemini_client()
    except ConfigError as e:
        await websocket.send_json({"type": "config_error", "text": str(e)})
        await websocket.close()
        return

    # One transcriber for the whole session. It caches the loaded model, and a
    # fresh instance per chunk reloaded hundreds of megabytes of weights every
    # flush — enough to fall behind a live meeting.
    transcriber = Transcriber(
        whisper_client, language=language, model=whisper_model,
        mode=transcriber_mode(),
    )

    async def send(payload):
        """Both loops write to the socket, and either may still be holding a
        result when the browser goes away. A failed send ends the session
        quietly rather than raising out of the middle of a recognition."""
        nonlocal alive
        if not alive:
            return
        try:
            await websocket.send_json(payload)
        except Exception:
            alive = False

    async def handle_chunk(samples):
        """Recognise one chunk and put the text on screen as soon as it exists."""
        global NOISE_FLOOR
        nonlocal prev_tail, last_topic_check

        cleaned, NOISE_FLOOR = _noise_reduce(samples, NOISE_FLOOR)
        if not _has_speech(cleaned):
            return
        trimmed = _trim_silence(cleaned)
        if len(trimmed) == 0:
            return

        # Say that something is being worked on before it takes seconds to
        # arrive, so a pause in the feed reads as progress, not as a freeze.
        await send({"type": "status", "state": "processing", "text": "Đang nhận dạng..."})
        try:
            text = await _recognise(transcriber, trimmed, prev_tail)
        except Exception as e:
            print(f"WS transcribe error: {e}")
            await send({"type": "error", "text": f"Lỗi nhận dạng: {str(e)[:100]}"})
            return

        text = _trim_overlap(text or "", prev_tail)
        if not text:
            await send({"type": "status", "state": "", "text": "Đang nghe..."})
            return

        all_transcripts.append(text)
        prev_tail = text[-50:]
        await send({"type": "transcript", "text": text, "count": len(all_transcripts)})

        if meeting_goals and time.time() - last_topic_check >= TOPIC_CHECK_INTERVAL:
            last_topic_check = time.time()
            context = ". ".join(all_transcripts[-3:])
            result = await check_topic(context, meeting_goals, gemini_client)
            if result and not result.get("on_topic", True):
                await send({
                    "type": "topic_warning",
                    "topic": result.get("topic", ""),
                    "suggestion": result.get("suggestion", ""),
                })

    async def receive_loop():
        """Does nothing but drain the socket into the buffer, so audio is never
        left unread while a chunk is being recognised."""
        nonlocal buffer, language, meeting_title, meeting_goals, settings_received

        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return

            if "text" in msg:
                text_data = msg["text"]
                if not settings_received:
                    # Not `settings`: that name is the config module, and binding
                    # it here made it local to the whole function — the summary
                    # step at the end then read GEMINI_MODEL off this dict and
                    # passed model=None to Gemini, so every recording ended in a
                    # summary error.
                    opts = json.loads(text_data)
                    language = opts.get("language", "vi")
                    meeting_title = opts.get("title", "")
                    meeting_goals = opts.get("goals", "")
                    # The transcriber was built before this message arrived.
                    transcriber.language = language
                    settings_received = True
                    continue
                if text_data == "DONE":
                    return

            elif "bytes" in msg:
                if not settings_received:
                    continue
                samples = np.frombuffer(msg["bytes"], dtype=np.int16)
                if len(samples):
                    buffer = np.append(buffer, samples)

    async def recogniser_loop():
        """Cuts the buffer at pauses and recognises each piece as it comes."""
        nonlocal buffer

        if transcriber.mode == "chunkformer":
            await send({"type": "status", "state": "processing", "text": "Đang nạp mô hình nhận dạng..."})
            try:
                await asyncio.to_thread(transcriber.warmup)
            except Exception as e:
                # Not fatal here: transcribe_file retries the load, and failing
                # the whole recording over a warmup would lose the meeting.
                print(f"WS warmup failed: {e}")
        await send({"type": "status", "state": "", "text": "Đang nghe..."})

        last_flush = time.time()
        while not stop.is_set():
            await asyncio.sleep(0.2)
            if len(buffer) < SAMPLE_RATE:
                continue
            elapsed = time.time() - last_flush
            ready = elapsed >= FLUSH_INTERVAL or (
                elapsed >= MIN_FLUSH_INTERVAL and _tail_is_silent(buffer)
            )
            if not ready:
                continue

            # Hand the chunk over and keep the tail as context for the next one.
            # Reassigning here means audio recorded during recognition lands in
            # the new buffer and is not lost.
            chunk, buffer = buffer, buffer[-OVERLAP_SAMPLES:].copy()
            # Timed from the cut, not from the end of recognition, so a slow
            # chunk does not push the next one further out as well.
            last_flush = time.time()
            await handle_chunk(chunk)

    recogniser = asyncio.create_task(recogniser_loop())
    try:
        await receive_loop()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WS error: {e}")
    finally:
        stop.set()

    try:
        # Lets whatever chunk is in flight finish and reach the browser.
        await recogniser
    except Exception as e:
        print(f"WS recogniser error: {e}")

    if len(buffer) >= SAMPLE_RATE:
        try:
            await handle_chunk(buffer)
        except Exception as e:
            print(f"WS final transcribe error: {e}")

    full_transcript = "\n".join(all_transcripts)
    if full_transcript.strip():
        title = meeting_title.strip() or f"Cuộc họp {datetime.now().strftime('%d-%m-%Y')}"

        # Written before the summary is attempted: an hour of recording must not
        # be lost because one API call failed.
        folder, tf, _ = _save_outputs(title, full_transcript)
        out_dir = os.path.join(output_dir(), folder)

        await send({"type": "status", "state": "processing", "text": "Đang viết biên bản..."})
        try:
            summarizer = Summarizer(gemini_client, model=settings.get("GEMINI_MODEL"))
            summary = await asyncio.to_thread(summarizer.summarize, full_transcript)
            folder, tf, sf = _save_outputs(title, full_transcript, summary, folder=folder)
            await send({
                "type": "summary", "transcript": full_transcript,
                "summary": summary, "transcript_file": tf, "summary_file": sf,
                # So the browser can tell the user where the files landed. It
                # cannot know: a packaged app writes to Documents, a source
                # checkout to ./meeting_outputs.
                "output_dir": out_dir, "folder": folder, "title": title,
            })
        except Exception as e:
            # Report the real reason to the browser. Swallowing it into the log
            # left users staring at a generic "no result" message with no idea
            # whether the key, the network or the model was at fault.
            detail = f"{type(e).__name__}: {e}"
            print(f"WS summary error: {detail}")
            await send({
                "type": "summary_error",
                "text": _explain_summary_error(e),
                "detail": detail[:300],
                "transcript": full_transcript,
                "transcript_file": tf,
                "output_dir": out_dir, "folder": folder, "title": title,
            })

    await send({"type": "done"})

@app.get("/api/download/{relpath:path}")
async def download_file(relpath: str):
    # A path now, not a bare name: each meeting lives in its own folder. It is
    # resolved against the output folder and rejected if it points outside.
    try:
        filepath = resolve_output_path(relpath)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    # text/plain, not octet-stream: the browser's Save dialog then offers a
    # sensible type, and the Save As picker can hand the bytes straight over.
    return FileResponse(filepath, filename=os.path.basename(filepath),
                        media_type="text/plain; charset=utf-8")


@app.post("/api/open-mic-settings")
async def open_mic_settings():
    """Open the OS privacy screen for the microphone.

    The app can grant itself the browser-level permission, but not the system
    one. When Windows or macOS has microphone access switched off, the only
    thing left to do is take the user to the exact screen that fixes it —
    telling them to "check their settings" is where most people give up.
    """
    try:
        if sys.platform == "win32":
            os.startfile("ms-settings:privacy-microphone")  # noqa: S606 - fixed URI
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
            ])
        else:
            raise RuntimeError("không hỗ trợ trên hệ điều hành này")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không mở được cài đặt: {e}")


@app.post("/api/open-in-browser")
async def open_in_browser(request: Request):
    """Reopen the running app in the user's default browser.

    The way out when the native window will not record. The browser asks for
    the microphone on its own terms and is the arrangement this app shipped on
    for months, so it is a working app rather than a support conversation —
    and the server is already up, so it is the same session, not a restart.
    """
    import webbrowser

    url = str(request.base_url)
    if not webbrowser.open(url):
        raise HTTPException(status_code=500, detail="Không mở được trình duyệt")
    return {"url": url}


@app.post("/api/open-output")
async def open_output(payload: dict = None):
    """Show the saved meetings in Explorer / Finder.

    The path alone is not enough for a packaged app: it lands under Documents in
    a folder the user never chose, and copying a path out of a web page into a
    file manager is not something most people will do.
    """
    try:
        return {"path": reveal_output_dir((payload or {}).get("folder"))}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không mở được thư mục: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
