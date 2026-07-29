import os
import sys
import time
import threading
import subprocess
import platform
from collections import deque
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI
from colorama import init, Fore, Style

from recorder import AudioRecorder
from transcriber import Transcriber
from summarizer import Summarizer

sys.stdout.reconfigure(encoding='utf-8')
init(autoreset=True)

if getattr(sys, 'frozen', False):
    base_dir = sys._MEIPASS
    os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(base_dir, '.env'))


def _beep():
    try:
        if platform.system() == "Windows":
            import ctypes
            ctypes.windll.kernel32.Beep(880, 250)
        else:
            print('\a', end='', flush=True)
    except Exception:
        pass


def _show_notification(title, message, is_error=False):
    try:
        system = platform.system()
        if system == "Windows":
            import ctypes
            flags = 0x10 | 0x1000 if is_error else 0x40 | 0x1000
            ctypes.windll.user32.MessageBoxW(0, message, title, flags)
        elif system == "Darwin":
            icon = "stop" if is_error else "note"
            subprocess.run([
                "osascript", "-e",
                f'display dialog "{message}" with title "{title}" buttons {{"OK"}} default button "OK" with icon {icon}'
            ], capture_output=True, timeout=10)
    except Exception:
        pass

BANNER = f"""
{Fore.CYAN}----------------------------------------
   Meeting Summarizer Tool v1.0
   Real-time transcription + Summary
----------------------------------------{Style.RESET_ALL}
"""


WHISPER_PROVIDERS = {"openai", "groq"}

def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "sk-your-api-key-here":
        print(f"{Fore.RED}Error: OPENAI_API_KEY not set or invalid.{Style.RESET_ALL}")
        print(f"Create a .env file based on .env.example with your OpenAI API key.")
        print(f"Get your key at: https://platform.openai.com/api-keys")
        sys.exit(1)
    return OpenAI(api_key=api_key)

def get_whisper_client(provider: str):
    if provider == "openai":
        return get_openai_client(), "whisper-1"

    key = os.getenv("GROQ_API_KEY")
    if not key or key == "gsk-your-groq-key-here":
        print(f"{Fore.RED}Error: GROQ_API_KEY not set or invalid.{Style.RESET_ALL}")
        print(f"Get your free key at: https://console.groq.com")
        sys.exit(1)
    client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
    return client, "whisper-large-v3"


HALLU_PATTERNS = [
    "hãy subscribe cho kênh",
    "hãy đăng ký kênh",
    "cảm ơn các bạn đã theo dõi",
    "các bạn nhớ đăng ký kênh",
    "để không bỏ lỡ",
    "hãy đăng ký để",
    "hẹn gặp lại",
    "các bạn có thể tìm",
    "các bạn có thể tham khảo",
    "ghền mì gõ",
    "lalaschool",
    "họp tiếng việt",
    "bạn có thể nhìn thấy",
    "bài hát của tôi",
    "trong phần bình luận",
    "cắt bàn tay",
    "bằng bàn tay để cắt",
    "bakit ever",
    "tẩy tẩy tẩy",
    "nhà hàng tập",
    "thấy gì kìa",
    "bằng bằng ý",
    "người sống ở đây",
]

def _is_hallucination(text):
    text_lower = text.lower().strip()
    for pattern in HALLU_PATTERNS:
        if pattern in text_lower:
            return True
    return False

def _trim_overlap(new_text, prev_tail):
    if not prev_tail or not new_text:
        return new_text
    min_overlap = 10
    max_overlap = min(len(prev_tail), len(new_text))
    for i in range(max_overlap, min_overlap - 1, -1):
        if new_text[:i] == prev_tail[-i:]:
            return new_text[i:].strip()
    return new_text

