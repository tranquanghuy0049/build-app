import sounddevice as sd
import numpy as np
import wave
import threading
import time
import os
import tempfile
from collections import deque

class AudioRecorder:
    SAMPLE_RATE = 16000
    CHUNK_DURATION = 30
    CHANNELS = 1
    DTYPE = 'int16'
    OVERLAP_SECONDS = 2

    def __init__(self, device=None):
        self.device = device
        self.recording = False
        self.audio_thread = None
        self.chunks = []
        self.chunk_queue = deque()
        self.temp_dir = tempfile.mkdtemp(prefix="meeting_")
        self.overlap_samples = int(self.SAMPLE_RATE * self.OVERLAP_SECONDS)
        self.overlap_buffer = np.array([], dtype=self.DTYPE)
        self.deferred_buffer = np.array([], dtype=self.DTYPE)

    def list_devices(self):
        devices = sd.query_devices()
        print("=== Audio Devices ===")
        for i, dev in enumerate(devices):
            print(f"  [{i}] {dev['name']} - {dev['max_input_channels']} in, {dev['max_output_channels']} out")
        print()
        default = sd.query_devices(kind='input')
        print(f"Default input device: [{default['index']}] {default['name']}")
        return devices

    def _record_loop(self):
        GAIN = 2.0

        def audio_callback(indata, frames, time_info, status):
            if status:
                print(f"Audio status: {status}")
            amplified = np.clip(indata * GAIN, -32768, 32767).astype(self.DTYPE)
            self.chunk_queue.append(amplified)

        try:
            with sd.InputStream(
                device=self.device,
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                callback=audio_callback,
            ):
                while self.recording:
                    sd.sleep(100)
        except Exception as e:
            msg = f"Audio error: {e}"
            print(f"\n{'='*50}")
            print(f"ERROR: {msg}")
            print("Try: run with --list-devices to see available microphones")
            print("Then: run with --device INDEX to select the right one")
            print(f"{'='*50}\n")
            self.recording = False

    def start(self):
        self.recording = True
        self.chunks = []
        self.chunk_queue.clear()
        self.overlap_buffer = np.array([], dtype=self.DTYPE)
        self.deferred_buffer = np.array([], dtype=self.DTYPE)

        self.audio_thread = threading.Thread(target=self._record_loop, daemon=True)
        self.audio_thread.start()
        print(f"Recording started (sample rate: {self.SAMPLE_RATE} Hz)")
        print("Processing audio chunks...")

    def stop(self):
        self.recording = False
        if self.audio_thread:
            self.audio_thread.join(timeout=5)

        all_audio = []
        while self.chunk_queue:
            all_audio.append(self.chunk_queue.popleft())

        if len(self.deferred_buffer) > 0:
            all_audio.insert(0, self.deferred_buffer)

        if all_audio:
            combined = np.concatenate(all_audio, axis=0)
            filepath = os.path.join(self.temp_dir, "full_recording.wav")
            self._save_wav(filepath, combined)
            print(f"Full recording saved to: {filepath}")
            return filepath
        return None

    def get_ready_chunks(self):
        chunks = []
        while self.chunk_queue:
            chunks.append(self.chunk_queue.popleft())
        return chunks

    def _has_speech(self, audio_data, threshold=30):
        rms = np.sqrt(np.mean(audio_data.astype(np.float64) ** 2))
        return rms > threshold

    def _trim_silence(self, audio_data, threshold=15):
        if audio_data is None or len(audio_data) == 0:
            return audio_data
        abs_audio = np.abs(audio_data.astype(np.float64))
        above_threshold = np.where(abs_audio > threshold)[0]
        if len(above_threshold) == 0:
            return audio_data
        return audio_data[above_threshold[0]:above_threshold[-1] + 1]

    def flush_chunks_to_file(self):
        chunks = self.get_ready_chunks()
        if not chunks and len(self.deferred_buffer) == 0:
            return None

        if len(chunks) > 0:
            combined = np.concatenate(chunks, axis=0)
        else:
            combined = np.array([], dtype=self.DTYPE)

        if len(self.deferred_buffer) > 0:
            combined = np.concatenate([self.deferred_buffer, combined], axis=0)
            self.deferred_buffer = np.array([], dtype=self.DTYPE)

        if not self._has_speech(combined):
            max_defer_seconds = 10
            if len(combined) < self.SAMPLE_RATE * max_defer_seconds:
                self.deferred_buffer = combined
                print("  (low energy, waiting for more audio)")
                return None

        trimmed = self._trim_silence(combined)

        if len(self.overlap_buffer) > 0:
            combined_with_overlap = np.concatenate([self.overlap_buffer, trimmed], axis=0)
        else:
            combined_with_overlap = trimmed

        if len(combined) >= self.overlap_samples:
            self.overlap_buffer = combined[-self.overlap_samples:].copy()
        else:
            self.overlap_buffer = combined.copy()

        timestamp = int(time.time())
        filepath = os.path.join(self.temp_dir, f"chunk_{timestamp}.wav")
        self._save_wav(filepath, combined_with_overlap)
        return filepath

    def _save_wav(self, filepath, data):
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(data.tobytes())

    def cleanup(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)