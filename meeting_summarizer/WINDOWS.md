# Build & deploy bản Windows

Bản Windows build **ngay trên máy Windows** — PyInstaller không cross-compile,
đúng như bản macOS phải build trên runner macOS.

Sản phẩm: `dist\MeetingSummarizer-<version>-win64-setup.exe`.

---

## 1. Yêu cầu máy build

| Thứ | Ghi chú |
|---|---|
| Windows 10/11 64-bit | Không có wheel torch 32-bit, nên bắt buộc 64-bit |
| Python 3.11 hoặc 3.12 | Đã test trên 3.12.7 |
| ~15 GB trống trên ổ C | venv + `build\` + `dist\` + file setup |
| Inno Setup 6 | `winget install JRSoftware.InnoSetup` |

Không cần Visual Studio hay compiler C++ — mọi thứ đều cài từ wheel dựng sẵn
(xem mục *deepspeed* bên dưới về lý do phải cố tình giữ như vậy).

## 2. Chạy build

```powershell
cd c:\Project\record_meeting-main\meeting_summarizer
$env:APP_VERSION = '1.0.0'
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

Script tự làm hết: tạo `.venv-build` riêng, cài dependency, tải model, chạy
PyInstaller, rồi gọi Inno Setup. Thiếu Inno Setup thì vẫn ra thư mục
`dist\MeetingSummarizer\` chạy được, chỉ bỏ qua bước đóng gói.

Lần đầu mất khoảng **30–50 phút**, phần lớn là tải dependency và nén LZMA2. Lần
sau nhanh hơn vì `.venv-build` và `models\` được giữ lại.

Muốn build bản dùng GPU NVIDIA: đặt `$env:BUNDLE_TORCH_CUDA = '1'`. Bundle sẽ
phình thêm khoảng 2 GB và chỉ chạy nhanh hơn trên máy có card NVIDIA — mặc định
CPU là lựa chọn đúng cho việc phát hành.

## 3. Kết quả

| File | Nội dung |
|---|---|
| `dist\MeetingSummarizer\` | Bundle onedir, chạy trực tiếp được |
| `dist\MeetingSummarizer-<version>-win64-setup.exe` | File gửi cho người dùng |

Onedir chứ không onefile: payload torch hơn 1 GB, onefile sẽ giải nén lại toàn
bộ vào thư mục temp **mỗi lần mở app**.

## 4. Kiểm tra bundle trước khi phát hành

```powershell
# Kiểm tra import: mọi thư viện nặng có mặt và load được
dist\MeetingSummarizer\MeetingSummarizer.exe --selftest

# Kiểm tra thật: tổng hợp giọng nói bằng SAPI rồi nhận dạng, có tắt mạng HF
dist\MeetingSummarizer\MeetingSummarizer.exe --selftest-transcribe

