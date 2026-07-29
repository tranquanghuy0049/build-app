from openai import OpenAI

SUMMARY_PROMPT = """Bạn là một chuyên gia viết biên bản cuộc họp. Nhiệm vụ của bạn là viết LẠI toàn bộ nội dung cuộc họp một cách CHI TIẾT, ĐẦY ĐỦ dựa trên transcript dưới đây.

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
    def __init__(self, client: OpenAI, model="gpt-4o-mini"):
        self.client = client
        self.model = model

    def summarize(self, transcript: str) -> str:
        if not transcript.strip():
            return "No transcript available to summarize."

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "Bạn là chuyên gia viết biên bản cuộc họp chi tiết bằng tiếng Việt."},
                {"role": "user", "content": SUMMARY_PROMPT.format(transcript=transcript)},
            ],
            temperature=0.7,
            max_tokens=4096,
        )

        return response.choices[0].message.content.strip()