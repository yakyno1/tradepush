from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from tradepush.services.reconstruction import reconstruct_and_archive
from tradepush.storage.repository import save_analysis
from tradepush.ui.components import (
    auditable_cards,
    cached_snapshot,
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
    render_stock_evidence,
)
from tradepush.ui.theme import setup_page
from tradepush.ui.history_backfill import render_history_backfill_tools

setup_page("今日总览", "◈")
snapshot = cached_snapshot()

# ── header ──────────────────────────────────────────────
hero(
    "今日交易控制台",
    "先判断市场，再判断板块，最后只处理有明确位置和失效位的个股。",
)
snapshot_context_notice(snapshot)
freshness_notice(snapshot.data_date)

# ── 1. 阻塞项汇总 ─────────────────────────────────────────
blockers: list[str] = []
if snapshot.market.label == "停止新开仓":
    blockers.append(f"市场开关「停止新开仓」({snapshot.market.score:.0f}分) → 禁止一切新开仓。")
days_stale = 0
parsed = pd.to_datetime(snapshot.data_date, errors="coerce")
if pd.notna(parsed):
    days_stale = (pd.Timestamp.now().normalize() - parsed.normalize()).days
if days_stale > 2:
    blockers.append(f"数据日期 {snapshot.data_date}，距今 {days_stale} 天 → 仅用于复盘验证，不应用于实盘。")
# Check sector fund flow freshness
health = snapshot.source_health.copy()
if not health.empty and "source" in health:
    sector_rows = health[health["source"].astype(str).eq("板块资金流")]
    if not sector_rows.empty:
        sector_status = str(sector_rows.iloc[0].get("status", ""))
        if sector_status in {"历史部分重建", "历史缺失"}:
            blockers.append(f"板块资金流：{sector_status} → 板块结论降级，部分个股被硬否决。")

if blockers:
    with st.container(border=True):
        st.markdown("### ⚠ 当前阻塞项")
        for b in blockers:
            st.error(b)

