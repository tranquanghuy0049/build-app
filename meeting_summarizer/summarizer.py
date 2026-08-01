"""Meeting minutes generation via the Gemini API."""

SUMMARY_SYSTEM = "Bạn là chuyên gia viết biên bản cuộc họp chi tiết bằng tiếng Việt."

SUMMARY_PROMPT = """Bạn là một chuyên gia viết biên bản cuộc họp. Nhiệm vụ của bạn là viết LẠI toàn bộ nội dung cuộc họp một cách CHI TIẾT, ĐẦY ĐỦ dựa trên transcript dưới đây.

LƯU Ý VỀ TRANSCRIPT: transcript được tạo tự động từ giọng nói nên có thể
KHÔNG CÓ DẤU CÂU, không viết hoa, và có chỗ nhận dạng sai. Hãy tự suy ra
ranh giới câu và ý, tự sửa những từ rõ ràng bị nhận dạng nhầm dựa vào ngữ
cảnh, rồi viết biên bản bằng câu văn hoàn chỉnh có dấu câu đầy đủ.

YÊU CẦU BẮT BUỘC:
- Viết HOÀN TOÀN bằng tiếng Việt
- GHI LẠI CHI TIẾT toàn bộ nội dung cuộc họp, KHÔNG tóm tắt ngắn gọn
- Trình bày theo đúng diễn biến cuộc họp, giữ nguyên trình tự thời gian các luận điểm
- Ghi rõ các chủ đề đã thảo luận, các luồng ý kiến, các lập luận được đưa ra
- Nếu có thể, ghi rõ ai đã nói gì, quan điểm của từng người
- Đề xuất/kết luận/action items cần được viết rõ ràng, chi tiết

Transcript cuộc họp:

{transcript}

Hãy viết biên bản chi tiết bằng tiếng Việt, dài ít nhất 500-1000 từ, tái hiện đầy đủ nội dung cuộc họp."""


class Summarizer:
    def __init__(self, client, model="gemini-2.5-flash"):
        self.client = client
        self.model = model

    def summarize(self, transcript: str) -> str:
        if not transcript.strip():
            return "No transcript available to summarize."

        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model,
            contents=SUMMARY_PROMPT.format(transcript=transcript),
            config=types.GenerateContentConfig(
                system_instruction=SUMMARY_SYSTEM,
                temperature=0.7,
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
