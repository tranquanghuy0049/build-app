# Build & deploy bản macOS

Bản macOS được build trên GitHub Actions (runner macOS thật), vì PyInstaller
không cross-compile — không có cách nào tạo `.app` từ máy Windows.

Sản phẩm: `MeetingSummarizer-<version>-arm64.dmg` — chạy trên Mac Apple Silicon
(M1/M2/M3/M4), tức mọi máy Mac bán ra từ cuối 2020.

Bản Intel (`-x86_64.dmg`) **không còn build được trên GitHub**: GitHub đã ngừng
runner macOS Intel, job Intel nằm chờ hết 24 giờ rồi kéo cả run thành *failed*
dù bản arm64 đã xong sau 11 phút. Nếu vẫn cần bản Intel, chạy
`bash packaging/build_macos.sh` ngay trên một máy Mac Intel — script vẫn giữ
nhánh x86_64 (ghim `torch==2.2.2`).

---

## 1. Đưa code lên GitHub

Repo đã có sẵn (`origin`), nên chỉ cần đẩy thay đổi:

```powershell
cd c:\Project\record_meeting-main
git add -A
git commit -m "..."
git push origin main
```

Kiểm tra trước khi push: `git status` **không được** liệt kê
`meeting_summarizer/.env`, `meeting_summarizer.zip` (file zip có chứa `.env` với
API key thật), thư mục `Meeting Summarizer/` hay `models/` — tất cả đã nằm trong
`.gitignore`. Riêng hai thứ sau nặng ~700 MB mỗi thứ và GitHub sẽ từ chối.

## 2. Chạy build

GitHub → tab **Actions** → **Build macOS app** → **Run workflow**, nhập version
(vd `1.0.0`).

Hoặc tự động khi push tag:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

Push tag sẽ build **và** tự tạo GitHub Release đính kèm file `.dmg`.

Mỗi build mất khoảng **10–20 phút** trên runner Apple Silicon.

> ⚠️ **Chi phí runner**: runner macOS tính **10×** phút so với Linux. Repo
> **private** với free tier 2000 phút/tháng chỉ đủ khoảng **3 build**/tháng.
> Repo **public** thì miễn phí không giới hạn. Nếu phải để private và build
> nhiều, cân nhắc self-hosted runner hoặc thuê Mac cloud.

## 3. Tải kết quả

Vào trang run vừa chạy → mục **Artifacts** ở cuối → tải
`MeetingSummarizer-macos-arm64`.

Kiểm tra máy đích trước khi gửi cho người dùng: trên Mac vào  → About This Mac.
Ghi *Apple M1/M2/M3/M4* → dùng được. Ghi *Intel* → phải build riêng trên máy đó
(xem đầu tài liệu).

---

## 4. Cài trên Macbook

Mở `.dmg`, kéo `MeetingSummarizer.app` vào `Applications`.

**Lần đầu mở app sẽ bị macOS chặn.** App được ký ad-hoc nhưng chưa notarize
(cần tài khoản Apple Developer $99/năm), nên Gatekeeper sẽ báo *"Apple could not
verify … is free of malware"*.

Cách mở:

- **macOS 15 (Sequoia) trở lên**: mở app một lần (bị chặn) → **System Settings →
  Privacy & Security** → kéo xuống cuối → bấm **Open Anyway**.
- **macOS 14 trở xuống**: chuột phải vào app → **Open** → **Open** trong hộp thoại.
- **Hoặc bằng Terminal** (nhanh nhất, làm một lần):

  ```bash
  xattr -dr com.apple.quarantine /Applications/MeetingSummarizer.app
  ```

Muốn bỏ hẳn bước này cho người dùng cuối thì phải notarize — xem mục *Notarize*
bên dưới.

## 5. Cấu hình lần đầu

App khởi động được ngay cả khi chưa có API key. Người dùng nhập khoá **ngay
trong giao diện**, không cần mở file nào:

1. Mở app → tự mở trình duyệt.
2. Có banner vàng "Chưa cấu hình xong" và nút **⚙** ở góc phải sáng màu cam.
3. Bấm ⚙ → nhập **Gemini API Key** → **Lưu**.

