"""端到端流水线编排。"""

from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .core import (CUSTOM_TEXT, CustomItem, Node, Source, WRITERS, build_tree,
                   default_output_name, is_pdf, iter_archive_entries,
                   parse_root_info, PDF_BOTH, PDF_ENCRYPT, PDF_NONE,
                   PDF_WATERMARK, selected_files, summarize_pdf_modes)
from .pdfproc import (ALLOW_DEFAULT, make_permissions, process_pdf)


@dataclass
class ProcessParams:
    source_path: str
    output_dir: str
    output_format: str           # zip / 7z / tar.gz / folder
    customer: str
    sdk_type: str               # SDK / SDKLite
    version: str
    date_str: str               # yyyyMMdd
    default_pdf_mode: str = PDF_BOTH   # PDF 默认处理方式
    owner_pw: str = ""
    user_pw: str = ""
    allow_print: bool = True
    allow_copy: bool = False
    allow_modify: bool = False
    allow_access: bool = True   # 无障碍（屏幕阅读器等）
    watermark_text: str = ""    # 空 = 不加水印
    watermark_opacity: float = 0.5
    watermark_angle: float = -45.0
    watermark_fontsize: int = 72
    watermark_density: float = 1.5
    output_name_override: str = ""   # 非空则用它作输出文件名（不含扩展名）
    # 已扫描并完成勾选/逐文件设置的文件树。
    # 传入则直接复用，避免重新扫描丢掉用户在界面上的选择。
    tree: Optional[Node] = None
    custom_items: List[CustomItem] = field(default_factory=list)
    progress_cb: Optional[Callable[[str, float], None]] = None
    cancelled_cb: Optional[Callable[[], bool]] = None


