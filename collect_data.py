from __future__ import annotations

import argparse

import pandas as pd

from tradepush.collectors.eastmoney import collect_eastmoney
from tradepush.collectors.history import collect_akshare_history
from tradepush.collectors.pipeline import run_all, run_intraday, save_status
from tradepush.collectors.xueqiu import collect_xueqiu
from tradepush.services.reconstruction import reconstruct_and_archive, reconstruct_range


def main() -> None:
    parser = argparse.ArgumentParser(description="TradePush 本地独立数据采集器")
    parser.add_argument(
        "mode",
        choices=[
            "intraday",
            "all",
            "xueqiu",
            "eastmoney",
            "history",
            "reconstruct",
            "reconstruct-range",
        ],
        nargs="?",
        default="intraday",
    )
    parser.add_argument("date", nargs="?", help="reconstruct 模式的日期，格式 YYYY-MM-DD")
    parser.add_argument("--start", help="reconstruct-range 起始日期，格式 YYYY-MM-DD")
    parser.add_argument("--end", help="reconstruct-range 结束日期，格式 YYYY-MM-DD")
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使已有重建版也生成新版本；旧版本不会删除",
    )
    parser.add_argument(
        "--with-sector-history",
        action="store_true",
        help="历史补录时联网恢复东方财富板块涨跌和主力资金；当日领涨股无法回溯",
    )
    parser.add_argument("--with-akshare", action="store_true", help="all 模式同时更新 AKShare 历史K线")
    args = parser.parse_args()

    if args.mode == "intraday":
        status = run_intraday()
    elif args.mode == "all":
        status = run_all(include_akshare_history=args.with_akshare)
    elif args.mode == "xueqiu":
        status = save_status(collect_xueqiu(collect_history=True))
    elif args.mode == "eastmoney":
        status = save_status(collect_eastmoney())
    elif args.mode == "history":
        status = save_status(collect_akshare_history())
    elif args.mode == "reconstruct":
        if not args.date:
            parser.error("reconstruct 模式需要日期，例如：collect_data.py reconstruct 2026-06-22")
        status = reconstruct_and_archive(
            args.date,
            force=args.force,
            fetch_sector_history=args.with_sector_history,
        ).to_dict()
        print(pd.DataFrame([status]).to_string(index=False))
        return
    else:
        if not args.start or not args.end:
            parser.error("reconstruct-range 模式需要 --start 和 --end")
        status = reconstruct_range(
            args.start,
            args.end,
            force=args.force,
            fetch_sector_history=args.with_sector_history,
        )
    print(status.to_string(index=False))


if __name__ == "__main__":
    main()
