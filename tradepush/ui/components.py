from __future__ import annotations

from datetime import datetime
from html import escape

import pandas as pd
import streamlit as st

from tradepush.services.dashboard import DashboardSnapshot, build_snapshot
from tradepush.storage.snapshots import (
    find_snapshot,
    latest_formal_close,
    list_snapshot_records,
    load_dashboard_snapshot,
)
from tradepush.time_context import market_phase


@st.cache_data(ttl=300, show_spinner="正在读取行情并计算三层规则…")
def _cached_live_snapshot() -> DashboardSnapshot:
    return build_snapshot()


@st.cache_data(ttl=300, show_spinner="正在读取历史快照…")
def _cached_archived_snapshot(path: str) -> DashboardSnapshot:
    return load_dashboard_snapshot(path)


def cached_snapshot() -> DashboardSnapshot:
    snapshot_id = str(st.session_state.get("tp_snapshot_id", "") or "")
    if snapshot_id:
        record = find_snapshot(snapshot_id)
        if record:
            return _cached_archived_snapshot(record.path)
    return _cached_live_snapshot()


def refresh_snapshot() -> DashboardSnapshot:
    _cached_live_snapshot.clear()
    _cached_archived_snapshot.clear()
    return cached_snapshot()


def current_phase() -> str:
    return market_phase()


def _set_snapshot_selection(data_date: str, snapshot_id: str) -> None:
    st.session_state["tp_snapshot_date"] = data_date
    st.session_state["tp_snapshot_id"] = snapshot_id
    st.session_state[f"tp_snapshot_version_{data_date}"] = snapshot_id


def snapshot_history_selector() -> None:
    records = list_snapshot_records()
    pending_snapshot_id = str(st.session_state.pop("tp_pending_snapshot_id", "") or "")
    if pending_snapshot_id:
        pending_record = next(
            (record for record in records if record.snapshot_id == pending_snapshot_id),
            None,
        )
        if pending_record:
            _set_snapshot_selection(pending_record.data_date, pending_record.snapshot_id)
    st.markdown("### 历史视图")
    if not records:
        st.caption("暂无归档快照，当前展示实时最新数据。")
        st.session_state["tp_snapshot_id"] = ""
        return

    dates = list(dict.fromkeys(record.data_date for record in records))
    date_options = ["实时最新", *dates]
    current_date = st.session_state.get("tp_snapshot_date")
    if current_date not in date_options:
        current_date = dates[0]
    selected_date = st.selectbox(
        "查看日期",
        date_options,
        index=date_options.index(current_date),
        key="tp_snapshot_date",
    )
    if selected_date == "实时最新":
        st.session_state["tp_snapshot_id"] = ""
        st.caption("显示当前本地数据；可能包含尚未归档的设置变化。")
        return

    versions = [record for record in records if record.data_date == selected_date]
    version_ids = [record.snapshot_id for record in versions]
    current_id = st.session_state.get("tp_snapshot_id")
    if current_id not in version_ids:
        current_id = version_ids[0]

    def label(snapshot_id: str) -> str:
        record = next(record for record in versions if record.snapshot_id == snapshot_id)
        time_text = record.generated_at[11:19] if len(record.generated_at) >= 19 else record.generated_at
        suffix = " · 正式" if record.formal else ""
        return f"{record.label}{suffix} · {time_text}"

    selected_id = st.selectbox(
        "数据版本",
        version_ids,
        index=version_ids.index(current_id),
        format_func=label,
        key=f"tp_snapshot_version_{selected_date}",
    )
    st.session_state["tp_snapshot_id"] = selected_id
    selected = next(record for record in versions if record.snapshot_id == selected_id)
    baseline = latest_formal_close(before_or_on=selected.data_date, exclude_date=selected.data_date)
    st.caption(f"当前：{selected.label} · {selected.reason}")
    if selected.kind == "intraday":
        if baseline:
            st.caption(f"正式基准：{baseline.data_date} {baseline.label}")
            st.button(
                "查看上一正式收盘版",
                key=f"tp_open_baseline_{selected.snapshot_id}",
                on_click=_set_snapshot_selection,
                args=(baseline.data_date, baseline.snapshot_id),
            )
        else:
            st.caption("正式基准：暂无更早的收盘正式版")


