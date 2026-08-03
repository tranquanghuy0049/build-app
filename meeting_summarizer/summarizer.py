"""Meeting minutes generation via the Gemini API."""

SUMMARY_SYSTEM = "Bạn là thư ký cuộc họp, viết biên bản bằng tiếng Việt."

# Chỉ dặn "viết chi tiết" là không đủ. Bản trước yêu cầu tối thiểu 500-1000 từ
# và cấm tóm tắt ngắn, nên với một transcript hai câu nó nở ra thành hai nghìn
# từ: đầy chỗ trống "[Vui lòng điền...]" và những chi tiết kỹ thuật không ai nói
# ra. Ba quy tắc dưới đây — không bịa, không để chỗ trống, độ dài theo nội dung
# thực có — là để chặn đúng ba kiểu nở đó.
SUMMARY_PROMPT = """Viết biên bản cuộc họp bằng tiếng Việt từ transcript dưới đây.

VỀ TRANSCRIPT: được tạo tự động từ giọng nói nên KHÔNG CÓ DẤU CÂU, không viết
hoa, và có chỗ nhận dạng sai. Hãy tự suy ra ranh giới câu và ý, tự sửa những từ
rõ ràng bị nhận dạng nhầm dựa vào ngữ cảnh, rồi viết bằng câu văn hoàn chỉnh có
dấu câu đầy đủ.

QUY TẮC BẮT BUỘC:

1. CHỈ VIẾT NHỮNG GÌ TRANSCRIPT THỰC SỰ CÓ. Không suy diễn, không thêm lợi ích,
   ví dụ, chi tiết kỹ thuật hay đánh giá mà người nói không nói ra. Thà biên bản
   ngắn còn hơn có một câu người ta không hề nói.

2. KHÔNG ĐỂ CHỖ TRỐNG. Tuyệt đối không viết "[Vui lòng điền...]", "[Tên người
   trình bày]" hay bất kỳ dạng placeholder nào. Thông tin nào transcript không
   có — thời gian, địa điểm, người tham dự, kế hoạch tuần sau — thì BỎ HẲN mục
   đó, đừng nêu tiêu đề rồi để trống.

3. ĐỘ DÀI THEO NỘI DUNG THỰC CÓ. Transcript này dài {word_count} từ; biên bản
   không được dài quá {max_words} từ. Transcript ngắn thì biên bản vài câu là
   đủ. Không kéo dài cho đủ số chữ.

4. Chỉ nêu mục kết luận hoặc việc cần làm NẾU transcript có nói tới. Không tự
   đề xuất thay người họp.

5. Giữ đúng trình tự và ý của người nói. Nếu phân biệt được nhiều người nói,
   ghi rõ ai nói gì.

6. KHÔNG DỰNG KHUNG TIÊU ĐỀ KHI KHÔNG CÓ GÌ ĐỂ DỰNG. Cuộc họp chỉ có một vài ý
   thì viết thẳng thành câu văn liền mạch — không cần tiêu đề "BIÊN BẢN CUỘC
   HỌP", không cần mục "Nội dung", "Kết luận". Chỉ chia thành mục có tiêu đề khi
   transcript thực sự có nhiều chủ đề tách bạch.

Transcript cuộc họp:

{transcript}

Viết biên bản bằng tiếng Việt."""


# Google's alias for whatever Flash is current. Used as the second attempt when
# the configured model is gone, because losing the minutes of a meeting that has
# already happened is a far worse outcome than quietly using a different model
# and saying so in the log.
FALLBACK_MODEL = "gemini-flash-latest"

# The two generations disagree about how to say "do not spend tokens thinking",
# and each rejects the other's spelling with a 400: Gemini 2.5 takes
# thinking_budget=0, Gemini 3 takes thinking_level and refuses a zero budget.
# The aliases hide which family answers — gemini-flash-latest resolves to 3.6
# today and will not tomorrow — so the style is discovered from the refusal and
# remembered, rather than guessed from a name that is designed to change.
_THINKING_STYLES = ("level", "budget", "none")
_thinking_style = {}


def _thinking(types, style):
    if style == "level":
        return types.ThinkingConfig(thinking_level="low")
    if style == "budget":
        return types.ThinkingConfig(thinking_budget=0)
    return None


def _styles_for(model):
    if model in _thinking_style:
        return (_thinking_style[model],)
    # A version in the name is a strong hint; an alias is not. Only saves a
    # round trip on the first call either way.
    if any(old in model for old in ("1.5", "2.0", "2.5")):
        return ("budget", "level", "none")
    return _THINKING_STYLES


def generate(client, model, contents, **config_kwargs):
    """generate_content, retried around the two ways this call goes stale.

    A model id that Google has closed to this key answers 404; a thinking option
    the model's generation does not recognise answers 400. Both are recoverable
    without the user doing anything, and both are invisible from a build machine.

    Returns (response, model_actually_used).
    """
    from google.genai import types

    last = None
    models = (model,) if model == FALLBACK_MODEL else (model, FALLBACK_MODEL)
    for name in models:
        for style in _styles_for(name):
            config = types.GenerateContentConfig(
                thinking_config=_thinking(types, style), **config_kwargs
            )
            try:
                response = client.models.generate_content(
                    model=name, contents=contents, config=config
                )
                if _thinking_style.get(name) != style:
                    _thinking_style[name] = style
                    print(f"gemini: {name} dùng thinking={style}")
                if name != model:
                    print(f"gemini: model {model!r} không dùng được, đã chuyển sang {name!r}")
                return response, name
            except Exception as e:
                message = str(e)
                last = e
                if "404" in message or "NOT_FOUND" in message:
                    break                      # nothing to do with the config
                if "400" in message or "INVALID_ARGUMENT" in message:
                    continue                   # wrong knob for this generation
                raise
    raise last


class Summarizer:
    def __init__(self, client, model=FALLBACK_MODEL):
        self.client = client
        self.model = model

    def summarize(self, transcript: str) -> str:
        if not transcript.strip():
            return "No transcript available to summarize."

        # A concrete ceiling, not just "viết vừa phải". Doubling leaves room to
        # restore the punctuation and sentence structure the ASR strips out,
        # while still binding hard on the short transcripts that were the whole
        # problem. The floor keeps a one-line transcript from being squeezed
        # into something unreadable.
        word_count = len(transcript.split())
        max_words = max(120, word_count * 2)

        # Thinking is held down by generate(): those tokens come out of
        # max_output_tokens, and writing minutes needs no reasoning budget —
        # leaving it on can eat the whole allowance and return empty text.
        response, self.model = generate(
            self.client,
            self.model,
            SUMMARY_PROMPT.format(
                transcript=transcript,
                word_count=word_count,
                max_words=max_words,
            ),
            system_instruction=SUMMARY_SYSTEM,
            # Was 0.7, which invited the model to embellish. Minutes should be a
            # faithful record, not a creative one.
            temperature=0.3,
            max_output_tokens=8192,
        )

        text = (response.text or "").strip()
        if not text:
            raise RuntimeError(
                f"Gemini không trả về nội dung (có thể bị bộ lọc an toàn chặn). "
                f"Lý do: {_finish_reason(response)}"
            )
        return text


def _finish_reason(response):
    try:
        return response.candidates[0].finish_reason
    except (AttributeError, IndexError, TypeError):
        return "không rõ"
