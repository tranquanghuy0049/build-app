"""Stage the speech model into the source tree so PyInstaller can bundle it.

Run at build time. The shipped app then transcribes with no network access and no
first-use download.

The checkpoint is copied verbatim: it is a WeNet-style bundle whose layout we
should not rewrite, and regenerating files through a library's save_pretrained is
what broke an earlier build when that library changed which files it emits.

  BUNDLE_ASR_MODEL=khanhld/chunkformer-ctc-large-vie python packaging/fetch_model.py
"""
import os
import shutil
import sys
import tempfile

REPO = os.environ.get("BUNDLE_ASR_MODEL", "khanhld/chunkformer-ctc-large-vie")

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_ROOT = os.path.join(SRC_DIR, "models")


def dir_size_mb(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(root, name))
    return total / (1024 * 1024)


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
            frames += struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / sample_rate)))
        wf.writeframes(bytes(frames))


def main():
    from huggingface_hub import snapshot_download

    # Must match app_paths.bundled_model_dir()'s naming, or the app will not
    # find what we stage here and will silently fall back to downloading.
    dest = os.path.join(DEST_ROOT, REPO.replace("/", "__"))
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)

    print(f"==> Downloading {REPO} verbatim")
    try:
        snapshot_download(REPO, local_dir=dest, token=os.getenv("HF_TOKEN") or None)
    except Exception as e:
        print(f"ERROR: could not download {REPO}: {type(e).__name__}: {e}")
        return 1

    # snapshot_download leaves bookkeeping behind that would be bundled.
    cache = os.path.join(dest, ".cache")
    if os.path.isdir(cache):
        shutil.rmtree(cache)

    staged = sorted(f for f in os.listdir(dest) if not f.startswith("."))
    if not staged:
        print("ERROR: nothing was staged")
        return 1
    print(f"    {len(staged)} files: {', '.join(staged)}")

    # Load exactly what will ship and decode with it. A missing config file or a
    # broken checkpoint fails the build instead of a user's Mac.
    print("==> Verifying the staged model loads and decodes")
    try:
        from chunkformer import ChunkFormerModel

        # Forced to CPU: the macOS CI runner reports mps as available but cannot
        # allocate on it. This step checks the staged files, not the backend.
        model = ChunkFormerModel.from_pretrained(dest).to("cpu")
        wav = os.path.join(tempfile.mkdtemp(), "probe.wav")
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
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"ERROR: staged model is not usable: {type(e).__name__}: {e}")
        return 1

    print(f"==> Staged {dir_size_mb(dest):.0f} MB to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
