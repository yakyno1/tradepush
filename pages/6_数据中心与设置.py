from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from tradepush.collectors.common import read_csv_safe
from tradepush.collectors.eastmoney import collect_eastmoney
from tradepush.collectors.history import collect_akshare_history
from tradepush.collectors.local import project_is_self_contained
from tradepush.collectors.pipeline import run_all, run_intraday, save_status
from tradepush.collectors.xueqiu import collect_xueqiu
from tradepush.config import CONFIG_DIR, load_account, save_account
from tradepush.ui.components import cached_snapshot, hero, refresh_snapshot, section, usage_note
from tradepush.ui.theme import setup_page

setup_page("数据中心与设置", "⚙")
snapshot = cached_snapshot()
hero("数据中心与设置", "查看来源健康、编辑账户与本地覆盖配置。Cookie只显示状态，不会在界面暴露内容。")
usage_note(
    "本页如何使用",
    [
        "盘中刷新：更新雪球行情与东方财富板块资金，速度较快。",
        "完整采集：同时更新雪球历史K线；首次运行或历史缺口较大时使用。",
        "AKShare历史：作为A/H股技术指标补充，耗时较长，可在收盘后单独运行。",
        "采集完成后系统只读取本项目 data 目录；Cookie内容不会显示或写入分析包。",
    ],
    opened=True,
)

section("本地独立采集")
st.success("✅ 所有运行时数据路径均位于当前 TradePush 项目内。") if project_is_self_contained() else st.error("检测到项目外数据路径。")
c1, c2, c3, c4 = st.columns(4)
run_fast = c1.button("⚡ 盘中刷新", type="primary", use_container_width=True)
run_full = c2.button("🛰️ 完整采集", use_container_width=True)
run_sector = c3.button("🧭 仅板块资金", use_container_width=True)
run_history = c4.button("📚 AKShare历史", use_container_width=True)
status_result = None
if run_fast:
    with st.spinner("正在采集雪球行情和东方财富板块资金…"):
        status_result = run_intraday()
elif run_full:
    with st.spinner("正在更新行情、板块与雪球历史K线…"):
        status_result = run_all(include_akshare_history=False)
elif run_sector:
    with st.spinner("正在更新东方财富板块资金…"):
        status_result = save_status(collect_eastmoney())
elif run_history:
    with st.spinner("正在用AKShare更新A/H股历史K线，可能需要几分钟…"):
        status_result = save_status(collect_akshare_history())
if status_result is not None:
    st.dataframe(status_result, use_container_width=True, hide_index=True)
    snapshot = refresh_snapshot()
    if (status_result["status"] == "OK").any():
        st.success("采集已完成，页面缓存已刷新。")
    if (status_result["status"] != "OK").any():
        st.warning("部分来源失败，系统保留上一份可用数据；请查看 error 列。")

section("数据来源健康")
health = snapshot.source_health.copy()
health["rows"] = health["rows"].astype(str)
st.dataframe(health, use_container_width=True, hide_index=True)
section("预测覆盖诊断")
stock_coverage = (
    snapshot.stock_forecasts.assign(
        可用=snapshot.stock_forecasts["result"].ne("分析不出结果")
    )
    .groupby("horizon", as_index=False)
    .agg(
        股票总数=("code", "nunique"),
        有效预测=("可用", "sum"),
        平均置信度=("confidence", "mean"),
        平均自信度=("conviction", "mean"),
    )
)
sector_coverage = (
    snapshot.sector_horizon_forecasts.assign(
        可用=snapshot.sector_horizon_forecasts["result"].ne("分析不出结果")
    )
    .groupby("horizon", as_index=False)
    .agg(
        板块总数=("name", "nunique"),
        有效预测=("可用", "sum"),
        平均置信度=("confidence", "mean"),
        平均自信度=("conviction", "mean"),
    )
)
c_stock, c_sector = st.columns(2)
with c_stock:
    st.caption("个股：缺历史或低质量信号会被拒绝")
    st.dataframe(stock_coverage, width="stretch", hide_index=True)
with c_sector:
    st.caption("板块：三个月预测需要至少35个有效快照")
    st.dataframe(sector_coverage, width="stretch", hide_index=True)
if any(health["status"].isin(["缺失", "缺失/失效"])):
    st.warning("存在缺失来源。系统会继续展示可用数据，但相关结论会降权。")

tab1, tab2, tab3, tab4 = st.tabs(["账户风控", "自选池", "安全区", "诊断"])
with tab1:
    account = load_account()
    with st.form("account_form"):
        c1, c2 = st.columns(2)
        with c1:
            equity = st.number_input("账户权益", min_value=0.0, value=float(account["equity"]), step=10_000.0)
            cash = st.number_input("现金", min_value=0.0, value=float(account["cash"]), step=10_000.0)
            risk = st.number_input("单笔风险 %", min_value=0.1, max_value=3.0, value=float(account["risk_per_trade_pct"]), step=0.1)
        with c2:
            max_stock = st.number_input("单票上限 %", min_value=1.0, max_value=50.0, value=float(account["max_stock_pct"]), step=1.0)
            max_theme = st.number_input("主题上限 %", min_value=1.0, max_value=80.0, value=float(account["max_theme_pct"]), step=1.0)
            max_total = st.number_input("总仓位上限 %", min_value=1.0, max_value=100.0, value=float(account["max_total_pct"]), step=1.0)
        confirmed = st.checkbox("我已核对以上账户参数", value=bool(account.get("confirmed")))
        submitted = st.form_submit_button("保存账户设置", type="primary", use_container_width=True)
    if submitted:
        save_account(
            {
                "equity": equity,
                "cash": cash,
                "risk_per_trade_pct": risk,
                "max_stock_pct": max_stock,
                "max_theme_pct": max_theme,
                "max_total_pct": max_total,
                "confirmed": confirmed,
            }
        )
        refresh_snapshot()
        st.success("账户设置已保存。")

with tab2:
    path = CONFIG_DIR / "watchlist.csv"
    df = read_csv_safe(path)
    edited = st.data_editor(df, use_container_width=True, num_rows="dynamic", key="watchlist_editor")
    if st.button("保存自选池", use_container_width=True):
        edited.to_csv(path, index=False, encoding="utf-8-sig")
        refresh_snapshot()
        st.success("自选池已保存。")

with tab3:
    path = CONFIG_DIR / "safety_zones.csv"
    df = read_csv_safe(path)
    edited_zones = st.data_editor(df, use_container_width=True, num_rows="dynamic", key="zones_editor")
    if st.button("保存安全区", use_container_width=True):
        edited_zones.to_csv(path, index=False, encoding="utf-8-sig")
        refresh_snapshot()
        st.success("安全区已保存。")

with tab4:
    section("价格模式纪律")
    st.write("- `raw`：真实买卖价、止损和成交复核。")
    st.write("- `qfq`：均线、趋势、相对强弱和回测。")
    st.write("- `hfq`：仅长期收益展示，不进入交易指示。")
    section("数据异常")
    problems = health[health["status"].isin(["缺失", "缺失/失效"])]
    if problems.empty:
        st.success("没有检测到来源级硬错误。")
    else:
        st.dataframe(problems, use_container_width=True, hide_index=True)
    st.code(json.dumps(snapshot.market.to_dict(), ensure_ascii=False, indent=2), language="json")
