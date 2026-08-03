"""Sinh icon ứng dụng từ mã, không phải từ một file ảnh ai đó vẽ rồi để lạc.

Chạy lại được bất cứ lúc nào và cho ra kết quả y hệt, nên khi cần đổi màu hay
đổi hình thì sửa ở đây chứ không phải mở phần mềm đồ hoạ.

    python packaging/make_icon.py

Sinh ra:
    packaging/icon.ico   — MeetingSummarizer.spec tự nhặt làm icon cho file exe
    static/icon.png      — 1024px, đóng gói cùng app: dùng làm favicon của trang
                           và làm nguồn để tạo icon macOS (.icns)

Chỉ cần Pillow, và chỉ cần lúc build. Không phải phụ thuộc lúc chạy.
"""
import os
import sys

from PIL import Image, ImageDraw

S = 1024                      # vẽ lớn rồi thu nhỏ: viền mượt hơn hẳn vẽ thẳng ở 32px
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Cùng dải xanh với nút chính trong giao diện, để icon trên taskbar và cửa sổ
# bên trong trông như một sản phẩm.
TOP = (59, 130, 246)          # #3b82f6
BOTTOM = (29, 78, 216)        # #1d4ed8
FG = (255, 255, 255, 255)

# Windows hiển thị icon nhỏ nhất ở 16px. Mọi kích thước đều được thu từ bản
# 1024 chứ không vẽ lại, nên nét không bị lệch giữa các cỡ.
ICO_SIZES = [256, 128, 64, 48, 32, 24, 16]


def rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius, fill=255)
    return mask


def vertical_gradient(size, top, bottom):
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return grad.resize((size, size), Image.NEAREST)


def draw_microphone(img):
    """Khe hở giữa thân và vành phải đủ rộng.

    Ở 16px, mỗi pixel là 64 pixel của bản vẽ này — khe hẹp sẽ nhoè thành một
    khối đặc và cái micro biến mất.
    """
    d = ImageDraw.Draw(img)
    # Thân micro: viên nang bo tròn hoàn toàn.
    d.rounded_rectangle([402, 202, 622, 560], radius=110, fill=FG)
    # Vành ôm: nửa dưới một đường tròn, hở phía trên như micro thật.
    # Bán kính trong 148 so với thân rộng 110 -> khe 38, giữ được ở cỡ nhỏ.
    d.arc([312, 300, 712, 700], start=0, end=180, fill=FG, width=52)
    # Chân và đế.
    d.rounded_rectangle([487, 698, 537, 792], radius=25, fill=FG)
    d.rounded_rectangle([392, 790, 632, 840], radius=25, fill=FG)


def build():
    base = vertical_gradient(S, TOP, BOTTOM).convert("RGBA")
    base.putalpha(rounded_mask(S, radius=232))

    # Vẽ micro trên lớp riêng rồi ghép: nét trắng không bị nền cắt mất phần bo.
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw_microphone(layer)
    icon = Image.alpha_composite(base, layer)

    # static/ đi cùng app khi đóng gói; packaging/ thì không, nên bản PNG phải
    # nằm ở static/ mới phục vụ được làm favicon lúc chạy.
    static_dir = os.path.join(os.path.dirname(OUT_DIR), "static")
    os.makedirs(static_dir, exist_ok=True)
    png_path = os.path.join(static_dir, "icon.png")
    icon.save(png_path)

    ico_path = os.path.join(OUT_DIR, "icon.ico")
    icon.save(ico_path, sizes=[(n, n) for n in ICO_SIZES])

    for path in (png_path, ico_path):
        print(f"  da tao {os.path.basename(path)}  ({os.path.getsize(path):,} bytes)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(build())
    except ImportError:
        print("Can Pillow de tao icon:  pip install pillow")
        sys.exit(1)
