"""核心流水线：扫描、勾选树、版本号解析、PDF 处理、压缩输出。"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional, Tuple

# 注意：py7zr 体积/导入开销较大，且多数资料包是 zip，故延迟到真正用到 7z 时再导入。


# ----------------------------------------------------------------------
# 1) 资源枚举：压缩包 / 文件夹的统一抽象
# ----------------------------------------------------------------------

ARCHIVE_EXTS = {".zip", ".tar", ".tgz", ".tbz", ".tbz2", ".txz", ".7z"}


@dataclass
class Entry:
    """资源树里的一个条目。"""
    name: str                # 在资源中的相对路径（统一用 /）
    is_dir: bool
    size: int = 0

    @property
    def relpath(self) -> str:
        return self.name


def iter_archive_entries(path: str | Path) -> Iterator[Entry]:
    """遍历压缩包或文件夹，返回所有条目（包含目录本身）。"""
    p = Path(path)
    if p.is_dir():
        yield from iter_folder(p)
        return
    suffix = p.suffix.lower()
    if suffix == ".zip":
        yield from iter_zip(p)
    elif suffix in (".tar", ".tgz", ".tbz", ".tbz2", ".txz"):
        yield from iter_tar(p)
    elif suffix == ".7z":
        yield from iter_7z(p)
    else:
        raise ValueError(f"不支持的资源格式: {p.suffix}")


def iter_folder(root: Path) -> Iterator[Entry]:
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel == ".":
            rel = ""
        yield Entry(name=rel, is_dir=True, size=0)
        for fn in filenames:
            full = Path(dirpath) / fn
            yield Entry(name=f"{rel}/{fn}" if rel else fn,
                        is_dir=False, size=full.stat().st_size)


def iter_zip(p: Path) -> Iterator[Entry]:
    # 探测文件名编码：先尝试 utf-8，再回落 cp936 (GBK)
    for enc in ("utf-8", "cp936"):
        try:
            with zipfile.ZipFile(p, metadata_encoding=enc) as z:
                # 触发一次解码
                _ = z.namelist()
                infos = sorted(z.infolist(), key=lambda i: i.filename)
                for info in infos:
                    yield Entry(name=info.filename.rstrip("/"),
                                is_dir=info.is_dir(),
                                size=info.file_size)
            return
        except UnicodeDecodeError:
            continue


def iter_tar(p: Path) -> Iterator[Entry]:
    mode = "r:*"
    with tarfile.open(p, mode) as t:
        members = sorted(t.getmembers(), key=lambda m: m.name)
        for m in members:
            yield Entry(name=m.name.rstrip("/"),
                        is_dir=m.isdir(),
                        size=m.size)


def iter_7z(p: Path) -> Iterator[Entry]:
    import py7zr
    with py7zr.SevenZipFile(p, mode="r") as z:
        names = sorted(z.getnames())
        infos = {n: z.getinfo(n) for n in names}
        for n in names:
            info = infos.get(n)
            is_dir = bool(getattr(info, "is_directory", False)) or n.endswith("/")
            size = int(getattr(info, "uncompressed", 0) or 0)
            yield Entry(name=n.rstrip("/"),
                        is_dir=is_dir, size=size)


# ----------------------------------------------------------------------
# 2) 文件树（带勾选状态）
# ----------------------------------------------------------------------

CHECKED_OFF = 0
CHECKED_ON = 1
CHECKED_PARTIAL = 2


# ----------------------------------------------------------------------
# 单个 PDF 的处理方式（可逐文件覆盖）
# ----------------------------------------------------------------------

PDF_INHERIT   = "inherit"    # 跟随全局默认
PDF_BOTH      = "both"       # 加水印 + 加密
PDF_WATERMARK = "watermark"  # 仅加水印
PDF_ENCRYPT   = "encrypt"    # 仅加密
PDF_NONE      = "none"       # 原样输出

PDF_MODE_LABELS = {
    PDF_INHERIT:   "同默认",
    PDF_BOTH:      "水印+加密",
    PDF_WATERMARK: "仅水印",
    PDF_ENCRYPT:   "仅加密",
    PDF_NONE:      "原样",
}
PDF_MODE_ORDER = [PDF_INHERIT, PDF_BOTH, PDF_WATERMARK, PDF_ENCRYPT, PDF_NONE]


def is_pdf(relpath: str) -> bool:
    return relpath.lower().endswith(".pdf")


@dataclass
class Node:
    """文件树节点。"""
    name: str               # 显示名（不含父路径）
    relpath: str            # 完整相对路径
    is_dir: bool
    size: int = 0
    checked: int = CHECKED_ON
    pdf_mode: str = PDF_INHERIT   # 仅对 PDF 有意义
    children: List["Node"] = field(default_factory=list)
    parent: Optional["Node"] = None

    def resolve_pdf_mode(self, default: str = PDF_BOTH) -> str:
        """解析该 PDF 最终采用的处理方式。"""
        if self.pdf_mode == PDF_INHERIT:
            return default
        return self.pdf_mode

    def add(self, child: "Node") -> "Node":
        child.parent = self
        self.children.append(child)
        return child

    def find(self, relpath: str) -> Optional["Node"]:
        if self.relpath == relpath:
            return self
        for c in self.children:
            r = c.find(relpath)
            if r is not None:
                return r
        return None

    def all_files(self) -> Iterator["Node"]:
        if not self.is_dir:
            yield self
            return
        for c in self.children:
            yield from c.all_files()

    def walk(self) -> Iterator["Node"]:
        yield self
        for c in self.children:
            yield from c.walk()


def build_tree(entries: Iterable[Entry]) -> Node:
    """从平铺的 Entry 列表构建一棵树，根节点用空 relpath 表示。"""
    root = Node(name="", relpath="", is_dir=True)
    for e in entries:
        rel = e.relpath
        if rel == "" or rel == ".":
            # 顶层目录自身（文件夹）已由第一个子项隐式覆盖
            continue
        parts = rel.split("/")
        cur = root
        for i, part in enumerate(parts):
            full = "/".join(parts[:i + 1])
            child = next((c for c in cur.children if c.relpath == full), None)
            if child is None:
                is_last = (i == len(parts) - 1)
                child = Node(name=part, relpath=full,
                             is_dir=(e.is_dir if is_last else True))
                cur.add(child)
            cur = child
        cur.size = e.size
    # 排序：目录优先，按名
    _sort_tree(root)
    return root


def _sort_tree(n: Node) -> None:
    n.children.sort(key=lambda c: (not c.is_dir, c.name.lower()))
    for c in n.children:
        _sort_tree(c)


def recalc_check_state(n: Node) -> int:
    """从子节点向上回算勾选状态。"""
    if not n.is_dir or not n.children:
        return n.checked
    states = [recalc_check_state(c) for c in n.children]
    if all(s == CHECKED_ON for s in states):
        n.checked = CHECKED_ON
    elif all(s == CHECKED_OFF for s in states):
        n.checked = CHECKED_OFF
    else:
        n.checked = CHECKED_PARTIAL
    return n.checked


def set_checked(n: Node, state: int) -> None:
    """设置节点及所有后代为同一勾选状态，再向上回算。"""
    def walk(x: Node) -> None:
        x.checked = state
        for c in x.children:
            walk(c)
    walk(n)
    p = n.parent
    while p is not None:
        recalc_check_state(p)
        p = p.parent


def selected_files(n: Node) -> List[Node]:
    """收集所有处于勾选/半勾选 状态下的文件节点。"""
    out: List[Node] = []
    def walk(x: Node) -> None:
        if not x.is_dir and x.checked != CHECKED_OFF:
            out.append(x)
        for c in x.children:
            walk(c)
    walk(n)
    return out


def set_pdf_mode(n: Node, mode: str) -> None:
    """把节点及其所有 PDF 后代的处理方式设为同一模式（非 PDF 不受影响）。"""
    def walk(x: Node) -> None:
        if is_pdf(x.relpath):
            x.pdf_mode = mode
        for c in x.children:
            walk(c)
    walk(n)


def summarize_pdf_modes(files: List[Node], default: str) -> dict:
    """统计各处理方式下的 PDF 数量，用于运行前展示。"""
    counts = {m: 0 for m in PDF_MODE_ORDER}
    for f in files:
        if is_pdf(f.relpath):
            counts[f.resolve_pdf_mode(default)] += 1
    return counts


# ----------------------------------------------------------------------
# 附加内容：往发布包里额外塞文件夹或自建文本文件
# ----------------------------------------------------------------------

CUSTOM_FOLDER = "folder"
CUSTOM_TEXT = "text"


@dataclass
class CustomItem:
    """一条用户自定义的附加内容。"""
    kind: str            # CUSTOM_FOLDER / CUSTOM_TEXT
    name: str            # 界面显示名
    target: str = ""     # 放到包内的哪个目录下（相对路径，"" 表示包根）
    src_path: str = ""   # folder：本地目录
    filename: str = ""   # text：文件名，如 使用说明.txt
    content: str = ""    # text：文件内容

    def display(self) -> str:
        if self.kind == CUSTOM_FOLDER:
            dest = f"{self.target}/{self.name}" if self.target else self.name
            return f"[文件夹] {self.src_path}  →  包内：{dest}/"
        dest = f"{self.target}/{self.filename}" if self.target else self.filename
        return f"[文本] {dest}"

    def expand(self, vars: Optional[dict] = None) -> List[Tuple[str, bytes]]:
        """展开成 (包内相对路径, 字节) 列表。

        vars 为可选变量表（如 {"customer": "张三", "date": "20260901"}），
        用于在文本内容上替换 {customer}/{type}/{version}/{date} 等占位符。
        """
        out: List[Tuple[str, bytes]] = []
        if self.kind == CUSTOM_TEXT:
            if not self.filename:
                return out
            content = self.content
            if vars:
                for k, v in vars.items():
                    content = content.replace("{" + k + "}", str(v))
            rel = f"{self.target}/{self.filename}" if self.target else self.filename
            out.append((rel, content.encode("utf-8")))
        elif self.kind == CUSTOM_FOLDER:
            p = Path(self.src_path)
            if not p.is_dir():
                return out
            folder_name = p.name   # 把"选中的文件夹本身"作为包内子目录整体加入
            for dirpath, _, filenames in os.walk(p):
                for fn in filenames:
                    full = Path(dirpath) / fn
                    rel_in_src = os.path.relpath(full, p).replace(os.sep, "/")
                    parts = [x for x in (self.target, folder_name) if x]
                    rel = "/".join(parts + [rel_in_src]) if parts else rel_in_src
                    try:
                        out.append((rel, full.read_bytes()))
                    except Exception:
                        continue
        return out


# ----------------------------------------------------------------------
# 3) 版本号 / 名称解析
# ----------------------------------------------------------------------

_VER_PATTERNS = [
    re.compile(r"[Vv](\d+(?:\.\d+){1,5}[A-Za-z0-9]*)"),       # V1.3.0.1RC1
    re.compile(r"(?<![A-Za-z])(\d+\.\d+(?:\.\d+){0,4}[A-Za-z0-9]*)"),  # 1.3.0.1RC1
    re.compile(r"(?<![A-Za-z])(\d+\.\d+)"),                    # 1.3
]


def parse_version(stem: str) -> str:
    """从资源根名中提取版本号，例如 'Delos_SDK_V2.3.5.8' → '2.3.5.8'。"""
    for pat in _VER_PATTERNS:
        m = pat.search(stem)
        if m:
            return m.group(1)
    return stem


def parse_root_info(stem: str) -> Tuple[str, str]:
    """返回 (类型, 版本号)。类型为 'SDK' 或 'SDKLite'。"""
    lower = stem.lower()
    typ = "SDKLite" if "sdklite" in lower else "SDK"
    return typ, parse_version(stem)


# ----------------------------------------------------------------------
# 4) 资源读取（流式抽取单个文件）
# ----------------------------------------------------------------------

class Source:
    """统一打开 zip/7z/tar/文件夹，按相对路径提供字节流。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.is_dir = self.path.is_dir()
        self.suffix = "" if self.is_dir else self.path.suffix.lower()
        self._open = None
        if self.is_dir:
            pass
        elif self.suffix == ".zip":
            self._open = _open_zip_any_encoding(self.path)
        elif self.suffix in (".tar", ".tgz", ".tbz", ".tbz2", ".txz"):
            self._open = tarfile.open(self.path, "r:*")
        elif self.suffix == ".7z":
            import py7zr
            self._open = py7zr.SevenZipFile(self.path, mode="r")
        else:
            raise ValueError(f"不支持的格式: {self.suffix}")

    def close(self) -> None:
        if self._open is not None:
            self._open.close()
            self._open = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read(self, relpath: str) -> bytes:
        if self.is_dir:
            return (self.path / relpath).read_bytes()
        if self.suffix == ".zip":
            with self._open.open(relpath) as f:    # type: ignore[union-attr]
                return f.read()
        if self.suffix in (".tar", ".tgz", ".tbz", ".tbz2", ".txz"):
            member = self._open.getmember(relpath)  # type: ignore[union-attr]
            f = self._open.extractfile(member)      # type: ignore[union-attr]
            assert f is not None
            return f.read()
        if self.suffix == ".7z":
            bio = self._open.read([relpath])        # type: ignore[union-attr]
            return bio[relpath].read()
        raise ValueError(relpath)


