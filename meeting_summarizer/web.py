import os
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
from app_paths import output_dir, resource_dir

# A windowed .app bundle has no console, so sys.stdout/stderr are None there.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

settings.load()

from transcriber import Transcriber
from summarizer import Summarizer

SAMPLE_RATE = 16000
OVERLAP_SAMPLES = SAMPLE_RATE * 2
FLUSH_INTERVAL = 10
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
    if provider == "phowhisper":
        return None, settings.get("PHOWHISPER_MODEL")
    if provider == "openai":
        return get_openai_client(), "whisper-1"
    if not settings.is_set("GROQ_API_KEY"):
        raise ConfigError("Chưa có GROQ_API_KEY. Mở Cài đặt (⚙) để nhập khoá.")
    client = OpenAI(api_key=settings.get("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1")
    return client, "whisper-large-v3"

def transcriber_mode():
    """Which engine Transcriber should use: a local model or a hosted API."""
    provider = settings.get("WHISPER_PROVIDER")
    if provider == "chunkformer":
        return "chunkformer"
    if provider == "phowhisper":
        return "local"
    return "api"


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
        return f"Không tìm thấy model '{model}'. Mở Cài đặt (⚙) và chọn model khác."
    if "quota" in msg or "resource_exhausted" in msg or "429" in msg or "rate limit" in msg:
        return "Đã vượt hạn mức miễn phí của Gemini. Đợi vài phút rồi thử lại, hoặc đổi sang Flash Lite trong Cài đặt (⚙)."
    if "deadline" in msg or "timeout" in msg or "connection" in msg or "network" in msg or "resolve" in msg:
        return "Không kết nối được tới Gemini. Kiểm tra mạng internet rồi thử lại."
    if "safety" in msg or "blocked" in msg or "không trả về nội dung" in str(exc):
        return "Gemini từ chối trả lời nội dung này (bộ lọc an toàn)."
    return "Không tạo được biên bản. Bản ghi lời nói vẫn được lưu lại."

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
        from google.genai import types
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
                max_output_tokens=512,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Topic check skipped: {type(e).__name__}: {e}")
        return None

@app.get("/api/health")
async def health():
    """Polled by the desktop launcher before it opens a browser window."""
    return {"status": "ok"}


@app.get("/api/settings")
async def read_settings():
    """Secrets come back masked — the browser never receives a usable key."""
    return settings.public_state()


@app.post("/api/settings")
async def write_settings(payload: dict):
    try:
        return settings.save(payload or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Không ghi được file cấu hình: {e}")


@app.get("/", response_class=HTMLResponse)
async def index():
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
        title = meeting_title.strip() or f"Meeting_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        out_dir = output_dir()
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        transcript_filename = f"transcript_{timestamp_str}_{session_id}.md"
        summary_filename = f"summary_{timestamp_str}_{session_id}.md"

        with open(os.path.join(out_dir, transcript_filename), "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n## Transcript\n\n{text}")
        with open(os.path.join(out_dir, summary_filename), "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n## Summary\n\n{summary}")

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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global NOISE_FLOOR
    await websocket.accept()

    language = "vi"
    meeting_title = ""
    meeting_goals = ""
    buffer = np.array([], dtype=np.int16)
    last_flush = time.time()
    prev_tail = ""
    all_transcripts = []
    settings_received = False
    topic_check_count = 0

    # Resolve credentials before recording starts. Previously a missing key
    # raised out of the handler and the browser saw only an opaque disconnect.
    try:
        whisper_client, whisper_model = get_whisper_client()
        gemini_client = get_gemini_client()
    except ConfigError as e:
        await websocket.send_json({"type": "config_error", "text": str(e)})
        await websocket.close()
        return

    try:
        while True:
            msg = await websocket.receive()

            if "text" in msg:
                text_data = msg["text"]
                if not settings_received:
                    settings = json.loads(text_data)
                    language = settings.get("language", "vi")
                    meeting_title = settings.get("title", "")
                    meeting_goals = settings.get("goals", "")
                    settings_received = True
                    continue
                if text_data == "DONE":
                    break

            elif "bytes" in msg:
                if not settings_received:
                    continue
                samples = np.frombuffer(msg["bytes"], dtype=np.int16)
                if len(samples) == 0:
                    continue

                buffer = np.append(buffer, samples)
                elapsed = time.time() - last_flush

                if elapsed >= FLUSH_INTERVAL and len(buffer) >= SAMPLE_RATE:
                    cleaned_buffer, NOISE_FLOOR = _noise_reduce(buffer, NOISE_FLOOR)
                    if _has_speech(cleaned_buffer):
                        trimmed = _trim_silence(cleaned_buffer)
                        if len(trimmed) > 0:
                            tmp = tempfile.mkdtemp()
                            audio_path = os.path.join(tmp, "chunk.wav")
                            _save_wav(audio_path, trimmed)
                            try:
                                mode = transcriber_mode()
                                transcriber = Transcriber(
                                    whisper_client, language=language,
                                    prompt=prev_tail or None, model=whisper_model,
                                    mode=mode,
                                )
                                text = transcriber.transcribe_file(audio_path)
                                if text:
                                    text = _trim_overlap(text, prev_tail)
                                    if text:
                                        all_transcripts.append(text)
                                        await websocket.send_json({"type": "transcript", "text": text})
                                        prev_tail = text[-50:]

                                        topic_check_count += 1
                                        if meeting_goals and topic_check_count % 3 == 0:
                                            context = ". ".join(all_transcripts[-3:])
                                            result = await check_topic(context, meeting_goals, gemini_client)
                                            if result and not result.get("on_topic", True):
                                                await websocket.send_json({
                                                    "type": "topic_warning",
                                                    "topic": result.get("topic", ""),
                                                    "suggestion": result.get("suggestion", ""),
                                                })
                            except Exception as e:
                                print(f"WS transcribe error: {e}")
                                try:
                                    await websocket.send_json({"type": "error", "text": f"Lỗi nhận dạng: {str(e)[:100]}"})
                                except:
                                    pass
                            finally:
                                shutil.rmtree(tmp, ignore_errors=True)

                    if len(buffer) >= OVERLAP_SAMPLES:
                        buffer = buffer[-OVERLAP_SAMPLES:].copy()
                    last_flush = time.time()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WS error: {e}")

    if len(buffer) >= SAMPLE_RATE:
        cleaned_buffer, _ = _noise_reduce(buffer, NOISE_FLOOR)
        if _has_speech(cleaned_buffer):
            trimmed = _trim_silence(cleaned_buffer)
            if len(trimmed) > 0:
                tmp = tempfile.mkdtemp()
                audio_path = os.path.join(tmp, "final.wav")
                _save_wav(audio_path, trimmed)
                try:
                    mode = transcriber_mode()
                    transcriber = Transcriber(
                        whisper_client, language=language,
                        prompt=prev_tail or None, model=whisper_model,
                        mode=mode,
                    )
                    text = transcriber.transcribe_file(audio_path)
                    if text:
                        text = _trim_overlap(text, prev_tail)
                        if text:
                            all_transcripts.append(text)
                            await websocket.send_json({"type": "transcript", "text": text})
                except Exception as e:
                    print(f"WS final transcribe error: {e}")
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)

    full_transcript = "\n".join(all_transcripts)
    if full_transcript.strip():
        title = meeting_title.strip() or f"Meeting_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        out_dir = output_dir()
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_id = str(uuid.uuid4())[:8]
        tf = f"transcript_{timestamp_str}_{session_id}.md"
        sf = f"summary_{timestamp_str}_{session_id}.md"

        # Save the transcript before attempting the summary. An hour of
        # recording must not be lost because one API call failed.
        try:
            with open(os.path.join(out_dir, tf), "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n## Transcript\n\n{full_transcript}")
        except OSError as e:
            print(f"WS transcript save error: {e}")
            tf = None

        try:
            summarizer = Summarizer(gemini_client, model=settings.get("GEMINI_MODEL"))
            summary = summarizer.summarize(full_transcript)
            with open(os.path.join(out_dir, sf), "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n## Summary\n\n{summary}")
            await websocket.send_json({
                "type": "summary", "transcript": full_transcript,
                "summary": summary, "transcript_file": tf, "summary_file": sf,
            })
        except Exception as e:
            # Report the real reason to the browser. Swallowing it into the log
            # left users staring at a generic "no result" message with no idea
            # whether the key, the network or the model was at fault.
            detail = f"{type(e).__name__}: {e}"
            print(f"WS summary error: {detail}")
            await websocket.send_json({
                "type": "summary_error",
                "text": _explain_summary_error(e),
                "detail": detail[:300],
                "transcript": full_transcript,
                "transcript_file": tf,
            })

    await websocket.send_json({"type": "done"})

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    safe_name = os.path.basename(filename)
    filepath = os.path.join(output_dir(), safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(filepath, filename=safe_name, media_type="application/octet-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
