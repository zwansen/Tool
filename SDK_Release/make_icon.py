"""生成工具图标 icon.ico（资料包 + 锁加密 + 斜向"凯芯"水印意象）。

用法：python make_icon.py
依赖：Pillow（已随 pymupdf 装入打包 venv）
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

OUT = r"C:\Users\yaoyu\WorkBuddy\2026-09-01-10-43-18\sdkpacker\icon.ico"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
]


def load_font(size: int) -> ImageFont.ImageFont:
    for p in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角蓝底
    pad = max(2, size // 16)
    d.rounded_rectangle([pad, pad, size - pad, size - pad],
                        radius=size // 8, fill=(37, 99, 235, 255))

    # 文档（白纸）
    doc_m = size // 6
    doc = [doc_m, doc_m, size - doc_m, int(size - doc_m * 1.6)]
    d.rounded_rectangle(doc, radius=max(2, size // 20),
                        fill=(255, 255, 255, 255),
                        outline=(203, 213, 225, 255),
                        width=max(1, size // 128))

    # 文档文本行
    lx0 = doc[0] + size // 8
    lx1 = doc[2] - size // 8
    ly = doc[1] + size // 6
    gap = size // 14
    for i in range(4):
        d.line([lx0, ly + i * gap, lx1, ly + i * gap],
               fill=(148, 163, 184, 255), width=max(1, size // 110))

    # 斜向"KX"水印（半透明，纯 ASCII 避免文件名/资源乱码）
    wm_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wm_layer)
    fs = size // 4
    f = load_font(fs)
    txt = "KX"
    bbox = wd.textbbox((0, 0), txt, font=f)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tile = Image.new("RGBA", (tw + 20, th + 20), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((10 - bbox[0], 10 - bbox[1]), txt, font=f,
                              fill=(110, 110, 110, 120))
    tile = tile.rotate(-45, resample=Image.BICUBIC, expand=True)
    wm_layer.paste(tile, ((size - tile.width) // 2, (size - tile.height) // 2), tile)
    img = Image.alpha_composite(img, wm_layer)

    # 锁（加密意象），置于文档右下角
    d = ImageDraw.Draw(img)
    lw = size // 6
    lh = size // 7
    lcx = doc[2] - lw - size // 10
    lcy = doc[3] - lh - size // 10
    lock_col = (251, 191, 36, 255)
    d.rounded_rectangle([lcx, lcy + lh // 3, lcx + lw, lcy + lh],
                        radius=lh // 6, fill=lock_col)
    sh = lh // 3
    d.arc([lcx + lw // 4, lcy, lcx + lw * 3 // 4, lcy + sh * 2],
          start=180, end=360, fill=lock_col, width=max(2, size // 90))
    d.ellipse([lcx + lw // 2 - 2, lcy + int(lh * 0.5),
               lcx + lw // 2 + 2, lcy + int(lh * 0.7)],
              fill=(37, 99, 235, 255))
    return img


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [make(s) for s in sizes]
    imgs[0].save(OUT, format="ICO", sizes=[(s, s) for s in sizes],
                 append_images=imgs[1:])
    print("saved", OUT)


if __name__ == "__main__":
    main()
