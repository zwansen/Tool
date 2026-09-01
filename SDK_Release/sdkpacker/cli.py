"""命令行原型：不启动 GUI，直接发布一个资料包。"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

# 允许脚本式运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SDK Release - command line interface")
    ap.add_argument("--source", required=True, help="原始 SDK 包 (.zip/.7z/.tar/文件夹)")
    ap.add_argument("-o", "--out", required=True, help="输出目录")
    ap.add_argument("--customer", required=True, help="客户名")
    ap.add_argument("--sdk-type", choices=["SDK", "SDKLite"], default="SDK")
    ap.add_argument("--version", default="", help="版本号，留空自动")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--format", choices=["zip", "7z", "tar.gz", "folder"],
                    default="zip")
    ap.add_argument("--wm", default="SDK_Release_To_{customer} · {date}", help="watermark text")
    ap.add_argument("--wm-opacity", type=float, default=0.18)
    ap.add_argument("--wm-angle", type=float, default=45.0)
    ap.add_argument("--wm-size", type=int, default=72, help="水印字号(pt)")
    ap.add_argument("--wm-gap", type=float, default=1.5, help="排列疏密")
    ap.add_argument("--pdf-mode", default="both",
                    choices=["both", "watermark", "encrypt", "none"],
                    help="PDF 默认处理方式")
    ap.add_argument("--encrypt", action="store_true")
    ap.add_argument("--owner-pw", default="KaixinOwner2026!")
    ap.add_argument("--user-pw", default="")
    ap.add_argument("--allow-print",  dest="ap", action="store_true",  default=True)
    ap.add_argument("--no-print",     dest="ap", action="store_false")
    ap.add_argument("--allow-copy",   dest="ac", action="store_true",  default=False)
    ap.add_argument("--no-copy",      dest="ac", action="store_false")
    ap.add_argument("--allow-modify", dest="am", action="store_true",  default=False)
    ap.add_argument("--no-modify",    dest="am", action="store_false")
    args = ap.parse_args(argv)

    # 版本号兜底
    from sdkpacker.core import parse_root_info
    from sdkpacker.pipeline import ProcessParams, run
    src_stem = Path(args.source).stem
    auto_type, auto_ver = parse_root_info(src_stem)
    version = args.version or auto_ver
    sdk_type = args.sdk_type
    if sdk_type is None:
        sdk_type = auto_type

    wm = args.wm.format(**{
        "customer": args.customer,
        "日期": args.date,
        "date": args.date,
    })
    # CLI 的 --encrypt 只是把默认方式覆盖为"仅加密"的快捷写法
    pdf_mode = "encrypt" if args.encrypt else args.pdf_mode

    p = ProcessParams(
        source_path=args.source,
        output_dir=args.out,
        output_format=args.format,
        customer=args.customer,
        sdk_type=sdk_type,
        version=version,
        date_str=args.date,
        default_pdf_mode=pdf_mode,
        owner_pw=args.owner_pw,
        user_pw=args.user_pw,
        allow_print=args.ap,
        allow_copy=args.ac,
        allow_modify=args.am,
        watermark_text=wm,
        watermark_opacity=args.wm_opacity,
        watermark_angle=args.wm_angle,
        watermark_fontsize=args.wm_size,
        watermark_density=args.wm_gap,
        progress_cb=lambda m, f: print(f"[{f:.3f}] {m}" if f >= 0 else f"      {m}"),
        cancelled_cb=lambda: False,
    )

    r = run(p)
    print(f"\nDONE: {r.output_path}\nfiles={r.total_files}, pdf={r.pdf_files}, "
          f"watermarked={r.watermarked}, encrypted={r.encrypted}, "
          f"bytes {r.bytes_in}->{r.bytes_out}, elapsed={r.elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())