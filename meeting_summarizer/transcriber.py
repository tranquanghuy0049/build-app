"""Speech to text, either through a hosted API or the bundled local model.

The local engine is ChunkFormer: a Conformer/CTC model that emits the whole
transcript in a single pass instead of generating it token by token. That makes
it markedly faster than a Whisper-style model and, unlike one, it cannot invent
words out of silence.

An instance caches the loaded model, so create one per recording session and
reuse it. Building a new Transcriber per audio chunk reloads several hundred
megabytes of weights from disk every time.
"""
import os
import threading
import time

# One loaded model per process, shared by every recording session. Loading takes
# the best part of ten seconds, and it used to be paid again on every press of
# Record — exactly the window in which the first lines of live text should be
# appearing on screen.
_ENGINE_CACHE = {}
_ENGINE_CACHE_LOCK = threading.Lock()
# Serialises inference on the shared model. Decoding runs an order of magnitude
# faster than real time, so making a second session queue costs far less than
# loading it a second copy of the weights.
_ENGINE_USE_LOCK = threading.Lock()


class Transcriber:
    VI_PROMPT = "Đây là cuộc họp tiếng Việt. Hãy viết chính xác những gì được nói, bao gồm dấu câu và dấu thanh đầy đủ. Không thêm bất kỳ câu chào kênh, quảng cáo hay lời đề nghị đăng ký nào."

    def __init__(self, client=None, language=None, prompt=None, model="whisper-1", mode="api"):
        self.client = client
        self.language = language
        self.prompt = prompt
        self.model = model
        self.mode = mode
        self._engine = None
        # Set once the GPU backend has proven unusable on this machine.
        self._force_cpu = False

    @staticmethod
    def _select_device():
        """Prefer Apple's Metal backend on Apple Silicon, then CUDA, then CPU."""
        import torch

        # PHOWHISPER_DEVICE is the older name, kept so configs written before
        # ChunkFormer existed keep working.
        override = (os.getenv("LOCAL_ASR_DEVICE")
                    or os.getenv("PHOWHISPER_DEVICE")
                    or "auto").lower()
        if override in ("cpu", "mps", "cuda"):
            return override
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _resolve_model_source(self):
        """Use weights shipped inside the app when the requested model is the one
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

    def warmup(self):
        """Load the local model now rather than on the first chunk.

        Loading takes as long as it takes to read several hundred megabytes off
        disk. Paid on the first chunk, it delayed the first line of live text by
        that much; paid at the start of a session, it overlaps the first few
        seconds of speech instead.
        """
        if self.mode == "chunkformer":
            self._get_chunkformer()

    def transcribe_file(self, filepath: str) -> str:
        if not filepath:
            return ""
        if self.mode == "chunkformer":
            return self._transcribe_chunkformer(filepath)
        return self._transcribe_api(filepath)

    # -------------------------------------------------------------- hosted API
    def _transcribe_api(self, filepath: str) -> str:
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

    # ------------------------------------------------------------ ChunkFormer
    def _get_chunkformer(self):
        if self._engine is not None:
            return self._engine

        # Held across the load so two sessions starting at once wait for one
        # copy of the weights instead of reading two.
        with _ENGINE_CACHE_LOCK:
            cached = _ENGINE_CACHE.get(self.model)
            if cached is not None:
                self._engine = cached
                return cached

            try:
                from app_paths import hf_cache_dir
                os.environ.setdefault("HF_HOME", hf_cache_dir())
            except Exception:
                pass
            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

            from chunkformer import ChunkFormerModel

            source = self._resolve_model_source()
            device = "cpu" if self._force_cpu else self._select_device()

            def build(dev):
                print(f"  Loading ChunkFormer {self.model} on device={dev}")
                return ChunkFormerModel.from_pretrained(source).to(dev)

            try:
                self._engine = build(device)
            except Exception as e:
                if device == "cpu":
                    raise
                # Metal can be reported as available yet fail to allocate — that
                # is exactly what happens on virtualised Macs. CPU is slower but
                # always works, and a slow transcript beats none.
                print(f"  {device} failed ({type(e).__name__}: {e}); retrying on CPU")
                self._force_cpu = True
                self._engine = build("cpu")

            _ENGINE_CACHE[self.model] = self._engine
        return self._engine

    @staticmethod
    def _flatten_chunkformer_output(out):
        """endless_decode's return shape varies with version and with
        return_timestamps, so accept a string, a dict, or a list of segments
        rather than assuming one of them."""
        if out is None:
            return ""
        if isinstance(out, str):
            return out.strip()
        if isinstance(out, dict):
            return str(out.get("text") or out.get("transcript") or "").strip()
        if isinstance(out, (list, tuple)):
            parts = []
            for item in out:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("transcript") or ""))
            return " ".join(p.strip() for p in parts if p and p.strip()).strip()
        return str(out).strip()

    def _transcribe_chunkformer(self, filepath: str) -> str:
        for attempt in range(3):
            try:
                model = self._get_chunkformer()
                with _ENGINE_USE_LOCK:
                    out = model.endless_decode(
                        audio_path=filepath,
                        chunk_size=64,
                        left_context_size=128,
                        right_context_size=128,
                        # Caps how much audio is batched at once. Our clips are
                        # seconds long, so this only matters for uploaded files.
                        total_batch_duration=1800,
                        return_timestamps=False,
                    )
                return self._flatten_chunkformer_output(out)
            except Exception as e:
                print(f"  ChunkFormer attempt {attempt + 1} failed: {e}")
                # The backend can fail mid-inference rather than at load time.
                # Drop to CPU and rebuild, or all three attempts fail alike.
                if not self._force_cpu:
                    print("  Rebuilding ChunkFormer on CPU for the retry")
                    self._force_cpu = True
                    self._engine = None
                    # Evict the broken engine, or every other session keeps
                    # being handed the copy that just failed.
                    with _ENGINE_CACHE_LOCK:
                        _ENGINE_CACHE.pop(self.model, None)
                if attempt < 2:
                    time.sleep(2)
        return ""