def snapshot_context_notice(snapshot: DashboardSnapshot) -> None:
    if snapshot.archive_path:
        st.info(
            f"当前查看：{snapshot.data_date} {snapshot.snapshot_label}，"
            f"生成于 {snapshot.generated_at.replace('T', ' ')}。此页读取当时归档结果，不使用未来数据重算。"
        )
    else:
        st.info(f"当前查看：实时最新数据 · {snapshot.data_date}，尚未锁定为历史版本。")
    if snapshot.snapshot_kind == "reconstructed":
        sector_status = ""
        if not snapshot.source_health.empty and "source" in snapshot.source_health:
            sector_rows = snapshot.source_health[
                snapshot.source_health["source"].astype(str).eq("板块资金流")
            ]
            if not sector_rows.empty:
                sector_status = str(sector_rows.iloc[0].get("status", ""))
        if sector_status in {"历史重建", "历史部分重建"}:
            coverage_note = (
                "部分板块主力资金仍缺失；" if sector_status == "历史部分重建" else ""
            )
            st.warning(
                "这是历史重建版：股票、指数、板块涨跌和主力资金均来自历史数据；"
                f"{coverage_note}当日领涨股无法可靠回溯，账户、自选池和规则使用当前版本。"
            )
        else:
            st.warning(
                "这是历史重建版：股票与指数来自当日历史K线；缺失的盘中资金快照不会被事后补造，"
                "账户、自选池和规则使用当前版本。"
            )
    if snapshot.snapshot_kind == "intraday" or (not snapshot.archive_path and current_phase() == "盘中"):
        baseline = latest_formal_close(before_or_on=snapshot.data_date, exclude_date=snapshot.data_date)
        if baseline:
            st.caption(f"上一份正式收盘基准：{baseline.data_date} · {baseline.generated_at[11:19]}")
        else:
            st.caption("上一份正式收盘基准：暂无。首次收盘后运行正式分析后会自动建立。")