Chỉ cần **một khoá Gemini duy nhất** (lấy miễn phí tại
[aistudio.google.com/apikey](https://aistudio.google.com/apikey)). Nhận dạng
giọng nói chạy offline bằng PhoWhisper đã đóng gói sẵn trong app nên không cần
khoá nào cho khâu đó.

Đổi khoá hoặc đổi nhà cung cấp nhận dạng giọng nói sau này cũng vào cùng chỗ đó.
Thay đổi có hiệu lực ngay, **không cần khởi động lại app**.

Bấm nút ghi âm khi chưa cấu hình xong sẽ tự mở hộp thoại Cài đặt thay vì để
người dùng nói xong mới báo lỗi.

Khoá được lưu vào `~/Library/Application Support/MeetingSummarizer/.env`
(quyền `600`) và chỉ nằm trên máy đó. Giao diện không bao giờ nhận lại khoá
nguyên văn — chỉ hiển thị dạng che `sk-••••••••WXYZ` để biết đã lưu khoá nào.

Vì micro do **trình duyệt** thu chứ không phải app, quyền micro sẽ do Chrome/
Safari hỏi, không cần cấp quyền ở tầng hệ điều hành.

### Vị trí file

| Nội dung | Đường dẫn |
|---|---|
| Cấu hình (ghi bởi màn hình Cài đặt) | `~/Library/Application Support/MeetingSummarizer/.env` |
| Log (debug khi app không mở được) | `~/Library/Application Support/MeetingSummarizer/launcher.log` |
| Model PhoWhisper đã tải | `~/Library/Application Support/MeetingSummarizer/hf_cache/` |
| Transcript & biên bản | `~/Documents/MeetingSummarizer/` |

### Thoát app

Cửa sổ trình duyệt đóng lại không làm server dừng. Chuột phải icon trên Dock →
**Quit**.

---

## 6. ChunkFormer (transcribe offline, đóng gói sẵn)

Mặc định là `WHISPER_PROVIDER=chunkformer`, model
`khanhld/chunkformer-ctc-large-vie` **nằm sẵn trong app** — cài xong là nhận dạng
được ngay, không tải gì, không cần mạng.

Vì sao chọn nó thay PhoWhisper:

| | ChunkFormer | PhoWhisper-large |
|---|---|---|
| Tham số | 110M | 1.55B |
| WER trung bình | **8.31** | 8.85 |
| Kiến trúc | Conformer/CTC | encoder-decoder |

Ba lợi thế thực tế: ít hơn nửa số tham số so với PhoWhisper-small mà chính xác
hơn; CTC chạy một lượt thay vì sinh từng token nên nhanh hơn nhiều; và **không
bịa chữ khi im lặng** — bệnh cố hữu của model autoregressive mà chính bài verify
trong CI từng bắt được ở PhoWhisper.

Đánh đổi: **ChunkFormer không xuất dấu câu**, đầu ra là chữ thường liền mạch.
[summarizer.py](summarizer.py) đã dặn Gemini tự khôi phục câu chữ khi viết biên
bản.

PhoWhisper đã được **gỡ bỏ hoàn toàn** — cùng với `transformers`, thứ chiếm vài
trăm MB kiến trúc model không dùng đến trong bundle.

Giấy phép trọng số ChunkFormer là **cc-by-nc-4.0 — cấm dùng thương mại**.

Cách đóng gói: [packaging/fetch_model.py](packaging/fetch_model.py) tải nguyên
repo lúc build vào `models/khanhld__chunkformer-ctc-large-vie`, spec đưa thư mục
đó vào bundle, rồi [transcriber.py](transcriber.py) ưu tiên đường dẫn cục bộ.

Đổi model đóng gói khi build:

```bash
BUNDLE_ASR_MODEL=khanhld/chunkformer-rnnt-large-vie bash packaging/build_macos.sh
```

Lưu ý:

- Trong ⚙ chọn được model khác, nhưng **chỉ bản được đóng gói là offline**.
- Trên Apple Silicon chạy bằng **Metal (MPS)**, tự chuyển sang CPU nếu Metal lỗi.
  Ép cứng bằng `LOCAL_ASR_DEVICE=cpu` (tên cũ `PHOWHISPER_DEVICE` vẫn đọc được).
- **Phần viết biên bản gọi Gemini API** — chỉ khâu nhận dạng là offline. Âm thanh
  không bao giờ rời khỏi máy; chỉ bản chữ mới gửi lên Gemini.

---

## 7. Notarize (tuỳ chọn, cần Apple Developer $99/năm)

Sau khi có tài khoản, thêm vào workflow các secret `APPLE_CERT_P12`,
`APPLE_CERT_PASSWORD`, `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD`, rồi
thay bước ad-hoc sign trong `packaging/build_macos.sh` bằng:

```bash
codesign --force --deep --options runtime --timestamp \
  --sign "Developer ID Application: <Tên> (<TEAM_ID>)" "$APP_PATH"

xcrun notarytool submit "$DMG_PATH" \
  --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_APP_PASSWORD" --wait

xcrun stapler staple "$DMG_PATH"
```

Lúc đó người dùng chỉ cần kéo-thả, không bị Gatekeeper chặn.

---

## 8. Khi build fail

Workflow có sẵn 3 lớp kiểm tra, đọc log của bước bị đỏ:

1. **Build .app và .dmg** — lỗi cài đặt hoặc PyInstaller.
2. **Smoke test → Dependency selftest** — bundle chạy được nhưng thiếu thư viện.
   Log in ra đúng package nào fail; sửa bằng cách thêm nó vào `hiddenimports`
   hoặc vòng `collect_all` trong `packaging/MeetingSummarizer.spec`.
3. **Smoke test → /api/health** — server không lên; log `launcher.log` được in ra.

### Giới hạn đã biết

- **Chỉ build được Apple Silicon trên GitHub** — runner macOS Intel đã bị gỡ.
  Bản Intel phải build tay trên một máy Mac Intel bằng
  `bash packaging/build_macos.sh`, và ở đó `torch` bị ghim `2.2.2` vì PyTorch
  không còn phát hành wheel macOS x86_64 từ 2.3.0.
- **`.app` khoảng 2–2.5GB**, `.dmg` khoảng 800MB. Gần hết là torch và model
  ChunkFormer. Muốn nhỏ hơn nhiều (~80MB) thì bỏ model local, chỉ dùng
  OpenAI/Groq API.
- **Không universal binary** — torch không có wheel `universal2`, nên arm64 và
  Intel bắt buộc là hai bản riêng.
- **Bản macOS mở giao diện bằng trình duyệt mặc định**, khác bản Windows đã
  chuyển sang cửa sổ riêng (pywebview + WebView2). Xem đầu `launcher.py`.
- Bản CLI (`main.py` + `recorder.py`) **không** nằm trong bundle macOS; nó vẫn
  chỉ dùng cho Windows.

---

## Chạy từ source trên Mac (dev)

Không cần build `.app`:

```bash
chmod +x start_web.command
./start_web.command
```

Lần đầu sẽ tự tạo venv và cài dependency. Sau đó double-click file này là chạy.
