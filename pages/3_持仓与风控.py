from __future__ import annotations

import pandas as pd
import streamlit as st

from tradepush.ui.components import cached_snapshot, cards, freshness_notice, hero, section
from tradepush.ui.theme import setup_page

setup_page("持仓与风控", "◉")
snapshot = cached_snapshot()
hero("持仓与风险预算", "仓位由止损距离和账户风险预算计算，不由主观信心或AI语气决定。")
freshness_notice(snapshot.data_date)

portfolio = snapshot.portfolio
account = snapshot.account
remaining = max(float(account["max_total_pct"]) - float(portfolio["exposure_pct"]), 0)
cards(
    [
        {"label": "账户权益", "value": f"¥{float(account['equity']):,.0f}", "note": "配置值", "color": "tp-cyan"},
        {"label": "现金", "value": f"¥{float(account['cash']):,.0f}", "note": f"{portfolio['cash_pct']:.1f}% 权益", "color": "tp-purple"},
        {"label": "总仓位", "value": f"{portfolio['exposure_pct']:.1f}%", "note": f"上限 {account['max_total_pct']:.0f}%", "color": "tp-amber"},
        {"label": "剩余仓位额度", "value": f"{remaining:.1f}%", "note": f"单笔风险 {account['risk_per_trade_pct']:.1f}%", "color": "tp-green"},
    ]
)

if not account.get("confirmed"):
    st.warning("账户参数尚未确认。请到“数据中心与设置”核对权益、现金和风险上限。")

left, right = st.columns([1.35, 1], gap="large")
with left:
    section("当前持仓")
    holdings = pd.DataFrame(portfolio["rows"])
    if holdings.empty:
        st.info("当前没有导入持仓。系统仍可生成空仓候选。")
    else:
        st.dataframe(holdings, use_container_width=True, hide_index=True, height=380)
with right:
    section("主题暴露")
    if not portfolio["theme_values"]:
        st.info("没有主题暴露。")
    else:
        theme_df = pd.DataFrame(
            [{"theme": k, "market_value": v, "weight_pct": v / float(account["equity"]) * 100} for k, v in portfolio["theme_values"].items()]
        ).sort_values("market_value", ascending=False)
        st.dataframe(theme_df, use_container_width=True, hide_index=True)
        violations = theme_df[theme_df["weight_pct"] > float(account["max_theme_pct"])]
        if not violations.empty:
            st.error("存在主题仓位超过35%的风险。")

section("订单草案")
orders = snapshot.decisions[snapshot.decisions["action"].isin(["条件买", "加仓"])][
    ["code", "name", "market", "theme", "action", "trigger_price", "suggested_weight_pct", "suggested_shares", "stop_price", "target_price", "risk_reward"]
]
if orders.empty:
    st.info("当前没有通过硬门槛的订单草案。")
else:
    st.dataframe(orders, use_container_width=True, hide_index=True)
    st.caption("A股股数已按100股向下取整；港股第一版默认100股一手，实际执行前必须在设置中核对每手股数。")

