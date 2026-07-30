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
        # Set once the GPU backend has proven unusable on this machine.
        self._force_cpu = False

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

    def _resolve_model_source(self):
        """Use weights shipped inside the app when the requested size is the one
        that was bundled, so the common path needs no network at all."""
        try:
            from app_paths import bundled_model_dir
            local = bundled_model_dir(self.model)
        except Exception:
            local = None
        if local:
            print(f"  Using bundled weights: {local}")
            return local
        print(f"  {self.model} is not bundled; downloading on first use")
        return self.model

    def _get_pipeline(self):
        if self._pipe is not None:
            return self._pipe

        # Must be set before transformers is imported, or a download lands in
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

        source = self._resolve_model_source()
        device = "cpu" if self._force_cpu else self._select_device()

        def build(dev):
            print(f"  Loading {self.model} on device={dev}")
            return pipeline(
                "automatic-speech-recognition",
                model=source,
                token=os.getenv("HF_TOKEN"),
                device=dev,
                # Bundled weights are stored as float16 to halve the app size,
                # but inference runs in float32: float16 on MPS produces NaNs
                # with Whisper on several torch releases.
                torch_dtype=torch.float32,
            )

        try:
            self._pipe = build(device)
        except Exception as e:
            if device == "cpu":
                raise
            # Metal can be reported as available yet fail to allocate — that is
            # exactly what happens on virtualised Macs. CPU is slower but always
            # works, and a slow transcript beats none.
            print(f"  {device} failed ({type(e).__name__}: {e}); retrying on CPU")
            self._force_cpu = True
            self._pipe = build("cpu")
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
                # The GPU backend can also fail mid-inference rather than at load
                # time. Drop to CPU and rebuild before retrying, otherwise all
                # three attempts fail identically.
                if not self._force_cpu:
                    print("  Rebuilding the pipeline on CPU for the retry")
                    self._force_cpu = True
                    self._pipe = None
                if attempt < 2:
                    time.sleep(2)
        return ""