def hero(title: str, subtitle: str, kicker: str = "TRADEPUSH · DECISION TERMINAL") -> None:
    st.markdown(
        f"""
        <div class="tp-hero">
          <div class="tp-kicker">{escape(kicker)}</div>
          <div class="tp-title">{escape(title)}</div>
          <div class="tp-subtitle">{escape(subtitle)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section(title: str) -> None:
    st.markdown(f'<div class="tp-section">{escape(title)}</div>', unsafe_allow_html=True)


def usage_note(title: str, steps: list[str], opened: bool = False) -> None:
    with st.expander(f"📘 {title}", expanded=opened):
        for index, step in enumerate(steps, 1):
            st.markdown(f"**{index}.** {step}")


def conclusion_panel(
    title: str,
    conclusion: str,
    reasons: list[str],
    next_action: str,
    tone: str = "cyan",
) -> None:
    reason_html = "".join(f"<li>{escape(str(reason))}</li>" for reason in reasons if reason)
    st.markdown(
        f"""
        <div class="tp-conclusion tp-conclusion-{escape(tone)}">
          <div class="tp-conclusion-title">{escape(title)}</div>
          <div class="tp-conclusion-main">{escape(conclusion)}</div>
          <ul>{reason_html}</ul>
          <div class="tp-conclusion-action">下一步：{escape(next_action)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def process_steps(items: list[dict]) -> None:
    html = ['<div class="tp-process">']
    for index, item in enumerate(items, 1):
        status = str(item.get("status", "观察"))
        color = str(item.get("color", "cyan"))
        html.append(
            '<div class="tp-process-step">'
            f'<div class="tp-process-index tp-dot-{escape(color)}">{index}</div>'
            '<div>'
            f'<div class="tp-process-title">{escape(str(item.get("title", "")))}'
            f'<span class="tp-process-status">{escape(status)}</span></div>'
            f'<div class="tp-process-detail">{escape(str(item.get("detail", "")))}</div>'
            "</div></div>"
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def auditable_process_steps(
    items: list[dict],
    *,
    state_key: str = "tp_process_evidence",
) -> str:
    for index, item in enumerate(items, 1):
        with st.container(border=True):
            index_col, content_col, action_col = st.columns([0.07, 0.7, 0.23])
            with index_col:
                color = escape(str(item.get("color", "cyan")))
                st.markdown(
                    f'<div class="tp-process-index tp-dot-{color}">{index}</div>',
                    unsafe_allow_html=True,
                )
            with content_col:
                st.markdown(
                    f"**{item.get('title', '')}**　"
                    f"<span class='tp-process-status'>{escape(str(item.get('status', '观察')))}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(str(item.get("detail", "")))
            with action_col:
                if st.button(
                    str(item.get("evidence_label", "查看本步数据")),
                    key=f"{state_key}_{item.get('id', index)}",
                    use_container_width=True,
                ):
                    st.session_state[state_key] = str(item.get("id", index))
                    st.session_state["tp_overview_evidence"] = ""
                    st.session_state["tp_trade_evidence"] = ""
                    st.rerun()
    return str(st.session_state.get(state_key, "") or "")


def status_color(label: str) -> str:
    if label in {"进攻", "条件买", "加仓", "主线进攻", "可用", "已配置"}:
        return "tp-red"
    if label in {"谨慎", "等待", "轮动观察", "权重护盘", "待验证"}:
        return "tp-amber"
    if label in {"停止新开仓", "禁止买入", "清仓", "退潮回避", "缺失", "缺失/失效"}:
        return "tp-green"
    return "tp-cyan"


def cards(items: list[dict]) -> None:
    html = ['<div class="tp-grid">']
    for item in items:
        color = item.get("color", "tp-cyan")
        html.append(
            '<div class="tp-card">'
            f'<div class="tp-label">{escape(str(item.get("label", "")))}</div>'
            f'<div class="tp-value {color}">{escape(str(item.get("value", "—")))}</div>'
            f'<div class="tp-note">{escape(str(item.get("note", "")))}</div>'
            "</div>"
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def auditable_cards(
    items: list[dict],
    *,
    state_key: str = "tp_overview_evidence",
) -> str:
    columns = st.columns(len(items))
    for column, item in zip(columns, items):
        with column:
            color = item.get("color", "tp-cyan")
            st.markdown(
                '<div class="tp-card">'
                f'<div class="tp-label">{escape(str(item.get("label", "")))}</div>'
                f'<div class="tp-value {color}">{escape(str(item.get("value", "—")))}</div>'
                f'<div class="tp-note">{escape(str(item.get("note", "")))}</div>'
                "</div>",
                unsafe_allow_html=True,
            )
            if st.button(
                str(item.get("evidence_label", "查看依据与原始数据")),
                key=f"{state_key}_{item.get('id', '')}",
                use_container_width=True,
            ):
                st.session_state[state_key] = str(item.get("id", ""))
                st.session_state["tp_process_evidence"] = ""
                st.session_state["tp_trade_evidence"] = ""
                st.rerun()
    return str(st.session_state.get(state_key, "") or "")


def freshness_notice(data_date: str) -> None:
    parsed = pd.to_datetime(data_date, errors="coerce")
    if pd.isna(parsed):
        st.markdown('<div class="tp-danger">⚠ 无法确认行情日期，禁止生成买入指示。</div>', unsafe_allow_html=True)
        return
    days = (pd.Timestamp.now().normalize() - parsed.normalize()).days
    if days > 4:
        st.markdown(
            f'<div class="tp-warning">◷ 当前展示的是 {escape(data_date)} 数据，距今天 {days} 天。'
            "可用于系统验证，但不应直接作为今日实盘依据。</div>",
            unsafe_allow_html=True,
        )


def action_badge(action: str) -> str:
    icons = {
        "条件买": "↗",
        "加仓": "＋",
        "持有": "◆",
        "等待": "◷",
        "禁止买入": "⊘",
        "减仓": "−",
        "清仓": "×",
    }
    return f"{icons.get(action, '·')} {action}"


def show_trade_cards(
    decisions: pd.DataFrame,
    limit: int = 8,
    *,
    state_key: str = "tp_trade_evidence",
) -> str:
    if decisions.empty:
        st.info("暂无交易决策。")
        return ""
    priority = {"条件买": 0, "加仓": 1, "减仓": 2, "清仓": 3, "持有": 4, "等待": 5, "禁止买入": 6}
    show = decisions.copy()
    show["_order"] = show["action"].map(priority).fillna(99)
    show = show.sort_values(["_order", "score"], ascending=[True, False]).head(limit)
    for _, row in show.iterrows():
        color = status_color(str(row["action"]))
        veto = str(row.get("hard_vetoes", "") or "无硬否决")
        st.markdown(
            f"""
            <div class="tp-trade-card">
              <div style="display:flex;justify-content:space-between;gap:1rem;align-items:center">
                <div><span class="tp-trade-name">{escape(str(row['name']))}</span>
                <span class="tp-trade-meta"> {escape(str(row['code']))} · {escape(str(row['sector_state']))} · {escape(str(row['path']))}</span></div>
                <span class="tp-badge {color}">{escape(action_badge(str(row['action'])))}</span>
              </div>
              <div class="tp-trade-meta">
                现价 {row['current_price']:.2f} · 触发 {row['trigger_price']:.2f} ·
                止损 {row['stop_price']:.2f} · 目标 {row['target_price']:.2f} ·
                建议 {row['suggested_weight_pct']:.1f}% / {int(row['suggested_shares'])}股
              </div>
              <div class="tp-trade-meta">取消/否决：{escape(veto)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        market = str(row.get("market", "A"))
        code = str(row.get("code", ""))
        token = f"{market}|{code}"
        if st.button(
            f"查看 {row['name']} 的计算依据",
            key=f"{state_key}_{market}_{code}",
            use_container_width=True,
        ):
            st.session_state[state_key] = token
            st.session_state["tp_overview_evidence"] = ""
            st.session_state["tp_process_evidence"] = ""
            st.rerun()
    return str(st.session_state.get(state_key, "") or "")


def format_decisions(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["动作"] = out["action"].map(action_badge)
    rename = {
        "name": "股票",
        "code": "代码",
        "sector_state": "板块状态",
        "role": "地位",
        "path": "交易路径",
        "current_price": "现价",
        "trigger_price": "触发价",
        "suggested_weight_pct": "仓位%",
        "suggested_shares": "股数",
        "stop_price": "失效位",
        "target_price": "目标",
        "risk_reward": "盈亏比",
        "score": "排序分",
        "hard_vetoes": "不买原因",
    }
    cols = ["股票", "代码", "板块状态", "地位", "交易路径", "动作", "现价", "触发价", "仓位%", "股数", "失效位", "目标", "盈亏比", "排序分", "不买原因"]
    out = out.rename(columns=rename)
    return out[[c for c in cols if c in out.columns]]
