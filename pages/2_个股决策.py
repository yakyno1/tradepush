from __future__ import annotations

import pandas as pd
import streamlit as st

from tradepush.collectors.local import load_history
from tradepush.ui.charts import candlestick
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

setup_page("个股决策", "⌁")
snapshot = cached_snapshot()
hero("个股决策舱", "评分只负责排序；市场关闭、数据异常、无失效位或收益风险比不足会直接否决。")
freshness_notice(snapshot.data_date)
usage_note(
    "本页如何使用",
    [
        "左侧选择股票；先读最终动作，再看触发价、失效位和目标位。",
        "条件买不等于立即买：现价到达触发区且六项硬门槛仍通过，才可人工确认。",
        "任何硬否决出现时都执行等待或禁止买入，AI不能把它改回买入。",
        "持仓股优先执行止损、减仓和清仓规则，不用新开仓逻辑替代卖出纪律。",
    ],
    opened=True,
)

if snapshot.decisions.empty:
    st.error("暂无个股决策。")
    st.stop()

with st.sidebar:
    market_filter = st.multiselect("市场", ["A", "HK"], default=["A", "HK"])
    action_filter = st.multiselect(
        "动作",
        ["条件买", "加仓", "持有", "等待", "禁止买入", "减仓", "清仓"],
        default=["条件买", "加仓", "持有", "等待", "减仓", "清仓"],
    )
    filtered = snapshot.decisions[
        snapshot.decisions["market"].isin(market_filter)
        & snapshot.decisions["action"].isin(action_filter)
    ]
    options = {
        f"{row['name']} · {row['code']} · {row['action']}": idx
        for idx, row in filtered.iterrows()
    }
    selected_label = st.selectbox("选择股票", list(options) or ["暂无匹配"])

if not options:
    st.info("当前筛选没有股票。")
    st.stop()

decision = snapshot.decisions.loc[options[selected_label]]
history, history_path = load_history(str(decision["code"]), str(decision["name"]))

vetoes = [item for item in str(decision.get("hard_vetoes", "")).split("；") if item]
conclusion_panel(
    "个股结论",
    f"{decision['name']} 当前结论为“{decision['action']}”，交易路径是“{decision['path']}”。",
    [
        f"现价 {decision['current_price']:.2f}，触发 {decision['trigger_price']:.2f}，失效 {decision['stop_price']:.2f}。",
        f"目标 {decision['target_price']:.2f}，收益风险比 {decision['risk_reward']:.2f}。",
        f"硬否决：{'；'.join(vetoes) if vetoes else '无'}。",
    ],
    "只有价格触发、板块未转弱、数据新鲜且仓位校验通过时，才生成订单草案。",
    tone="red" if decision["action"] in {"条件买", "加仓"} else "amber",
)

section("该结论的计算过程")
process_steps(
    [
        {"title": "市场允许", "status": snapshot.market.label, "detail": f"市场评分 {snapshot.market.score:.0f}/100。", "color": "red" if snapshot.market.label == "进攻" else "amber"},
        {"title": "板块与地位", "status": str(decision["sector_state"]), "detail": f"{decision['theme']} · {decision['role']}。", "color": "purple"},
        {"title": "入场路径", "status": str(decision["path"]), "detail": "趋势回踩与安全区低吸分别计算，不满足则否决。", "color": "cyan"},
        {"title": "盈亏结构", "status": f"{decision['risk_reward']:.2f}R", "detail": "先定义失效位，再计算目标和建议股数。", "color": "green"},
        {"title": "最终门槛", "status": "通过" if bool(decision["gate_passed"]) else "未通过", "detail": "硬门槛优先于评分和AI意见。", "color": "green" if bool(decision["gate_passed"]) else "amber"},
    ]
)

cards(
    [
        {"label": "最终动作", "value": decision["action"], "note": decision["path"], "color": "tp-red" if decision["action"] in {"条件买", "加仓"} else "tp-amber"},
        {"label": "板块 / 地位", "value": f"{decision['sector_state']} · {decision['role']}", "note": decision["theme"], "color": "tp-purple"},
        {"label": "触发 / 失效", "value": f"{decision['trigger_price']:.2f} / {decision['stop_price']:.2f}", "note": f"建议 {decision['suggested_weight_pct']:.1f}% · {int(decision['suggested_shares'])}股", "color": "tp-cyan"},
        {"label": "目标 / 盈亏比", "value": f"{decision['target_price']:.2f} / {decision['risk_reward']:.2f}", "note": f"排序分 {decision['score']:.0f}", "color": "tp-red"},
    ]
)

left, right = st.columns([1.65, 1], gap="large")
with left:
    if history.empty:
        st.warning("没有找到该股票的历史K线。")
    else:
        st.plotly_chart(candlestick(history, decision), use_container_width=True)
        st.caption(f"历史来源：{history_path}")
with right:
    section("六项硬门槛")
    gates = [
        ("市场允许", snapshot.market.label != "停止新开仓"),
        ("核心或中军", decision["role"] in {"核心", "中军"}),
        ("板块非退潮", decision["sector_state"] != "退潮回避"),
        ("位置/路径合格", "趋势与安全区均不合格" not in str(decision["hard_vetoes"])),
        ("量价承接", "量能承接不足" not in str(decision["hard_vetoes"])),
        ("盈亏比≥2", float(decision["risk_reward"]) >= 2),
    ]
    for label, passed in gates:
        st.markdown(f"{'✅' if passed else '⛔'} **{label}**")
    section("规则证据")
    for reason in str(decision["reasons"]).split("；"):
        if reason:
            st.write(f"- {reason}")
    if decision["hard_vetoes"]:
        section("硬否决")
        for veto in str(decision["hard_vetoes"]).split("；"):
            if veto:
                st.error(veto)
    else:
        st.success("所有硬门槛通过。仍需等待价格触发并人工确认。")

section("股票池快速对比")
show = snapshot.decisions[
    ["name", "code", "market", "sector_state", "role", "path", "action", "current_price", "trigger_price", "stop_price", "target_price", "risk_reward", "score", "hard_vetoes"]
]
st.dataframe(show, use_container_width=True, hide_index=True, height=420)
