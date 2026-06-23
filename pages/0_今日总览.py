from __future__ import annotations

import pandas as pd
import streamlit as st

from tradepush.services.reconstruction import reconstruct_and_archive
from tradepush.storage.repository import save_analysis
from tradepush.ui.components import (
    auditable_process_steps,
    auditable_cards,
    cached_snapshot,
    cards,
    current_phase,
    freshness_notice,
    hero,
    conclusion_panel,
    refresh_snapshot,
    section,
    show_trade_cards,
    snapshot_context_notice,
    usage_note,
)
from tradepush.time_context import snapshot_kind
from tradepush.ui.overview_evidence import (
    render_overview_evidence,
    render_process_evidence,
    render_stock_evidence,
)
from tradepush.ui.theme import setup_page
from tradepush.ui.history_backfill import render_history_backfill_tools

setup_page("今日总览", "◈")
snapshot = cached_snapshot()

hero(
    "今日交易控制台",
    "先判断市场，再判断板块，最后只处理有明确位置和失效位的个股。",
)
snapshot_context_notice(snapshot)
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

backfill_notice = str(st.session_state.pop("tp_backfill_notice", "") or "")
if backfill_notice:
    st.success(backfill_notice)

section("当前日期数据完整性")
health = snapshot.source_health.copy()
core_sources = ["股票日行情", "市场指数", "板块资金流"]
sector_data_status = ""
if health.empty:
    st.warning("当前版本没有来源级完整性记录。")
else:
    core_health = health[health["source"].astype(str).isin(core_sources)].copy()
    health_map = {
        str(row["source"]): row
        for _, row in core_health.iterrows()
    }
    if "板块资金流" in health_map:
        sector_data_status = str(health_map["板块资金流"].get("status", ""))
    data_items = []
    for source in core_sources:
        row = health_map.get(source)
        status = str(row.get("status", "缺失")) if row is not None else "缺失"
        rows = row.get("rows", 0) if row is not None else 0
        try:
            row_count = int(float(rows))
        except (TypeError, ValueError):
            row_count = 0
        usable = status not in {
            "缺失",
            "历史缺失",
            "缺失/失效",
            "历史部分重建",
        } and row_count > 0
        data_items.append(
            {
                "label": source,
                "value": f"{row_count} · {status}",
                "note": "已纳入当前结论" if usable else "当前结论会降级",
                "color": "tp-cyan" if usable else "tp-amber",
            }
        )
    cards(data_items)
    incomplete_statuses = {"缺失", "历史缺失", "缺失/失效", "历史部分重建"}
    missing_core = [
        source
        for source in core_sources
        if source not in health_map
        or str(health_map[source].get("status", "")) in incomplete_statuses
        or not float(health_map[source].get("rows", 0) or 0)
    ]
    if missing_core:
        st.warning(
            f"数据门槛 {snapshot.market.data_quality:.0f}%；"
            f"仍需完善：{'、'.join(missing_core)}。下方按钮会针对当前日期生成一个新版本。"
        )

if snapshot.snapshot_kind == "reconstructed":
    sector_rows = health[
        health["source"].astype(str).eq("板块资金流")
    ] if not health.empty and "source" in health else pd.DataFrame()
    sector_status = str(sector_rows.iloc[0].get("status", "")) if not sector_rows.empty else "历史缺失"
    needs_backfill = (
        len(snapshot.prices) == 0
        or len(snapshot.indices) == 0
        or len(snapshot.sectors) == 0
        or sector_status in {"历史缺失", "历史部分重建"}
    )
    if sector_status == "历史部分重建":
        button_label = "继续尝试补齐当前日期板块资金"
    elif needs_backfill:
        button_label = "联网补齐当前日期缺失数据"
    else:
        button_label = "重新补算当前日期并保留新版本"
    st.caption(
        "历史补齐会使用当日股票、指数K线和东方财富历史板块涨跌/主力资金；"
        "当日领涨股与当时账户配置无法精确恢复。"
    )
    if st.button(
        button_label,
        type="primary" if needs_backfill else "secondary",
        use_container_width=True,
        key=f"overview_backfill_{snapshot.snapshot_id}",
    ):
        with st.spinner(f"正在完善 {snapshot.data_date} 历史数据…"):
            result = reconstruct_and_archive(
                snapshot.data_date,
                force=True,
                fetch_sector_history=True,
            )
        if result.status == "CREATED":
            st.session_state["tp_pending_snapshot_id"] = result.snapshot_id
            st.session_state["tp_backfill_notice"] = (
                f"{snapshot.data_date} 已生成新版本：{result.stocks}只股票、"
                f"{result.indices}个指数、{result.sectors}个板块。"
            )
            refresh_snapshot()
            st.rerun()
        elif result.status in {"EXISTS", "SKIPPED"}:
            st.info(result.message)
        else:
            st.error(result.message)