# Cả hai đều build windowed nên không in ra console — đọc kết quả ở:
Get-Content "$env:APPDATA\MeetingSummarizer\launcher.log" -Tail 40
```

`--selftest-transcribe` ép `HF_HUB_OFFLINE=1`, nên nó pass cũng là bằng chứng
trọng số model thật sự nằm trong bundle chứ không phải âm thầm tải về.

## 5. Cài trên máy người dùng

Chạy file setup. **Windows SmartScreen sẽ chặn lần đầu** — installer không được
ký số (chứng chỉ code signing khoảng 200–400 USD/năm), nên Windows báo *"Windows
protected your PC"*. Bấm **More info** → **Run anyway**.

Mặc định cài per-user (`%LOCALAPPDATA%\Programs\MeetingSummarizer`), không hỏi
quyền admin. Người dùng muốn cài cho mọi tài khoản thì bấm nút nâng quyền ở hộp
thoại đầu tiên.

Hướng dẫn cho người dùng cuối nằm ở
[HUONG_DAN_SU_DUNG_WINDOWS.txt](HUONG_DAN_SU_DUNG_WINDOWS.txt), được cài kèm và
tạo shortcut trong Start Menu.

### Vị trí file

| Nội dung | Đường dẫn |
|---|---|
| Cấu hình (ghi bởi màn hình Cài đặt) | `%APPDATA%\MeetingSummarizer\.env` |
| Log (debug khi app không mở được) | `%APPDATA%\MeetingSummarizer\launcher.log` |
| Cache model tải thêm | `%APPDATA%\MeetingSummarizer\hf_cache\` |
| Transcript & biên bản | `%USERPROFILE%\Documents\MeetingSummarizer\` |

Gỡ cài đặt **không** xoá ba thứ đầu lẫn thư mục biên bản — cài lại là dùng tiếp
được, không phải nhập lại API key.

### Thoát app

Bundle build ở chế độ windowed nên không có cửa sổ và không có icon khay hệ
thống. Đóng tab trình duyệt không làm server dừng. Muốn tắt hẳn phải vào Task
Manager và End task tiến trình `MeetingSummarizer`.

Đây là điểm kém hơn bản macOS (ở đó có icon Dock để Quit). Muốn sửa thì thêm
icon khay hệ thống bằng `pystray`, hoặc thêm một nút "Thoát" trong giao diện web
gọi xuống một endpoint tắt uvicorn.

---

## 6. deepspeed: vì sao `pip install chunkformer` không chạy trên Windows

`chunkformer` khai báo `deepspeed>=0.14.0`. deepspeed chỉ phát hành sdist, không
có wheel Windows, và backend build của nó `import torch` trong môi trường build
cô lập vốn không có torch — nên `pip install chunkformer` **fail ngay từ khâu
resolve**, trước cả khi động tới compiler.

deepspeed là dependency phục vụ **training**. Đường mà app này đi —
`ChunkFormerModel.from_pretrained` rồi `endless_decode` — không import tới nó.

Nên `packaging\build_windows.ps1` cài chunkformer bằng `--no-deps`, còn
[requirements-win.txt](requirements-win.txt) liệt kê thay phần dependency thật
sự cần. Danh sách đó dựng bằng cách import `ChunkFormerModel` trên môi trường
trống rồi bổ sung dần theo đúng thứ nó đòi:

```
jiwer  pandas  pydub  PyYAML  transformers  huggingface_hub  torch  torchaudio  numpy
```

Bỏ đi được: `deepspeed`, `tensorboard`, `tensorboardX`, `textgrid`,
`sentencepiece`, `Pillow`, `colorama`.

Vì ta đã tự nhận trách nhiệm về danh sách này nên **chunkformer bị ghim version**
(`==1.2.2`). Bản mới có thể import thêm thứ khác, và chỗ đó sẽ vỡ lúc chạy trong
tay người dùng chứ không phải lúc build. Nâng version thì chạy lại
`--selftest-transcribe` trước khi phát hành.

Build script cũng có sẵn bước `chunkformer import check` chạy trước PyInstaller,
để lỗi kiểu này lộ ra trong một phút thay vì sau nửa tiếng đóng gói.

## 7. Giới hạn 260 ký tự đường dẫn của Windows

torch đóng gói 107 file license third-party lồng sâu tới **182 ký tự** tính từ
gốc bundle:

```
_internal\torch-…dist-info\licenses\third_party\kineto\libkineto\third_party\
dynolog\third_party\prometheus-cpp\3rdparty\civetweb\src\third_party\
duktape-1.5.2\LICENSE.txt
```

Cộng thư mục cài đặt vào là chạm trần `MAX_PATH` của Windows. Triệu chứng là
Inno Setup báo *"The system cannot find the path specified"* giữa lúc nén — nó
không mở nổi file để đọc.

Hai chỗ đã xử lý, đừng gỡ ra:

1. **`SourceDir=..` trong [installer.iss](packaging/installer.iss)** — ISCC nối
   chuỗi đường dẫn chứ không chuẩn hoá, nên nếu để `Source: "..\dist\..."` thì
   tiền tố `packaging\..\` bị tính vào giới hạn cho **mọi** file.

2. **`consolidate_nested_licences()` trong
   [MeetingSummarizer.spec](packaging/MeetingSummarizer.spec)** — gộp mỗi cây
   `*.dist-info/licenses/**` thành một file `licenses/<dist>-THIRD-PARTY-
   NOTICES.txt`, mỗi đoạn có tiêu đề ghi đường dẫn gốc. Làm phẳng tên file không
   cứu được, vì độ dài nằm ngay trong chính tên các thư mục. Không có gì đọc
   thư mục này lúc chạy — `importlib.metadata` chỉ cần `METADATA`. Bước này chỉ
   bật trên Windows.

Bỏ hẳn cây license thì gọn hơn nhưng vi phạm điều khoản ghi công của giấy phép
BSD mà torch dùng, nên gộp chứ không xoá.

Sau khi xử lý, file sâu nhất còn lại trong bundle là 112 ký tự — thoải mái.

## 8. torch CPU, không phải CUDA

Wheel torch mặc định trên PyPI cho Windows gói kèm CUDA runtime — khoảng 2 GB
payload chỉ có ích với số ít người dùng có card NVIDIA. Script cài từ
`https://download.pytorch.org/whl/cpu` và **kiểm tra lại** sau khi cài xong mọi
thứ rằng `torch.__version__` vẫn còn hậu tố `+cpu`; một package nào đó phụ thuộc
torch có thể âm thầm kéo bản PyPI về, và triệu chứng đầu tiên nếu không kiểm tra
sẽ là file setup phình lên vài GB.

## 9. Khi build fail

| Bước đỏ | Nghĩa là |
|---|---|
| `torch install` | Mạng, hoặc index CPU đổi URL |
| `chunkformer import check` | chunkformer bản mới import thêm package — thêm vào `requirements-win.txt` |
| `PyInstaller` | Thường là thiếu `hiddenimports`; log nói rõ module nào |
| `--selftest` sau khi build | Bundle chạy nhưng thiếu thư viện — thêm vào `collect_all` trong spec |
| Inno Setup: *cannot find the path specified* | Đường dẫn vượt 260 ký tự — xem mục 7 |
| Inno Setup: lỗi khác | Xem `packaging\installer.iss`; `ArchitecturesAllowed=x64compatible` cần Inno ≥ 6.3 |

### Giới hạn đã biết

- **Không ký số.** SmartScreen sẽ cảnh báo mọi người dùng ở lần cài đầu. Bỏ hẳn
  cảnh báo cần mua chứng chỉ code signing (EV thì hết cảnh báo ngay, OV phải
  tích luỹ danh tiếng).
- **Chỉ chạy CPU** trong bản phát hành mặc định — xem mục 8.
- **Không có cách thoát ngoài Task Manager** — xem mục 5.
- **Wizard cài đặt bằng tiếng Anh.** Inno Setup 6 không kèm bản dịch tiếng Việt.
  Bản thân app và file hướng dẫn thì hoàn toàn tiếng Việt.
- Bản CLI (`main.py` + `recorder.py`) **không** nằm trong bundle; entry point là
  `launcher.py`, y như bản macOS.

---

## Chạy từ source trên Windows (dev)

Không cần build:

```powershell
.\start_web.bat
```
