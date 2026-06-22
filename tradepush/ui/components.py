from __future__ import annotations

from datetime import datetime
from html import escape

import pandas as pd
import streamlit as st

from tradepush.services.dashboard import DashboardSnapshot, build_snapshot


@st.cache_data(ttl=300, show_spinner="正在读取行情并计算三层规则…")
def cached_snapshot() -> DashboardSnapshot:
    return build_snapshot()


def refresh_snapshot() -> DashboardSnapshot:
    cached_snapshot.clear()
    return cached_snapshot()


def current_phase() -> str:
    now = datetime.now()
    hhmm = now.hour * 100 + now.minute
    if hhmm < 925:
        return "盘前"
    if 925 <= hhmm <= 1610:
        return "盘中"
    return "收盘后"


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


def show_trade_cards(decisions: pd.DataFrame, limit: int = 8) -> None:
    if decisions.empty:
        st.info("暂无交易决策。")
        return
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