# ----------------------------------------------------------------------
# 5) 输出归档
# ----------------------------------------------------------------------

def write_zip(out_path: Path, files: List[Tuple[str, bytes]],
              progress: Optional[Callable[[str], None]] = None) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as z:
        for arcname, data in files:
            zi = zipfile.ZipInfo(filename=arcname)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.flag_bits |= 0x800          # 标记文件名 UTF-8 (gp bit 11)
            zi.date_time = (1980, 1, 1, 0, 0, 0)
            z.writestr(zi, data)
            if progress is not None:
                progress(f"打包: {arcname}")


def _open_zip_any_encoding(p: Path) -> zipfile.ZipFile:
    """打开 zip 文件名解码，兼容 utf-8 与 cp936 (GBK) 等编码。"""
    last: Optional[Exception] = None
    for enc in ("utf-8", "cp936", "cp437"):
        try:
            z = zipfile.ZipFile(p, metadata_encoding=enc)
            _ = z.namelist()  # 强制解码
            return z
        except UnicodeDecodeError as e:
            last = e
            continue
    raise last or RuntimeError(f"无法解码 zip 文件名: {p}")


def write_7z(out_path: Path, files: List[Tuple[str, bytes]],
             progress: Optional[Callable[[str], None]] = None) -> None:
    import py7zr
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(out_path, mode="w") as z:
        for arcname, data in files:
            z.writestr(data, arcname)
            if progress is not None:
                progress(f"打包: {arcname}")


