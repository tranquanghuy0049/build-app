# Build & deploy bản macOS

Bản macOS được build trên GitHub Actions (runner macOS thật), vì PyInstaller
không cross-compile — không có cách nào tạo `.app` từ máy Windows.

Sản phẩm: `MeetingSummarizer-<version>-arm64.dmg` (Apple Silicon) và
`MeetingSummarizer-<version>-x86_64.dmg` (Intel).

---

## 1. Đưa code lên GitHub

Thư mục hiện tại chưa phải git repo:

```powershell
cd c:\Project\record_meeting-main
git init
git add .
git commit -m "Add macOS build pipeline"
git branch -M main
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main
```

Kiểm tra trước khi push: `git status` **không được** liệt kê
`meeting_summarizer/.env` hay `meeting_summarizer.zip` (file zip có chứa `.env`
với API key thật — đã thêm vào `.gitignore`).

## 2. Chạy build

GitHub → tab **Actions** → **Build macOS app** → **Run workflow**, nhập version
(vd `1.0.0`).

Hoặc tự động khi push tag:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

Push tag sẽ build **và** tự tạo GitHub Release đính kèm 2 file `.dmg`.

Mỗi build mất khoảng **40–70 phút** (phần lớn là PyInstaller gom torch).

> ⚠️ **Chi phí runner**: runner macOS tính **10×** phút so với Linux. Repo
> **private** với free tier 2000 phút/tháng chỉ đủ khoảng **3 build**/tháng.
> Repo **public** thì miễn phí không giới hạn. Nếu phải để private và build
> nhiều, cân nhắc self-hosted runner hoặc thuê Mac cloud.

## 3. Tải kết quả

Vào trang run vừa chạy → mục **Artifacts** ở cuối → tải
`MeetingSummarizer-macos-arm64` (hoặc `-x86_64`).

Cách chọn kiến trúc: trên Mac vào  → About This Mac. Nếu ghi *Apple M1/M2/M3/M4*
→ dùng bản **arm64**. Nếu ghi *Intel* → bản **x86_64**.

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

## 6. PhoWhisper (transcribe offline, đóng gói sẵn)

Mặc định là `WHISPER_PROVIDER=phowhisper`. Trọng số `PhoWhisper-small` **nằm sẵn
trong app**, cài xong là nhận dạng được ngay, không tải gì, không cần mạng.

Cách đóng gói: [packaging/fetch_model.py](packaging/fetch_model.py) tải model lúc
build và lưu vào `models/vinai__PhoWhisper-small`, spec đưa thư mục đó vào
bundle, rồi [transcriber.py](transcriber.py) ưu tiên đường dẫn cục bộ đó.

Trọng số lưu ở **float16** để `.dmg` bớt một nửa dung lượng, nhưng suy luận vẫn
chạy ở float32 — float16 trên MPS gây NaN với Whisper ở nhiều bản torch.

Đổi model đóng gói khi build:

```bash
PHOWHISPER_BUNDLE_MODEL=vinai/PhoWhisper-base bash packaging/build_macos.sh
PHOWHISPER_BUNDLE_FP16=0   # giữ nguyên độ chính xác đầy đủ, file to gấp đôi
```

Lưu ý:

- Trong ⚙ vẫn chọn được `tiny`/`base`/`medium`, nhưng **chỉ bản được đóng gói là
  offline**. Chọn bản khác thì app sẽ tải về (cần mạng một lần).
- Trên Apple Silicon model chạy bằng **Metal (MPS)**. Gặp lỗi lạ thì đổi
  `PHOWHISPER_DEVICE=cpu` trong ⚙ — chậm hơn nhưng ổn định nhất.
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

Chạy lại chỉ một kiến trúc: workflow đặt `fail-fast: false` nên bản arm64 vẫn
build xong kể cả khi bản Intel hỏng.

### Giới hạn đã biết

- **Intel Mac ghim `torch==2.2.2`** — PyTorch không còn phát hành wheel macOS
  x86_64 từ 2.3.0. Nếu sau này `transformers` yêu cầu torch mới hơn, bản Intel
  sẽ phải bỏ PhoWhisper và chỉ dùng API.
- **`.app` khoảng 2–2.5GB**, `.dmg` khoảng 1–1.3GB. Gần hết là torch. Muốn nhỏ
  hơn nhiều (~80MB) thì bỏ PhoWhisper và chỉ dùng OpenAI/Groq API.
- **Không universal binary** — torch không có wheel `universal2`, nên bắt buộc
  hai bản riêng.
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
