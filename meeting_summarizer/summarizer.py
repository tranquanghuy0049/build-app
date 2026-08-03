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


class Summarizer:
    def __init__(self, client, model="gemini-2.5-flash"):
        self.client = client
        self.model = model

    def summarize(self, transcript: str) -> str:
        if not transcript.strip():
            return "No transcript available to summarize."

        from google.genai import types

        # A concrete ceiling, not just "viết vừa phải". Doubling leaves room to
        # restore the punctuation and sentence structure the ASR strips out,
        # while still binding hard on the short transcripts that were the whole
        # problem. The floor keeps a one-line transcript from being squeezed
        # into something unreadable.
        word_count = len(transcript.split())
        max_words = max(120, word_count * 2)

        response = self.client.models.generate_content(
            model=self.model,
            contents=SUMMARY_PROMPT.format(
                transcript=transcript,
                word_count=word_count,
                max_words=max_words,
            ),
            config=types.GenerateContentConfig(
                system_instruction=SUMMARY_SYSTEM,
                # Was 0.7, which invited the model to embellish. Minutes should
                # be a faithful record, not a creative one.
                temperature=0.3,
                max_output_tokens=8192,
                # Gemini 2.5 Flash thinks by default and those tokens are drawn
                # from max_output_tokens. Writing minutes needs no reasoning
                # budget, and leaving it on can consume the whole allowance and
                # return empty text.
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
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
