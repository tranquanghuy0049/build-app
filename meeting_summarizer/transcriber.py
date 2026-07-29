from openai import OpenAI
import time
import os
import numpy as np
import wave

class Transcriber:
    VI_PROMPT = "Đây là cuộc họp tiếng Việt. Hãy viết chính xác những gì được nói, bao gồm dấu câu và dấu thanh đầy đủ. Không thêm bất kỳ câu chào kênh, quảng cáo hay lời đề nghị đăng ký nào."

    def __init__(self, client=None, language=None, prompt=None, model="whisper-1", mode="api"):
        self.client = client
        self.language = language
        self.prompt = prompt
        self.model = model
        self.mode = mode
        self._pipe = None

    @staticmethod
    def _select_device():
        """Prefer Apple's Metal backend on Apple Silicon, then CUDA, then CPU."""
        import torch

        override = os.getenv("PHOWHISPER_DEVICE", "auto").lower()
        if override in ("cpu", "mps", "cuda"):
            return override
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _get_pipeline(self):
        if self._pipe is not None:
            return self._pipe

        # Must be set before transformers is imported, or the download lands in
        # ~/.cache and is lost when the app's support directory is cleared.
        try:
            from app_paths import hf_cache_dir
            os.environ.setdefault("HF_HOME", hf_cache_dir())
        except Exception:
            pass
        # Whisper hits a few ops MPS lacks; without this the pipeline raises
        # instead of falling back to CPU for those ops.
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

        import torch
        from transformers import pipeline

        device = self._select_device()
        print(f"  Loading {self.model} on device={device} (first run downloads the model)")
        self._pipe = pipeline(
            "automatic-speech-recognition",
            model=self.model,
            token=os.getenv("HF_TOKEN"),
            device=device,
            # float16 on MPS produces NaNs with Whisper on several torch releases.
            torch_dtype=torch.float32,
        )
        return self._pipe

    def transcribe_file(self, filepath: str) -> str:
        if not filepath:
            return ""

        if self.mode == "local":
            return self._transcribe_local(filepath)

        for attempt in range(3):
            try:
                with open(filepath, "rb") as audio_file:
                    kwargs = {
                        "model": self.model,
                        "file": audio_file,
                        "response_format": "text",
                        "temperature": 0,
                    }
                    if self.language:
                        kwargs["language"] = self.language
                    prompt_parts = [p for p in [self.VI_PROMPT, self.prompt] if p]
                    kwargs["prompt"] = " ".join(prompt_parts)

                    result = self.client.audio.transcriptions.create(**kwargs)
                    return result.strip()

            except Exception as e:
                print(f"  Transcription attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    time.sleep(2)
        return ""

    @staticmethod
    def _resample(audio, orig_sr, target_sr=16000):
        try:
            import soxr
            return soxr.resample(audio, orig_sr, target_sr)
        except ImportError:
            import librosa
            return librosa.resample(audio, orig_sr=orig_sr, target_sr=target_sr)

    def _transcribe_local(self, filepath: str) -> str:
        import soundfile as sf
        for attempt in range(3):
            try:
                pipe = self._get_pipeline()
                audio, sr = sf.read(filepath, dtype="float32")
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                if sr != 16000:
                    audio = self._resample(audio, sr)
                result = pipe(
                    audio,
                    # Whisper only sees 30s at a time; without chunking anything
                    # longer is silently truncated.
                    chunk_length_s=30,
                    stride_length_s=5,
                    generate_kwargs={"task": "transcribe", "language": "vi"},
                )
                return result["text"].strip()
            except Exception as e:
                print(f"  Local transcription attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    time.sleep(2)
        return ""
