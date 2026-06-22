from __future__ import annotations

import pandas as pd
import streamlit as st

from tradepush.collectors.common import read_csv_safe
from tradepush.config import CONFIG_DIR
from tradepush.ui.charts import sector_heatmap, sector_ranking
from tradepush.ui.components import (
    cached_snapshot,
    cards,
    conclusion_panel,
    freshness_notice,
    hero,
    process_steps,
    section,
    usage_note,
)
from tradepush.ui.theme import setup_page

setup_page("板块轮动", "▦")
snapshot = cached_snapshot()
hero("板块轮动雷达", "识别主线、轮动、护盘与退潮；传导关系只有获得价格和资金验证后才升级。")
freshness_notice(snapshot.data_date)
usage_note(
    "本页如何使用",
    [
        "“当前强度”回答今天谁最强；“条件前瞻”回答未来1–3个交易日谁可能升温或延续。",
        "优先观察升温候选和延续候选，但必须等确认条件发生，不能只凭预测追涨。",
        "修复观察表示资金可能先行、价格尚未确认；转弱风险默认回避。",
        "预测分数用于排序，不会覆盖市场开关、个股失效位和仓位限制。",
    ],
    opened=True,
)

sectors = snapshot.sectors
if sectors.empty:
    st.error("没有找到板块资金流数据。")
    st.stop()

counts = sectors["sector_state"].value_counts()
forecast = snapshot.sector_forecast
actionable = forecast[forecast["forecast_state"].isin(["升温候选", "延续候选", "修复观察"])] if not forecast.empty else pd.DataFrame()
top_names = "、".join(actionable.head(3)["name"].astype(str)) if not actionable.empty else "暂无"
low_confidence = bool(not forecast.empty and forecast["confidence"].astype(str).str.contains("低").all())
conclusion_panel(
    "板块结论",
    f"当前最强为 {sectors.iloc[0]['name']}；未来1–3个交易日优先验证：{top_names}。",
    [
        "前瞻同时使用当日涨幅、净流入、领涨结构和本地历史快照出现频率。",
        "只有下一交易日资金与价格继续确认，候选才升级为可交易主线。",
        "当前历史样本不足，置信度偏低。" if low_confidence else "已有多日快照辅助判断持续性。",
    ],
    "盘中查看候选板块是否满足“确认条件”；触发失效条件则降级或回避。",
    tone="purple",
)

section("板块前瞻推导过程")
process_steps(
    [
        {"title": "当日价格强度", "status": "已计算", "detail": "比较板块涨跌幅、强弱排名和是否过热。", "color": "red"},
        {"title": "资金确认", "status": "已计算", "detail": "净流入为正才有资格进入升温/延续候选。", "color": "cyan"},
        {"title": "领涨结构", "status": "已计算", "detail": "观察领涨股响应，避免只有指数权重拉动。", "color": "purple"},
        {"title": "历史持续性", "status": f"{min(len(forecast), 6)} 档输出", "detail": "统计本地最近快照中的出现次数、上涨率和流入率。", "color": "amber"},
        {"title": "确认与失效", "status": "等待盘中验证", "detail": "预测不会直接生成买单，确认后仍需进入个股硬门槛。", "color": "green"},
    ]
)
cards(
    [
        {"label": "主线进攻", "value": counts.get("主线进攻", 0), "note": "价格与资金共振", "color": "tp-red"},
        {"label": "轮动观察", "value": counts.get("轮动观察", 0), "note": "等待持续性验证", "color": "tp-amber"},
        {"label": "权重护盘", "value": counts.get("权重护盘", 0), "note": "指数红不等于普涨", "color": "tp-purple"},
        {"label": "退潮回避", "value": counts.get("退潮回避", 0), "note": "默认不做后排", "color": "tp-green"},
    ]
)

tab0, tab1, tab2, tab3 = st.tabs(["未来1–3日条件前瞻", "强度地图", "排行与资金", "传导验证"])
with tab0:
    if forecast.empty:
        st.info("暂无可计算的板块前瞻。")
    else:
        display = forecast.rename(
            columns={
                "forecast_rank": "排名",
                "name": "板块",
                "forecast_state": "前瞻状态",
                "forecast_score": "前瞻分",
                "confidence": "置信度",
                "current_state": "当前状态",
                "pct_chg": "当日涨跌%",
                "net_amount": "净流入(亿)",
                "history_hits": "历史出现",
                "why": "推导理由",
                "confirmation": "升级确认条件",
                "invalidation": "失效条件",
                "horizon": "观察周期",
            }
        )
        st.dataframe(display, use_container_width=True, hide_index=True, height=560)
        st.caption("这是条件前瞻而非确定预测：确认条件未发生时，结论保持“等待”。")
with tab1:
    st.plotly_chart(sector_heatmap(sectors), use_container_width=True)
with tab2:
    c1, c2 = st.columns([1.15, 1])
    with c1:
        st.plotly_chart(sector_ranking(sectors), use_container_width=True)
    with c2:
        section("板块明细")
        show_cols = [
            "name", "sector_state", "pct_chg", "net_amount", "amount",
            "leader", "leader_pct", "transmission_status",
        ]
        st.dataframe(sectors[[c for c in show_cols if c in sectors]], use_container_width=True, hide_index=True, height=520)
with tab3:
    mapping = read_csv_safe(CONFIG_DIR / "sector_transmission.csv")
    if mapping.empty:
        st.info("暂无板块传导配置。")
    else:
        active_names = set(sectors.loc[sectors["sector_state"] == "主线进攻", "name"].astype(str))
        mapping["当前验证"] = mapping["trigger_sector"].apply(
            lambda x: "待验证：需出现资金与核心股响应" if not any(str(x) in name or name in str(x) for name in active_names) else "触发端已强，继续验证传导端"
        )
        st.dataframe(mapping, use_container_width=True, hide_index=True)
        st.caption("传导表只生成观察候选，不直接产生个股买入动作。")
