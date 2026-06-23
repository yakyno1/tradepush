from __future__ import annotations

import pandas as pd
import streamlit as st

from tradepush.storage.repository import save_analysis
from tradepush.ui.components import (
    cached_snapshot,
    cards,
    current_phase,
    freshness_notice,
    hero,
    conclusion_panel,
    process_steps,
    refresh_snapshot,
    section,
    show_trade_cards,
    usage_note,
)
from tradepush.ui.theme import setup_page

setup_page("今日总览", "◈")
snapshot = cached_snapshot()

hero(
    "今日交易控制台",
    "先判断市场，再判断板块，最后只处理有明确位置和失效位的个股。",
)
freshness_notice(snapshot.data_date)
usage_note(
    "本页如何使用",
    [
        "先看“今日结论”和市场总开关；若为停止新开仓，不因个股评分高而买入。",
        "再看推导过程，确认数据、市场、板块、个股、风控五层是否依次通过。",
        "交易卡只表示计划：到触发价后仍需核对取消条件，并由你人工确认下单。",
        "盘中先运行本地采集，再点刷新；收盘后运行今日分析保存复盘证据。",
    ],
    opened=True,
)

with st.sidebar:
    st.markdown("### ◈ TradePush")
    st.caption("A/H 波段交易执行指示器")
    st.divider()
    st.write(f"**当前阶段**：{current_phase()}")
    st.write(f"**数据日期**：{snapshot.data_date}")
    st.write(f"**生成时间**：{snapshot.generated_at[11:19]}")
    st.divider()
    refresh = st.button("↻ 刷新盘中数据", use_container_width=True)
    run = st.button("▶ 运行今日分析", type="primary", use_container_width=True)
    st.caption("刷新会重新读取本地数据；运行分析会保存CSV、JSON和Markdown结果。")

if refresh:
    snapshot = refresh_snapshot()
    st.toast("已重新读取数据并计算规则", icon="↻")

if run:
    folder = save_analysis(
        snapshot.data_date,
        snapshot.market.to_dict(),
        snapshot.sectors,
        snapshot.decisions,
        snapshot.stock_forecasts,
        snapshot.sector_horizon_forecasts,
    )
    st.success(f"今日分析已保存：{folder}")

action_counts = snapshot.decisions["action"].value_counts() if not snapshot.decisions.empty else pd.Series(dtype=int)
strong_sector = snapshot.sectors.iloc[0]["name"] if not snapshot.sectors.empty else "暂无"
remaining_risk = max(
    float(snapshot.account.get("max_total_pct", 85)) - float(snapshot.portfolio.get("exposure_pct", 0)),
    0,
)
cards(
    [
        {
            "label": "市场总开关",
            "value": f"{snapshot.market.label} · {snapshot.market.score:.0f}",
            "note": "硬门槛优先于个股评分",
            "color": "tp-red" if snapshot.market.label == "进攻" else "tp-amber" if snapshot.market.label == "谨慎" else "tp-green",
        },
        {
            "label": "今日最强方向",
            "value": strong_sector,
            "note": "来自板块资金与价格共振",
            "color": "tp-purple",
        },
        {
            "label": "条件买 / 等待",
            "value": f"{action_counts.get('条件买', 0)} / {action_counts.get('等待', 0)}",
            "note": "候选不等于必须成交",
            "color": "tp-cyan",
        },
        {
            "label": "已用仓位 / 剩余额度",
            "value": f"{snapshot.portfolio.get('exposure_pct', 0):.1f}% / {remaining_risk:.1f}%",
            "note": "账户配置值，需在数据中心确认",
            "color": "tp-amber",
        },
    ]
)

forecast_candidates = snapshot.sector_forecast[
    snapshot.sector_forecast["forecast_state"].isin(["升温候选", "延续候选", "修复观察"])
] if not snapshot.sector_forecast.empty else pd.DataFrame()
forecast_text = "、".join(forecast_candidates.head(3)["name"].astype(str)) if not forecast_candidates.empty else "暂无高质量候选"
buy_count = int(action_counts.get("条件买", 0))
tone = "red" if snapshot.market.label == "进攻" else "amber" if snapshot.market.label == "谨慎" else "green"
conclusion_panel(
    "今日结论",
    f"市场处于“{snapshot.market.label}”，当前有 {buy_count} 个条件买候选；板块前瞻关注 {forecast_text}。",
    [
        f"市场可交易评分 {snapshot.market.score:.0f}/100，数据完整度 {snapshot.market.data_quality:.0f}%。",
        f"账户已用仓位 {snapshot.portfolio.get('exposure_pct', 0):.1f}%，剩余额度 {remaining_risk:.1f}%。",
        "硬门槛、失效位和组合风险优先于排序分数。",
    ],
    "只处理交易卡中的触发条件；未触发、数据过期或板块转弱时继续等待。",
    tone=tone,
)

section("结论是怎样推出来的")
top_sector = snapshot.sectors.iloc[0]["name"] if not snapshot.sectors.empty else "缺少板块数据"
top_stock = snapshot.decisions.iloc[0]["name"] if not snapshot.decisions.empty else "无候选"
process_steps(
    [
        {
            "title": "数据门槛",
            "status": f"{snapshot.market.data_quality:.0f}% 完整",
            "detail": f"行情日期 {snapshot.data_date}；雪球、指数、板块资金和历史K线分别检查。",
            "color": "cyan",
        },
        {
            "title": "市场开关",
            "status": snapshot.market.label,
            "detail": "综合指数涨跌、上涨广度、MA20覆盖与强势板块数量。",
            "color": "red" if snapshot.market.label == "进攻" else "amber",
        },
        {
            "title": "板块轮动",
            "status": str(top_sector),
            "detail": f"当前强度领先；未来1–3日条件候选为 {forecast_text}。",
            "color": "purple",
        },
        {
            "title": "个股硬门槛",
            "status": str(top_stock),
            "detail": "检查核心地位、趋势/安全区、量价承接、收益风险比与明确失效位。",
            "color": "cyan",
        },
        {
            "title": "仓位与执行",
            "status": f"剩余额度 {remaining_risk:.1f}%",
            "detail": "通过单票、主题、总仓位和每笔1R风险限制后，才生成订单草案。",
            "color": "green",
        },
    ]
)

left, right = st.columns([1.45, 1], gap="large")
with left:
    section("最重要的交易指示")
    show_trade_cards(snapshot.decisions, limit=9)
with right:
    section("市场状态拆解")
    for reason in snapshot.market.reasons:
        st.markdown(f"- {reason}")
    st.progress(int(snapshot.market.score), text=f"市场可交易评分 {snapshot.market.score:.0f}/100")
    section("板块状态分布")
    if snapshot.sectors.empty:
        st.info("暂无板块资金流数据。")
    else:
        counts = snapshot.sectors["sector_state"].value_counts()
        for label in ["主线进攻", "轮动观察", "防守方向", "权重护盘", "退潮回避"]:
            st.write(f"**{label}**　{counts.get(label, 0)}")

section("今日决策表")
if snapshot.decisions.empty:
    st.warning("未找到可用股票数据。请到“数据中心与设置”检查来源。")
else:
    from tradepush.ui.components import format_decisions

    st.dataframe(
        format_decisions(snapshot.decisions),
        use_container_width=True,
        hide_index=True,
        height=480,
    )

st.caption("TradePush 只提供规则化交易指示和订单草案；第一版不会连接券商自动下单。")