def run_transcription_loop(recorder, transcriber, transcript_lock, all_transcripts, stop_event):
    last_flush = time.time()
    flush_interval = 20
    prev_tail = ""
    recent_texts = deque(maxlen=6)

    while not stop_event.is_set():
        time.sleep(1)
        elapsed = time.time() - last_flush

        if elapsed >= flush_interval:
            filepath = recorder.flush_chunks_to_file()
            if filepath:
                print(f"{Fore.YELLOW}[Transcribing chunk...]{Style.RESET_ALL}", end=" ", flush=True)
                text = transcriber.transcribe_file(filepath)
                if text:
                    text = _trim_overlap(text, prev_tail)
                    if text and not _is_hallucination(text):
                        text_key = text[:80].lower()
                        dup_count = sum(1 for t in recent_texts if t == text_key)
                        recent_texts.append(text_key)
                        if dup_count < 2:
                            timestamp = datetime.now().strftime("%H:%M:%S")
                            with transcript_lock:
                                all_transcripts.append(f"[{timestamp}] {text}")
                            print(f"{Fore.GREEN}{text}{Style.RESET_ALL}")
                            prev_tail = text[-50:]
                else:
                    print(f"{Fore.YELLOW}(silence / no speech detected){Style.RESET_ALL}")
                try:
                    os.remove(filepath)
                except OSError:
                    pass
            last_flush = time.time()


def print_usage():
    print(f"\n{Fore.CYAN}Usage:{Style.RESET_ALL}")
    print(f"  python main.py [options]")
    print(f"\n{Fore.CYAN}Options:{Style.RESET_ALL}")
    print(f"  --language LANG      Language hint (e.g., vi, en, ja)")
    print(f"  --model MODEL        GPT model for summary (default: gpt-4o-mini)")
    print(f"  --whisper-provider   Whisper backend: openai or groq (default: openai)")
    print(f"  --device INDEX       Audio input device index (use --list-devices to see)")
    print(f"  --list-devices       List available audio input devices")
    print(f"\n{Fore.CYAN}Examples:{Style.RESET_ALL}")
    print(f"  python main.py")
    print(f"  python main.py --language vi")
    print(f"  python main.py --language vi --whisper-provider groq")
    print(f"  python main.py --language vi --model gpt-4o --whisper-provider groq")
    print(f"  python main.py --list-devices")


