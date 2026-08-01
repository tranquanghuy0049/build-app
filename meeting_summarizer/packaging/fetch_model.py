"""Stage a speech model into the source tree so PyInstaller can bundle it.

Run at build time. The shipped app then transcribes with no network access and no
first-use download.

Two engines are supported. ChunkFormer is the default and is copied verbatim
because its checkpoint is a WeNet-style bundle whose layout we should not
rewrite. PhoWhisper is a transformers checkpoint, so its weights are re-saved as
float16 to halve what goes into the .dmg; inference still runs in float32
because float16 on MPS produces NaNs with Whisper.

Either way, config and tokenizer files come straight from the Hub rather than
from save_pretrained, whose output has changed between library versions.

  BUNDLE_ENGINE=chunkformer BUNDLE_ASR_MODEL=khanhld/chunkformer-ctc-large-vie \
      python packaging/fetch_model.py
"""
import os
import shutil
import sys

ENGINE = os.environ.get("BUNDLE_ENGINE", "chunkformer").lower()
DEFAULT_MODELS = {
    "chunkformer": "khanhld/chunkformer-ctc-large-vie",
    "phowhisper": "vinai/PhoWhisper-small",
}
REPO = os.environ.get("BUNDLE_ASR_MODEL") or DEFAULT_MODELS.get(ENGINE, "")
USE_FP16 = os.environ.get("PHOWHISPER_BUNDLE_FP16", "1") != "0"

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_ROOT = os.path.join(SRC_DIR, "models")

# Files a checkpoint needs besides its weights.
CONFIG_PATTERNS = ["*.json", "*.txt", "*.yaml", "*.yml", "*.model", "*.jinja"]
WHISPER_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".h5", ".msgpack", ".ckpt")


def dir_size_mb(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(root, name))
    return total / (1024 * 1024)


def dest_for(repo):
    # Must match app_paths.bundled_model_dir()'s naming, or the app will not
    # find what we stage here and will silently fall back to downloading.
    return os.path.join(DEST_ROOT, repo.replace("/", "__"))


def strip_hub_cache(dest):
    """snapshot_download leaves bookkeeping behind that would be bundled."""
    cache = os.path.join(dest, ".cache")
    if os.path.isdir(cache):
        shutil.rmtree(cache)


def make_test_wav(path, seconds=2, sample_rate=16000):
    """A quiet tone. We are checking that decoding executes, not what it says."""
    import math
    import struct
    import wave

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(seconds * sample_rate):
            value = int(3000 * math.sin(2 * math.pi * 220 * i / sample_rate))
            frames += struct.pack("<h", value)
        wf.writeframes(bytes(frames))


def stage_chunkformer():
    from huggingface_hub import snapshot_download

    dest = dest_for(REPO)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)

    print(f"==> Downloading {REPO} verbatim")
    snapshot_download(REPO, local_dir=dest, token=os.getenv("HF_TOKEN") or None)
    strip_hub_cache(dest)

    staged = sorted(f for f in os.listdir(dest) if not f.startswith("."))
    print(f"    {len(staged)} files: {', '.join(staged)}")

    print("==> Verifying the staged model loads and decodes")
    import tempfile

    from chunkformer import ChunkFormerModel

    model = ChunkFormerModel.from_pretrained(dest).to("cpu")
    tmp = tempfile.mkdtemp()
    wav = os.path.join(tmp, "probe.wav")
    make_test_wav(wav)
    out = model.endless_decode(
        audio_path=wav,
        chunk_size=64,
        left_context_size=128,
        right_context_size=128,
        total_batch_duration=1800,
        return_timestamps=False,
    )
    print(f"    decode returned {type(out).__name__}: {str(out)[:120]!r}")
    return dest


def stage_phowhisper():
    import torch
    from huggingface_hub import snapshot_download
    from transformers import WhisperForConditionalGeneration, pipeline

    dest = dest_for(REPO)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)

    token = os.getenv("HF_TOKEN") or None
    print(f"==> Copying config and tokenizer files from {REPO}")
    snapshot_download(REPO, local_dir=dest, allow_patterns=CONFIG_PATTERNS, token=token)
    strip_hub_cache(dest)

    staged = sorted(f for f in os.listdir(dest) if not f.startswith("."))
    print(f"    {len(staged)} files: {', '.join(staged)}")
    if "preprocessor_config.json" not in staged:
        raise RuntimeError("preprocessor_config.json is absent from the repo snapshot")

    print("==> Loading weights")
    model = WhisperForConditionalGeneration.from_pretrained(REPO, token=token)
    if USE_FP16:
        print("    converting to float16")
        model = model.half()
    model.save_pretrained(dest, safe_serialization=True)

    kept = []
    for name in os.listdir(dest):
        path = os.path.join(dest, name)
        if not os.path.isfile(path) or not name.endswith(WHISPER_WEIGHT_SUFFIXES):
            continue
        if name.endswith(".safetensors"):
            kept.append(name)
        else:
            print(f"    removing duplicate weights: {name}")
            os.remove(path)
    if not kept:
        raise RuntimeError("no safetensors weight file was written")

    print("==> Verifying the staged model loads and runs")
    import numpy as np

    # Forced to CPU: the macOS CI runner reports mps as available but cannot
    # allocate on it. This step checks the staged files, not the backend.
    pipe = pipeline("automatic-speech-recognition", model=dest,
                    torch_dtype=torch.float32, device="cpu")
    result = pipe(np.zeros(16000, dtype=np.float32))
    print(f"    pipeline ran, returned {result.get('text', '')!r}")
    print(f"    torch {torch.__version__}")
    return dest


def main():
    if ENGINE not in DEFAULT_MODELS:
        print(f"ERROR: unknown BUNDLE_ENGINE '{ENGINE}'")
        return 1
    if not REPO:
        print("ERROR: no model to stage")
        return 1

    print(f"==> Staging {REPO} for engine '{ENGINE}'")
    try:
        dest = stage_chunkformer() if ENGINE == "chunkformer" else stage_phowhisper()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"ERROR: staging failed: {type(e).__name__}: {e}")
        return 1

    print(f"==> Staged {dir_size_mb(dest):.0f} MB to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
