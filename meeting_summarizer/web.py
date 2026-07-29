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
from dotenv import load_dotenv
from openai import OpenAI

from app_paths import env_file, output_dir, resource_dir

# A windowed .app bundle has no console, so sys.stdout/stderr are None there.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

load_dotenv(env_file())

from transcriber import Transcriber
from summarizer import Summarizer

SAMPLE_RATE = 16000
OVERLAP_SAMPLES = SAMPLE_RATE * 2
FLUSH_INTERVAL = 10
NOISE_FLOOR = None

WHISPER_PROVIDER = os.getenv("WHISPER_PROVIDER", "openai")
PHOWHISPER_MODEL = os.getenv("PHOWHISPER_MODEL", "vinai/PhoWhisper-small")

app = FastAPI(title="Meeting Summarizer")

def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "sk-your-api-key-here":
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured in .env")
    return OpenAI(api_key=api_key)

def get_whisper_client(provider: str = WHISPER_PROVIDER):
    if provider == "phowhisper":
        return None, PHOWHISPER_MODEL
    if provider == "openai":
        return get_openai_client(), "whisper-1"
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not configured in .env")
    client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
    return client, "whisper-large-v3"

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

async def check_topic(chunk, goals, client, model="gpt-4o-mini"):
    if not goals.strip():
        return None
    prompt = f"""You are a meeting topic tracker. The meeting's main topic/goal is: {goals}

The latest thing someone said: "{chunk}"

Respond in JSON only:
{{"on_topic": true/false, "topic": "what they are talking about", "suggestion": "brief one-line suggestion if off-topic, empty string if on-topic"}}"""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except:
        return None

@app.get("/api/health")
async def health():
    """Polled by the desktop launcher before it opens a browser window."""
    return {"status": "ok"}


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
        gpt_client = get_openai_client()

        mode = "local" if WHISPER_PROVIDER == "phowhisper" else "api"
        transcriber = Transcriber(whisper_client, language=language or None, prompt=None, model=whisper_model, mode=mode)
        summarizer = Summarizer(gpt_client, model="gpt-4o-mini")

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
    whisper_client, whisper_model = get_whisper_client()
    gpt_client = get_openai_client()

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
                                mode = "local" if WHISPER_PROVIDER == "phowhisper" else "api"
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
                                            result = await check_topic(context, meeting_goals, gpt_client)
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
                    mode = "local" if WHISPER_PROVIDER == "phowhisper" else "api"
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
        try:
            summarizer = Summarizer(gpt_client, model="gpt-4o-mini")
            summary = summarizer.summarize(full_transcript)
            title = meeting_title.strip() or f"Meeting_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            out_dir = output_dir()
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = str(uuid.uuid4())[:8]
            tf = f"transcript_{timestamp_str}_{session_id}.md"
            sf = f"summary_{timestamp_str}_{session_id}.md"
            with open(os.path.join(out_dir, tf), "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n## Transcript\n\n{full_transcript}")
            with open(os.path.join(out_dir, sf), "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n## Summary\n\n{summary}")
            await websocket.send_json({
                "type": "summary", "transcript": full_transcript,
                "summary": summary, "transcript_file": tf, "summary_file": sf,
            })
        except Exception as e:
            print(f"WS summary error: {e}")

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
