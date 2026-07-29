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

Lần chạy đầu tiên app tạo file cấu hình rồi hiện hộp thoại và thoát:

```
~/Library/Application Support/MeetingSummarizer/.env
```

Mở file đó, điền `OPENAI_API_KEY` (bắt buộc — phần tóm tắt luôn dùng GPT), rồi
mở lại app. App sẽ tự tìm port trống từ 8000 và mở trình duyệt mặc định.

Vì micro do **trình duyệt** thu chứ không phải app, quyền micro sẽ do Chrome/
Safari hỏi, không cần cấp quyền ở tầng hệ điều hành.

### Vị trí file

| Nội dung | Đường dẫn |
|---|---|
| Cấu hình | `~/Library/Application Support/MeetingSummarizer/.env` |
| Log (debug khi app không mở được) | `~/Library/Application Support/MeetingSummarizer/launcher.log` |
| Model PhoWhisper đã tải | `~/Library/Application Support/MeetingSummarizer/hf_cache/` |
| Transcript & biên bản | `~/Documents/MeetingSummarizer/` |

### Thoát app

Cửa sổ trình duyệt đóng lại không làm server dừng. Chuột phải icon trên Dock →
**Quit**.

---

## 6. PhoWhisper (transcribe offline)

`WHISPER_PROVIDER=phowhisper` chạy model ngay trên máy, không gọi API để nhận
dạng giọng nói. Lưu ý:

- **Lần đầu vẫn cần internet** để tải model (~1GB với `PhoWhisper-small`). Sau
  đó chạy hoàn toàn offline. Muốn tải trước, mở app một lần khi còn mạng.
- Trên Apple Silicon, model chạy trên **Metal (MPS)**. Nếu gặp lỗi lạ khi nhận
  dạng, sửa `.env`: `PHOWHISPER_DEVICE=cpu` (chậm hơn nhưng ổn định).
- **Phần tóm tắt vẫn gọi OpenAI API** — chỉ transcribe là offline.
- Model to hơn (`PhoWhisper-medium`) chính xác hơn nhưng chậm hơn nhiều trên CPU.

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