# ── 2. 指数迷你条 ─────────────────────────────────────────
indices = snapshot.indices.copy()
if not indices.empty:
    index_cols = st.columns(min(len(indices), 7))
    for i, (_, row) in enumerate(indices.iterrows()):
        if i >= 7:
            break
        pct = float(row.get("pct_chg", 0) or 0)
        ma20_val = float(row.get("ma20", 0) or 0)
        close_val = float(row.get("close", 0) or 0)
        above_ma20 = close_val >= ma20_val if ma20_val else None
        arrow = "▲" if pct > 0 else "▼" if pct < 0 else "─"
        color = "#ef4444" if pct > 0 else "#22c55e" if pct < 0 else "#9ca3af"
        ma_note = "·MA20" if above_ma20 else "·破MA20" if above_ma20 is False else ""
        source = str(row.get("source_used", ""))
        if "RECONSTRUCTION" in source:
            price_tag = "收盘价"
        elif "XUEQIU" in source:
            price_tag = "实时/收盘"
        else:
            price_tag = source[:6] if source else ""
        with index_cols[i]:
            st.markdown(
                f"<div style='text-align:center;line-height:1.4'>"
                f"<div style='font-size:0.7rem;color:#9ca3af'>{row.get('name','')[:4]}</div>"
                f"<div style='font-size:1.1rem;font-weight:700;color:{color}'>{arrow}{abs(pct):.1f}%</div>"
                f"<div style='font-size:0.65rem;color:#6b7280'>{close_val:.0f}{ma_note}</div>"
                f"<div style='font-size:0.55rem;color:#4b5563'>{price_tag}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    st.divider()

usage_note(
    "本页如何使用",
    [
        "先看顶部阻塞项和「今日结论」；有阻塞项时不应买入，无论个股评分多高。",
        "四张摘要卡点击「查看」可展开完整评分拆解、原始数据和数据来源。",
        "交易卡只表示计划：到触发价后仍需核对取消条件，并由你人工确认下单。",
        "盘中先运行本地采集，再点刷新；收盘后运行今日分析保存复盘证据。",
    ],
    opened=False,
)

# ── 3. 数据完整性 + 补录 ──────────────────────────────────
section("当前日期数据完整性")
core_sources = ["股票日行情", "市场指数", "板块资金流"]
sector_data_status = ""
if health.empty:
    st.warning("当前版本没有来源级完整性记录。")
else:
    core_health = health[health["source"].astype(str).isin(core_sources)].copy()
    health_map = {str(row["source"]): row for _, row in core_health.iterrows()}
    if "板块资金流" in health_map:
        sector_data_status = str(health_map["板块资金流"].get("status", ""))
    data_items = []
    for source in core_sources:
        row = health_map.get(source)
        status = str(row.get("status", "缺失")) if row is not None else "缺失"
        rows_val = row.get("rows", 0) if row is not None else 0
        try:
            row_count = int(float(rows_val))
        except (TypeError, ValueError):
            row_count = 0
        usable = status not in {
            "缺失", "历史缺失", "缺失/失效", "历史部分重建",
        } and row_count > 0
        data_items.append({
            "label": source,
            "value": f"{row_count} · {status}",
            "note": "已纳入当前结论" if usable else "当前结论会降级",
            "color": "tp-cyan" if usable else "tp-amber",
        })
    # Use simple cards (no evidence buttons needed for data completeness)
    cols = st.columns(len(data_items))
    for col, item in zip(cols, data_items):
        with col:
            st.markdown(
                f'<div class="tp-card">'
                f'<div class="tp-label">{item["label"]}</div>'
                f'<div class="tp-value {item["color"]}">{item["value"]}</div>'
                f'<div class="tp-note">{item["note"]}</div>'
                f"</div>",
                unsafe_allow_html=True,
            )
    incomplete_statuses = {"缺失", "历史缺失", "缺失/失效"}
    missing_core = [
        source for source in core_sources
        if source not in health_map
        or str(health_map[source].get("status", "")) in incomplete_statuses
        or not float(health_map[source].get("rows", 0) or 0)
    ]
    if missing_core:
        st.warning(
            f"数据门槛 {snapshot.market.data_quality:.0f}%；"
            f"仍需完善：{'、'.join(missing_core)}。"
        )
    # "历史部分重建" means sector data exists but fund flow coverage is low —
    # the historical API gave what it could. Don't flag as actionable.
    partial = [
        source for source in core_sources
        if source in health_map
        and str(health_map[source].get("status", "")) == "历史部分重建"
    ]
    if partial:
        # Show coverage info inline with each card instead of a separate warning
        for source in partial:
            row = health_map[source]
            detail = str(row.get("detail", ""))
            # Extract coverage percentage if present
            cov_match = re.search(r"覆盖(\d+)%", detail)
            cov_text = f"（资金覆盖{cov_match.group(1)}%）" if cov_match else "（部分资金缺失）"
            st.caption(f"📌 {source}：历史已重建{cov_text}，东财历史接口已尽最大努力。不视为阻塞。")

backfill_notice = str(st.session_state.pop("tp_backfill_notice", "") or "")
if backfill_notice:
    st.success(backfill_notice)

if snapshot.snapshot_kind == "reconstructed":
    sector_rows = health[health["source"].astype(str).eq("板块资金流")] if not health.empty and "source" in health else pd.DataFrame()
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
                snapshot.data_date, force=True, fetch_sector_history=True,
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

render_history_backfill_tools(snapshot, expanded=snapshot.snapshot_kind == "reconstructed")

# ── sidebar ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ◈ TradePush")
    st.caption("A/H 波段交易执行指示器")
    st.divider()
    st.write(f"**当前阶段**：{current_phase()}")
    st.write(f"**数据日期**：{snapshot.data_date}")
    st.write(f"**生成时间**：{snapshot.generated_at[11:19]}")
    st.divider()
    viewing_history = bool(snapshot.archive_path)
    phase = current_phase()

    if viewing_history:
        # ── 历史日期 ──
        st.caption(f"📅 {snapshot.data_date} · {snapshot.snapshot_label}")
        if st.button("🔧 重建/补齐此日数据", type="primary", use_container_width=True,
                     help="从本地K线+东方财富历史接口重建该日完整数据"):
            with st.spinner(f"正在重建 {snapshot.data_date} 数据…"):
                from tradepush.services.reconstruction import reconstruct_and_archive
                result = reconstruct_and_archive(snapshot.data_date, force=True, fetch_sector_history=True)
            if result.status == "CREATED":
                st.session_state["tp_pending_snapshot_id"] = result.snapshot_id
                st.toast(f"已生成新版本：{result.stocks}只股票", icon="🔧")
                refresh_snapshot()
                st.rerun()
            else:
                st.warning(result.message)
        if st.button("↻ 切回实时最新", use_container_width=True):
            st.session_state["tp_snapshot_id"] = ""
            st.session_state["tp_snapshot_date"] = "实时最新"
            st.rerun()
        st.caption("历史快照只读。上方「重建」会生成新版而不覆盖旧版。")
    elif phase == "盘中":
        # ── 实时 · 盘中 ──
        if st.button("↻ 盘中采集并刷新", type="primary", use_container_width=True,
                     help="从雪球拉最新行情+东财板块资金，刷新页面"):
            with st.spinner("正在采集雪球行情和东方财富板块资金…"):
                from tradepush.collectors.pipeline import run_intraday
                run_intraday()
            snapshot = refresh_snapshot()
            st.toast("采集完成", icon="↻")
            st.rerun()
        if st.button("▶ 保存盘中分析", use_container_width=True):
            kind = snapshot_kind()
            folder, record = save_analysis(
                snapshot.data_date, snapshot.market.to_dict(),
                snapshot.sectors, snapshot.decisions,
                snapshot.stock_forecasts, snapshot.sector_horizon_forecasts,
                snapshot=snapshot, kind=kind, formal=False,
            )
            st.toast(f"盘中分析已保存：{folder}", icon="▶")
        st.caption("盘中采集实时行情。收盘后再运行正式版。")
    else:
        # ── 实时 · 收盘后 ──
        if st.button("📊 收盘采集并保存正式版", type="primary", use_container_width=True,
                     help="完整采集+计算规则+保存为正式收盘版"):
            with st.spinner("正在采集收盘行情并保存正式版…"):
                from tradepush.collectors.pipeline import run_all
                run_all(include_akshare_history=False)
            snapshot = refresh_snapshot()
            kind = snapshot_kind()
            folder, record = save_analysis(
                snapshot.data_date, snapshot.market.to_dict(),
                snapshot.sectors, snapshot.decisions,
                snapshot.stock_forecasts, snapshot.sector_horizon_forecasts,
                snapshot=snapshot, kind=kind, formal=True,
            )
            st.toast(f"收盘正式版已保存", icon="📊")
            st.rerun()
        if st.button("↻ 盘中采集并刷新", use_container_width=True,
                     help="仅刷新行情（不保存正式版）"):
            with st.spinner("正在采集雪球行情和东方财富板块资金…"):
                from tradepush.collectors.pipeline import run_intraday
                run_intraday()
            snapshot = refresh_snapshot()
            st.toast("采集完成", icon="↻")
            st.rerun()
        st.caption("收盘后建议用正式版归档，盘中刷新仅供临时查看。")

# ── 4. 摘要三卡 ───────────────────────────────────────────
action_counts = snapshot.decisions["action"].value_counts() if not snapshot.decisions.empty else pd.Series(dtype=int)
strong_sector = snapshot.sectors.iloc[0]["name"] if not snapshot.sectors.empty else "暂无"
sector_is_partial = sector_data_status == "历史部分重建"
market_score_text = f"{snapshot.market.score:.1f}".rstrip("0").rstrip(".")

selected_evidence = auditable_cards([
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
        "note": "仅按价格强度，资金未完整" if sector_is_partial else "板块资金与价格共振",
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
])

# ── 展开证据（紧贴四卡下方） ──
render_overview_evidence(snapshot, selected_evidence)

# ── 5. 今日结论（紧贴四卡） ──────────────────────────────────
forecast_candidates = snapshot.sector_forecast[
    snapshot.sector_forecast["forecast_state"].isin(["升温候选", "延续候选", "修复观察"])
] if not snapshot.sector_forecast.empty else pd.DataFrame()
forecast_text = "、".join(forecast_candidates.head(3)["name"].astype(str)) if not forecast_candidates.empty else "暂无高质量候选"
buy_count = int(action_counts.get("条件买", 0))
tone = "red" if snapshot.market.label == "进攻" else "amber" if snapshot.market.label == "谨慎" else "green"

conclusion_panel(
    "今日结论",
    f"市场「{snapshot.market.label}」{market_score_text}分，{buy_count} 个条件买候选；板块前瞻关注 {forecast_text}。",
    [
        f"数据完整度 {snapshot.market.data_quality:.0f}%。",
        f"硬否决 {len(blockers)} 项{'（见顶部阻塞提示）' if blockers else '：无'}。",
        "硬门槛、失效位和组合风险优先于排序分数。",
    ],
    "只处理交易卡中的触发条件；未触发、数据过期或板块转弱时继续等待。",
    tone=tone,
)

# ── 7. 交易卡 ────────────────────────────────────────────
left, right = st.columns([1.45, 1], gap="large")
with left:
    section("最重要的交易指示")
    selected_trade_evidence = show_trade_cards(snapshot.decisions, limit=9)
    # 个股证据紧贴交易卡下方展开
    render_stock_evidence(snapshot, selected_trade_evidence)
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

# ── 8. 决策表（带筛选） ────────────────────────────────────
section("今日决策表")
if snapshot.decisions.empty:
    st.warning("未找到可用股票数据。请到「数据中心与设置」检查来源。")
else:
    from tradepush.ui.components import format_decisions

    all_actions = ["全部"] + sorted(snapshot.decisions["action"].astype(str).unique().tolist())
    action_filter = st.selectbox(
        "按动作筛选",
        all_actions,
        index=0,
        key="decision_action_filter",
        label_visibility="collapsed",
    )
    show = snapshot.decisions.copy()
    if action_filter != "全部":
        show = show[show["action"].astype(str).eq(action_filter)]

    st.dataframe(
        format_decisions(show),
        use_container_width=True,
        hide_index=True,
        height=480,
    )
    if action_filter != "全部":
        st.caption(f"共 {len(show)} 只 · 筛选自 {action_filter}")

st.caption("TradePush 只提供规则化交易指示和订单草案；不会连接券商自动下单。")
