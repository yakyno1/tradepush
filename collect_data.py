from __future__ import annotations

import argparse

from tradepush.collectors.eastmoney import collect_eastmoney
from tradepush.collectors.history import collect_akshare_history
from tradepush.collectors.pipeline import run_all, run_intraday, save_status
from tradepush.collectors.xueqiu import collect_xueqiu


def main() -> None:
    parser = argparse.ArgumentParser(description="TradePush 本地独立数据采集器")
    parser.add_argument(
        "mode",
        choices=["intraday", "all", "xueqiu", "eastmoney", "history"],
        nargs="?",
        default="intraday",
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
    else:
        status = save_status(collect_akshare_history())
    print(status.to_string(index=False))


if __name__ == "__main__":
    main()