def write_tar_gz(out_path: Path, files: List[Tuple[str, bytes]],
                 progress: Optional[Callable[[str], None]] = None) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import io
    with tarfile.open(out_path, "w:gz") as t:
        for arcname, data in files:
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            info.mtime = int(time.time())
            t.addfile(info, io.BytesIO(data))
            if progress is not None:
                progress(f"打包: {arcname}")


def write_folder(out_path: Path, files: List[Tuple[str, bytes]],
                 progress: Optional[Callable[[str], None]] = None) -> None:
    out_path.mkdir(parents=True, exist_ok=True)
    for arcname, data in files:
        dst = out_path / arcname
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        if progress is not None:
            progress(f"复制: {arcname}")


WRITERS = {
    "zip": write_zip,
    "7z":  write_7z,
    "tar.gz": write_tar_gz,
    "folder": write_folder,
}


# ----------------------------------------------------------------------
# 6) 默认输出文件名
# ----------------------------------------------------------------------

def default_output_name(customer: str, sdk_type: str, version: str,
                        date_str: str, fmt: str) -> str:
    customer = customer.strip() or "客户"
    parts = [f"SDK_Release_To_{customer}", sdk_type]
    if version:
        parts.append(version)
    parts += ["release", date_str]
    name = "_".join(parts)
    return f"{name}.{fmt}" if fmt != "folder" else name