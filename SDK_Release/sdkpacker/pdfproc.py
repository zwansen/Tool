"""PDF 处理：水印渲染 + AES-256 加密。"""

from __future__ import annotations

import io
import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pymupdf
from PIL import Image, ImageDraw, ImageFont


# ----------------------------------------------------------------------
# 水印位图（PNG）生成与缓存
# ----------------------------------------------------------------------

CN_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simsun.ttc",   # 宋体（优先：用户要求水印用宋体）
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
]


def pick_cjk_font() -> str:
    for p in CN_FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise RuntimeError("未找到系统中文字体")


@dataclass(frozen=True)
class WatermarkKey:
    """水印单元的全部可变参数。

    fontsize 以 PDF 点为单位（1 点 = 1/72 英寸）。
    density 控制平铺疏密：值越大水印之间留白越多。
    """
    text: str
    fontsize: int
    angle: float
    color: int
    opacity: float
    density: float


def _measure_text(text: str, font) -> tuple[int, int]:
    """测量文本像素尺寸。"""
    probe = Image.new("RGBA", (8, 8))
    bbox = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
    return max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])


def compute_tile_size(key: WatermarkKey) -> tuple[int, int]:
    """根据字号/角度/密度推算水印单元尺寸（PDF 点）。"""
    font = ImageFont.truetype(pick_cjk_font(), key.fontsize)
    tw, th = _measure_text(key.text, font)
    r = math.radians(key.angle)
    rw = abs(tw * math.cos(r)) + abs(th * math.sin(r))
    rh = abs(tw * math.sin(r)) + abs(th * math.cos(r))
    gap = key.fontsize * key.density
    return max(16, int(rw + gap)), max(16, int(rh + gap * 1.2))


@lru_cache(maxsize=64)
def build_watermark_tile(key: WatermarkKey) -> tuple[bytes, int, int]:
    """生成水印单元，返回 (PNG 字节, 单元宽, 单元高)。"""
    fontpath = pick_cjk_font()
    try:
        font = ImageFont.truetype(fontpath, key.fontsize)
    except Exception:
        font = ImageFont.load_default()

    tw, th = _measure_text(key.text, font)
    pad = max(8, int(key.fontsize * 0.3))
    canvas = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (255, 255, 255, 0))
    alpha = max(0, min(255, int(key.opacity * 255)))
    ImageDraw.Draw(canvas).text(
        (pad, pad), key.text, font=font,
        fill=((key.color >> 16) & 0xFF,
              (key.color >> 8) & 0xFF,
              key.color & 0xFF,
              alpha))
    canvas = canvas.rotate(-key.angle, resample=Image.BICUBIC, expand=True)

    W, H = compute_tile_size(key)
    cw, ch = canvas.size
    # 单元太小装不下时，按实际内容撑开
    W, H = max(W, cw + 4), max(H, ch + 4)
    base = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    # 不带 mask 直接覆盖，避免 paste 把 alpha 二次相乘
    base.paste(canvas, ((W - cw) // 2, (H - ch) // 2))
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    return buf.getvalue(), W, H


# ----------------------------------------------------------------------
# 加密权限
# ----------------------------------------------------------------------

PERM_PRINT      = pymupdf.PDF_PERM_PRINT       # 4
PERM_MODIFY    = pymupdf.PDF_PERM_MODIFY     # 8
PERM_COPY      = pymupdf.PDF_PERM_COPY       # 16
PERM_ANNOTATE  = pymupdf.PDF_PERM_ANNOTATE   # 32
PERM_ACCESS    = pymupdf.PDF_PERM_ACCESSIBILITY  # 512

# 允许位掩码 (默认: 允许打印/注释/无障碍，禁复制/修改)
ALLOW_DEFAULT = PERM_PRINT | PERM_ANNOTATE | PERM_ACCESS
ALLOW_PRINT   = PERM_PRINT | PERM_ANNOTATE | PERM_ACCESS
ALLOW_FULL    = PERM_PRINT | PERM_MODIFY | PERM_COPY | PERM_ANNOTATE | PERM_ACCESS


def make_permissions(allow_print: bool, allow_copy: bool, allow_modify: bool,
                     allow_access: bool = True) -> int:
    """根据开关位返回 PyMuPDF 的 permissions 参数值（允许位）。"""
    allow = PERM_ANNOTATE
    if allow_print:   allow |= PERM_PRINT
    if allow_copy:    allow |= PERM_COPY
    if allow_modify:  allow |= PERM_MODIFY
    if allow_access:  allow |= PERM_ACCESS
    return allow


# ----------------------------------------------------------------------
# 处理一个 PDF
# ----------------------------------------------------------------------

def apply_watermark(doc, *, text: str, fontsize: int = 72, angle: float = -45.0,
                    color: int = 0x6E6E6E, opacity: float = 0.5,
                    density: float = 1.5) -> None:
    """就地给已打开的文档铺水印：每页正中央一条。"""
    key = WatermarkKey(text=text, fontsize=fontsize, angle=angle,
                       color=color, opacity=opacity, density=density)
    png, tw, th = build_watermark_tile(key)
    for page in doc:
        w, h = page.rect.width, page.rect.height
        cx, cy = w / 2, h / 2
        page.insert_image(
            pymupdf.Rect(cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2),
            stream=png, overlay=True)
    try:
        doc.subset_fonts()
    except Exception:
        pass


def encrypt_pdf(doc, out, *, owner_pw: str = "", user_pw: str = "",
                permissions: int = ALLOW_DEFAULT) -> None:
    doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256,
             owner_pw=owner_pw or "owner", user_pw=user_pw,
             permissions=permissions, deflate=True, garbage=3)


def process_pdf(src_bytes: bytes,
                *,
                watermark: Optional[str] = None,
                watermark_color: int = 0x6E6E6E,
                watermark_opacity: float = 0.5,
                watermark_angle: float = -45.0,
                watermark_fontsize: int = 72,
                watermark_density: float = 1.5,
                encrypt: bool = False,
                owner_pw: str = "",
                user_pw: str = "",
                permissions: int = ALLOW_DEFAULT,
                ) -> bytes:
    """对 PDF 字节流执行水印 / 加密，返回新的 PDF 字节流。

    watermark 为 None/空 → 不加水印；encrypt 为 False → 不加密。
    两者都可独立开关，便于对单个文档做「仅水印 / 仅加密 / 两者 / 原样」。
    """
    doc = pymupdf.open(stream=src_bytes, filetype="pdf")

    if watermark:
        apply_watermark(doc, text=watermark, fontsize=watermark_fontsize,
                        angle=watermark_angle, color=watermark_color,
                        opacity=watermark_opacity, density=watermark_density)

    out = io.BytesIO()
    if encrypt:
        encrypt_pdf(doc, out, owner_pw=owner_pw, user_pw=user_pw,
                    permissions=permissions)
    else:
        doc.save(out, deflate=True, garbage=3)
    doc.close()
    return out.getvalue()