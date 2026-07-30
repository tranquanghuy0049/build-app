"""Stage a PhoWhisper checkpoint into the source tree so PyInstaller can bundle it.

Run at build time. The shipped app then transcribes with no network access and no
first-use download.

Weights are saved as float16, which halves what goes into the .dmg. Inference
still runs in float32 — transformers upcasts on load, and float16 on MPS is
known to produce NaNs with Whisper.

  PHOWHISPER_BUNDLE_MODEL=vinai/PhoWhisper-small python packaging/fetch_model.py
  PHOWHISPER_BUNDLE_FP16=0   # keep full precision instead
"""
import os
import shutil
import sys

REPO = os.environ.get("PHOWHISPER_BUNDLE_MODEL", "vinai/PhoWhisper-small")
USE_FP16 = os.environ.get("PHOWHISPER_BUNDLE_FP16", "1") != "0"

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_ROOT = os.path.join(SRC_DIR, "models")


def dir_size_mb(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(root, name))
    return total / (1024 * 1024)


def main():
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    # Must match app_paths.bundled_model_dir()'s naming, or the app will not find
    # what we stage here and will silently fall back to downloading.
    dest = os.path.join(DEST_ROOT, REPO.replace("/", "__"))
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)

    print(f"Fetching {REPO}")
    processor = WhisperProcessor.from_pretrained(REPO, token=os.getenv("HF_TOKEN"))
    model = WhisperForConditionalGeneration.from_pretrained(REPO, token=os.getenv("HF_TOKEN"))

    if USE_FP16:
        print("Converting weights to float16")
        model = model.half()

    print(f"Saving to {dest}")
    processor.save_pretrained(dest)
    model.save_pretrained(dest, safe_serialization=True)

    # The pipeline resolves the feature extractor and tokenizer from this same
    # directory, so a missing file here becomes a runtime failure on a user's
    # machine rather than a build failure.
    required = ["config.json", "preprocessor_config.json", "tokenizer_config.json"]
    missing = [f for f in required if not os.path.exists(os.path.join(dest, f))]
    if missing:
        print(f"ERROR: staged model is incomplete, missing: {', '.join(missing)}")
        return 1

    weights = [f for f in os.listdir(dest) if f.endswith((".safetensors", ".bin"))]
    if not weights:
        print("ERROR: no weight file was written")
        return 1

    print(f"Staged {dir_size_mb(dest):.0f} MB  (weights: {', '.join(weights)})")
    print(f"torch {torch.__version__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