render_history_backfill_tools(
    snapshot,
    expanded=snapshot.snapshot_kind == "reconstructed",
)

with st.sidebar:
    st.markdown("### ◈ TradePush")
    st.caption("A/H 波段交易执行指示器")
    st.divider()
    st.write(f"**当前阶段**：{current_phase()}")
    st.write(f"**数据日期**：{snapshot.data_date}")
    st.write(f"**生成时间**：{snapshot.generated_at[11:19]}")
    st.divider()
    viewing_history = bool(snapshot.archive_path)
    refresh = st.button("↻ 刷新实时数据", use_container_width=True, disabled=viewing_history)
    run = st.button("▶ 保存本次分析", type="primary", use_container_width=True, disabled=viewing_history)
    st.caption("刷新会重新读取本地数据；运行分析会保存CSV、JSON和Markdown结果。")
    if viewing_history:
        st.caption("历史快照为只读。切换到“实时最新”后可刷新或保存分析。")

if refresh:
    snapshot = refresh_snapshot()
    st.toast("已重新读取数据并计算规则", icon="↻")

if run:
    kind = snapshot_kind()
    formal = kind == "close"
    folder, record = save_analysis(
        snapshot.data_date,
        snapshot.market.to_dict(),
        snapshot.sectors,
        snapshot.decisions,
        snapshot.stock_forecasts,
        snapshot.sector_horizon_forecasts,
        snapshot=snapshot,
        kind=kind,
        formal=formal,
    )
    version_text = "收盘正式版" if formal else "盘中分析版"
    st.success(f"{version_text}已保存：{folder}")
    if record:
        st.caption("新版本已加入左侧“数据版本”列表；下次页面刷新后即可切换查看。")

action_counts = snapshot.decisions["action"].value_counts() if not snapshot.decisions.empty else pd.Series(dtype=int)
strong_sector = snapshot.sectors.iloc[0]["name"] if not snapshot.sectors.empty else "暂无"
sector_is_partial = sector_data_status == "历史部分重建"
market_score_text = f"{snapshot.market.score:.1f}".rstrip("0").rstrip(".")
account_exposure_limit = float(snapshot.account.get("max_total_pct", 85))
market_exposure_limit = float(snapshot.market.max_exposure_pct)
effective_exposure_limit = min(account_exposure_limit, market_exposure_limit)
remaining_risk = max(
    effective_exposure_limit - float(snapshot.portfolio.get("exposure_pct", 0)),
    0,
)
selected_evidence = auditable_cards(
    [
        {
            "id": "market",
            "evidence_label": "查看市场评分",
            "label": "市场总开关",
            "value": f"{snapshot.market.label} · {market_score_text}",
            "note": "硬门槛优先于个股评分",
            "color": "tp-red" if snapshot.market.label == "进攻" else "tp-amber" if snapshot.market.label == "谨慎" else "tp-green",
        },
        {
            "id": "sector",
            "evidence_label": "查看板块排名",
            "label": "今日最强方向",
            "value": strong_sector,
            "note": "仅按价格强度，资金未完整" if sector_is_partial else "来自板块资金与价格共振",
            "color": "tp-purple",
        },
        {
            "id": "decisions",
            "evidence_label": "查看决策门槛",
            "label": "条件买 / 等待",
            "value": f"{action_counts.get('条件买', 0)} / {action_counts.get('等待', 0)}",
            "note": "候选不等于必须成交",
            "color": "tp-cyan",
        },
        {
            "id": "portfolio",
            "evidence_label": "查看仓位计算",
            "label": "已用仓位 / 剩余额度",
            "value": f"{snapshot.portfolio.get('exposure_pct', 0):.1f}% / {remaining_risk:.1f}%",
            "note": "账户上限与市场上限取较低值",
            "color": "tp-amber",
        },
    ]
)
render_overview_evidence(snapshot, selected_evidence)

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
        f"市场可交易评分 {market_score_text}/100，数据完整度 {snapshot.market.data_quality:.0f}%。",
        f"账户已用仓位 {snapshot.portfolio.get('exposure_pct', 0):.1f}%，剩余额度 {remaining_risk:.1f}%。",
        "硬门槛、失效位和组合风险优先于排序分数。",
    ],
    "只处理交易卡中的触发条件；未触发、数据过期或板块转弱时继续等待。",
    tone=tone,
)