def main():
    print(BANNER)

    language = None
    model = "gpt-4o-mini"
    whisper_provider = "openai"
    list_devices = False
    device = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--language":
            i += 1
            if i < len(args):
                language = args[i]
            else:
                print("Error: --language requires a value")
                print_usage()
                return
        elif args[i] == "--model":
            i += 1
            if i < len(args):
                model = args[i]
            else:
                print("Error: --model requires a value")
                print_usage()
                return
        elif args[i] == "--whisper-provider":
            i += 1
            if i < len(args):
                whisper_provider = args[i]
                if whisper_provider not in WHISPER_PROVIDERS:
                    print(f"Error: --whisper-provider must be one of: {', '.join(sorted(WHISPER_PROVIDERS))}")
                    print_usage()
                    return
            else:
                print("Error: --whisper-provider requires a value")
                print_usage()
                return
        elif args[i] == "--device":
            i += 1
            if i < len(args):
                try:
                    device = int(args[i])
                except ValueError:
                    print("Error: --device requires a numeric device index")
                    print_usage()
                    return
            else:
                print("Error: --device requires a value")
                print_usage()
                return
        elif args[i] == "--list-devices":
            list_devices = True
        elif args[i] in ("-h", "--help"):
            print_usage()
            return
        else:
            print(f"Unknown argument: {args[i]}")
            print_usage()
            return
        i += 1

    if list_devices:
        # Create a temp recorder just to list devices
        temp_recorder = AudioRecorder(device=0)
        temp_recorder.list_devices()
        return

    # Auto-detect best input device
    if device is None:
        import sounddevice as sd
        try:
            devices = sd.query_devices()
            input_devices = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
            if input_devices:
                default_input = sd.query_devices(kind='input')
                device = default_input['index']
                print(f"{Fore.CYAN}[DEVICE] Auto-selected: [{device}] {default_input['name']}{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.YELLOW}[DEVICE] Could not auto-detect: {e}, using default{Style.RESET_ALL}")

    recorder = AudioRecorder(device=device)

    gpt_client = get_openai_client()
    whisper_client, whisper_model = get_whisper_client(whisper_provider)

    transcriber = Transcriber(whisper_client, language=language, prompt=None, model=whisper_model)
    summarizer = Summarizer(gpt_client, model=model)

    lang_str = f" ({language})" if language else ""
    print(f"{Fore.WHITE}Summary model: {Fore.YELLOW}{model}{Style.RESET_ALL}")
    print(f"{Fore.WHITE}Whisper provider: {Fore.YELLOW}{whisper_provider} ({whisper_model}){Style.RESET_ALL}")
    print(f"{Fore.WHITE}Language: {Fore.YELLOW}{language or 'auto'}{lang_str}{Style.RESET_ALL}")
    print(f"\n{Fore.GREEN}Press Ctrl+C to stop the meeting and generate summary.{Style.RESET_ALL}")
    print(f"{Fore.GREEN}Starting in 3 seconds...{Style.RESET_ALL}")
    _beep()
    _show_notification(
        "Meeting Summarizer",
        "Recording started!\n\n"
        "Press Ctrl+C in the console window to stop\n"
        "and generate the summary automatically."
    )
    time.sleep(3)

    transcript_lock = threading.Lock()
    all_transcripts = []
    stop_event = threading.Event()

    recorder.start()

    transcription_thread = threading.Thread(
        target=run_transcription_loop,
        args=(recorder, transcriber, transcript_lock, all_transcripts, stop_event),
        daemon=True,
    )
    transcription_thread.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n\n{Fore.CYAN}[STOP] Meeting stopped. Processing final audio...{Style.RESET_ALL}")

    stop_event.set()
    final_filepath = recorder.stop()

    if final_filepath:
        print(f"{Fore.YELLOW}[Transcribing final audio...]{Style.RESET_ALL}", flush=True)
        text = transcriber.transcribe_file(final_filepath)
        if text:
            timestamp = datetime.now().strftime("%H:%M:%S")
            with transcript_lock:
                all_transcripts.append(f"[{timestamp}] {text}")
            print(f"{Fore.GREEN}{text}{Style.RESET_ALL}")
        try:
            os.remove(final_filepath)
        except OSError:
            pass

    with transcript_lock:
        full_transcript = "\n".join(all_transcripts)

    if not full_transcript.strip():
        print(f"{Fore.RED}No speech was transcribed during the meeting.{Style.RESET_ALL}")
        recorder.cleanup()
        return

    print(f"\n{Fore.CYAN}========================================{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[TRANSCRIPT] Full Transcript{Style.RESET_ALL}")
    print(f"{Fore.CYAN}========================================{Style.RESET_ALL}")
    print(full_transcript)

    print(f"\n{Fore.CYAN}========================================{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[SUMMARY] Generating Summary...{Style.RESET_ALL}")
    print(f"{Fore.CYAN}========================================{Style.RESET_ALL}")

    try:
        summary = summarizer.summarize(full_transcript)
    except Exception as e:
        print(f"{Fore.RED}Error generating summary: {e}{Style.RESET_ALL}")
        recorder.cleanup()
        return

    print(f"\n{Fore.CYAN}========================================{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[RESULT] Meeting Summary{Style.RESET_ALL}")
    print(f"{Fore.CYAN}========================================{Style.RESET_ALL}")
    print(f"\n{Fore.WHITE}{summary}{Style.RESET_ALL}")
    print(f"\n{Fore.CYAN}========================================{Style.RESET_ALL}")

    output_dir = os.path.join(os.getcwd(), "meeting_outputs")
    os.makedirs(output_dir, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    transcript_path = os.path.join(output_dir, f"transcript_{timestamp_str}.txt")
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(full_transcript)

    summary_path = os.path.join(output_dir, f"summary_{timestamp_str}.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)

    print(f"\n{Fore.GREEN}[OK] Files saved:{Style.RESET_ALL}")
    print(f"   Transcript: {Fore.YELLOW}{transcript_path}{Style.RESET_ALL}")
    print(f"   Summary:    {Fore.YELLOW}{summary_path}{Style.RESET_ALL}")

    _beep()
    _beep()
    _show_notification(
        "Meeting Summarizer - Done",
        f"Summary saved!\n\n"
        f"Transcript: {transcript_path}\n"
        f"Summary:    {summary_path}\n\n"
        f"You can close this window now."
    )

    recorder.cleanup()


if __name__ == "__main__":
    main()