@dataclass
class ProcessResult:
    output_path: str
    total_files: int
    pdf_files: int
    watermarked: int
    encrypted: int
    bytes_in: int
    bytes_out: int
    elapsed: float
    log: List[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# 进度辅助：把 0~1 的区间按比例映射，避免进度条长时间卡在某一段
# ----------------------------------------------------------------------

class Progress:
    def __init__(self, cb: Optional[Callable[[str, float], None]]):
        self.cb = cb
        self._last = -1.0

    def emit(self, msg: str = "", frac: float = -1.0) -> None:
        if self.cb is None:
            return
        if frac >= 0:
            # 只推送有可见变化的进度，减少 UI 抖动
            if frac - self._last < 0.001 and frac < 1.0:
                return
            self._last = frac
        self.cb(msg, frac)

    def range(self, start: float, end: float):
        """返回一个把 0~1 映射到 [start, end] 的子进度函数。"""
        def sub(done: float, msg: str = "") -> None:
            self.emit(msg, start + (end - start) * max(0.0, min(1.0, done)))
        return sub


def _cost(node: Node) -> float:
    """估算单个文件的处理开销，用于让进度条走得均匀。

    PDF 需要解码/水印/重编码，耗时远高于同体积的普通文件复制，
    因此给它一个放大系数，否则进度条会在 PDF 段明显"卡住"。
    """
    size = max(node.size, 4096)
    if is_pdf(node.relpath):
        return size * 6 + 3_000_000
    return size


def run(params: ProcessParams) -> ProcessResult:
    log: List[str] = []
    t0 = time.time()
    prog = Progress(params.progress_cb)

    def emit(msg: str, frac: float = -1) -> None:
        log.append(msg)
        prog.emit(msg, frac)

    source = Source(params.source_path)
    try:
        # ---------- 1) 扫描（传入已扫描的树则直接复用） ----------
        if params.tree is not None:
            tree = params.tree
            emit(f"复用界面已扫描的文件树: {params.source_path}", 0.03)
        else:
            emit(f"扫描资源: {params.source_path}", 0.0)
            entries = list(iter_archive_entries(source.path))
            tree = build_tree(entries)
            emit(f"发现 {sum(1 for e in entries if not e.is_dir)} 个条目", 0.03)

        # ---------- 2) 勾选 ----------
        files = selected_files(tree)
        if not files:
            raise RuntimeError("未勾选任何文件，终止")
        n_pdf = sum(1 for f in files if is_pdf(f.relpath))
        counts = summarize_pdf_modes(files, params.default_pdf_mode)
        emit(f"已勾选 {len(files)} 个文件（PDF {n_pdf} 个："
             f"水印 {counts[PDF_WATERMARK] + counts[PDF_BOTH]} / "
             f"加密 {counts[PDF_ENCRYPT] + counts[PDF_BOTH]}）", 0.05)

        # ---------- 3) 输出路径 ----------
        out_dir = Path(params.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if params.output_name_override:
            out_name = params.output_name_override
            if params.output_format != "folder":
                ext = params.output_format   # zip / 7z / tar.gz
                if not out_name.lower().endswith("." + ext):
                    out_name += "." + ext
        else:
            out_name = default_output_name(
                params.customer, params.sdk_type, params.version,
                params.date_str, params.output_format)
        out_path = out_dir / out_name

        # ---------- 4) 逐文件处理 ----------
        collected: List[tuple[str, bytes]] = []
        bytes_in = 0
        bytes_out = 0
        n_wm = 0
        n_enc = 0
        perms = make_permissions(
            params.allow_print, params.allow_copy, params.allow_modify,
            params.allow_access)

        total_cost = sum(_cost(f) for f in files) or 1.0
        done_cost = 0.0
        step = prog.range(0.05, 0.88)

        for i, node in enumerate(files):
            if params.cancelled_cb and params.cancelled_cb():
                raise RuntimeError("用户取消")
            relpath = node.relpath
            short = relpath.rsplit("/", 1)[-1]
            step(done_cost / total_cost, f"处理 ({i + 1}/{len(files)}): {short}")

            try:
                raw = source.read(relpath)
            except Exception as e:
                emit(f"读取失败 {relpath}: {e}")
                done_cost += _cost(node)
                continue
            bytes_in += len(raw)

            if is_pdf(relpath):
                mode = node.resolve_pdf_mode(params.default_pdf_mode)
                do_wm = mode in (PDF_BOTH, PDF_WATERMARK) and bool(params.watermark_text)
                do_enc = mode in (PDF_BOTH, PDF_ENCRYPT)
                if do_wm or do_enc:
                    try:
                        out_data = process_pdf(
                            raw,
                            watermark=params.watermark_text if do_wm else None,
                            watermark_opacity=params.watermark_opacity,
                            watermark_angle=params.watermark_angle,
                            watermark_fontsize=params.watermark_fontsize,
                            watermark_density=params.watermark_density,
                            encrypt=do_enc,
                            owner_pw=params.owner_pw,
                            user_pw=params.user_pw,
                            permissions=perms,
                        )
                        tags = []
                        if do_wm:
                            tags.append("水印"); n_wm += 1
                        if do_enc:
                            tags.append("加密"); n_enc += 1
                        emit(f"  {'+'.join(tags)}: {short} "
                             f"({len(raw)}→{len(out_data)})")
                    except Exception as e:
                        emit(f"PDF 处理失败 {short}: {e}，按原样输出")
                        out_data = raw
                else:
                    out_data = raw
                    emit(f"  原样: {short}")
            else:
                out_data = raw

            bytes_out += len(out_data)
            collected.append((relpath, out_data))
            done_cost += _cost(node)

        step(1.0)

        # ---------- 4.5) 附加内容 ----------
        if params.custom_items:
            n_added = 0
            text_vars = {
                "customer": params.customer,
                "type": params.sdk_type,
                "version": params.version,
                "date": params.date_str,
            }
            for item in params.custom_items:
                # 文本类内容在此做变量替换（{customer} 等 → 实际值）
                pairs = item.expand(vars=text_vars if item.kind == CUSTOM_TEXT
                                    else None)
                collected.extend(pairs)
                n_added += len(pairs)
            if n_added:
                emit(f"附加内容：新增 {n_added} 个文件", 0.895)

        # ---------- 5) 写出 ----------
        emit(f"输出 -> {out_path}", 0.90)
        writer = WRITERS[params.output_format]
        step2 = prog.range(0.90, 1.0)

        def on_write(msg: str) -> None:
            # 回调里带的是"打包: xxx"文本，这里按计数推进度
            on_write.n += 1
            if on_write.n % 20 == 0 or on_write.n >= len(collected):
                step2(min(1.0, on_write.n / max(1, len(collected))), msg)

        on_write.n = 0
        writer(out_path, collected, progress=on_write)

        elapsed = time.time() - t0
        emit(f"完成：{len(files)} 个文件，PDF {n_pdf} 个"
             f"（水印 {n_wm} / 加密 {n_enc}），用时 {elapsed:.1f}s", 1.0)
        return ProcessResult(
            output_path=str(out_path),
            total_files=len(files),
            pdf_files=n_pdf,
            watermarked=n_wm,
            encrypted=n_enc,
            bytes_in=bytes_in,
            bytes_out=bytes_out,
            elapsed=elapsed,
            log=log,
        )
    finally:
        source.close()