section("结论是怎样推出来的")
top_sector = snapshot.sectors.iloc[0]["name"] if not snapshot.sectors.empty else "缺少板块数据"
top_stock = snapshot.decisions.iloc[0]["name"] if not snapshot.decisions.empty else "无候选"
top_stock_token = (
    f"{snapshot.decisions.iloc[0].get('market', 'A')}|{snapshot.decisions.iloc[0].get('code', '')}"
    if not snapshot.decisions.empty
    else ""
)
selected_process_evidence = auditable_process_steps(
    [
        {
            "id": "data",
            "title": "数据门槛",
            "status": (
                "核心表 3/3 到位"
                if snapshot.market.data_quality >= 99.5
                else f"核心表 {round(snapshot.market.data_quality / 100 * 3):.0f}/3 到位"
            ),
            "detail": f"行情日期 {snapshot.data_date}；核心表存在不等于所有字段100%完整。",
            "color": "cyan",
            "evidence_label": "查看来源状态",
        },
        {
            "id": "market",
            "title": "市场开关",
            "status": snapshot.market.label,
            "detail": "综合指数涨跌、上涨广度、MA20覆盖与强势板块数量。",
            "color": "red" if snapshot.market.label == "进攻" else "amber",
            "evidence_label": "查看评分拆解",
        },
        {
            "id": "sector",
            "title": "板块轮动",
            "status": str(top_sector),
            "detail": (
                f"当前仅按价格强度领先，资金数据不完整；未来1–3日条件候选为 {forecast_text}。"
                if sector_is_partial
                else f"当前价格与资金综合强度领先；未来1–3日条件候选为 {forecast_text}。"
            ),
            "color": "purple",
            "evidence_label": "查看排名与前瞻",
        },
        {
            "id": "stock",
            "title": "个股硬门槛",
            "status": f"排序第一：{top_stock}",
            "detail": "检查核心地位、趋势/安全区、量价承接、收益风险比与明确失效位。",
            "color": "cyan",
            "evidence_label": "查看该股门槛",
        },
        {
            "id": "portfolio",
            "title": "仓位与执行",
            "status": f"剩余额度 {remaining_risk:.1f}%",
            "detail": "通过单票、主题、总仓位和每笔1R风险限制后，才生成订单草案。",
            "color": "green",
            "evidence_label": "查看仓位约束",
        },
    ]
)
render_process_evidence(snapshot, selected_process_evidence, top_stock_token)

left, right = st.columns([1.45, 1], gap="large")
with left:
    section("最重要的交易指示")
    selected_trade_evidence = show_trade_cards(snapshot.decisions, limit=9)
with right:
    section("市场状态拆解")
    for reason in snapshot.market.reasons:
        st.markdown(f"- {reason}")
    st.progress(int(snapshot.market.score), text=f"市场可交易评分 {market_score_text}/100")
    section("板块状态分布")
    if snapshot.sectors.empty:
        st.info("暂无板块资金流数据。")
    else:
        counts = snapshot.sectors["sector_state"].value_counts()
        for label in ["主线进攻", "轮动观察", "防守方向", "权重护盘", "退潮回避"]:
            st.write(f"**{label}**　{counts.get(label, 0)}")

render_stock_evidence(snapshot, selected_trade_evidence)

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
