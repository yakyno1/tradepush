from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from tradepush.collectors.local import load_prediction_verification
from tradepush.ui.charts import equity_curve
from tradepush.ui.components import cards, hero, section
from tradepush.ui.theme import setup_page

setup_page("复盘诊断", "◎")
hero("复盘与影子验证", "先看动作是否能赚钱，再看方向描述是否相似；旧预测记录只作为校准参考。")

verify, paths = load_prediction_verification()
if verify.empty:
    st.info("尚无历史验证数据。完成每日影子记录后，这里会显示收益、回撤和规则表现。")
    st.stop()

verify["actual_pct_chg"] = pd.to_numeric(verify.get("actual_pct_chg"), errors="coerce")
labels = verify.get("direction_judgment", pd.Series("", index=verify.index)).astype(str)
valid = verify["actual_pct_chg"].dropna()
win_rate = float((valid > 0).mean() * 100) if not valid.empty else 0
expectancy = float(valid.mean()) if not valid.empty else 0
gains = valid[valid > 0].sum()
losses = abs(valid[valid < 0].sum())
profit_factor = float(gains / losses) if losses else 0
curve = (1 + valid.fillna(0) / 100).cumprod()
drawdown = ((curve / curve.cummax()) - 1).min() * 100 if not curve.empty else 0
days = verify.get("verify_date", pd.Series(dtype=str)).astype(str).nunique()

cards(
    [
        {"label": "参考胜率", "value": f"{win_rate:.1f}%", "note": "基于历史预测标的实际涨跌", "color": "tp-cyan"},
        {"label": "平均涨跌", "value": f"{expectancy:+.2f}%", "note": "不是实盘成交收益", "color": "tp-purple"},
        {"label": "Profit Factor", "value": f"{profit_factor:.2f}", "note": "参考口径", "color": "tp-amber"},
        {"label": "最大回撤", "value": f"{drawdown:.1f}%", "note": f"覆盖 {days} 个验证日", "color": "tp-green"},
    ]
)

left, right = st.columns([1.35, 1], gap="large")
with left:
    st.plotly_chart(equity_curve(verify), use_container_width=True)
with right:
    section("严格判定分布")
    counts = labels.value_counts()
    st.dataframe(counts.rename_axis("判定").reset_index(name="数量"), use_container_width=True, hide_index=True)
    section("20日影子门槛")
    checks = [
        ("验证交易日≥20", days >= 20),
        ("Profit Factor≥1.2", profit_factor >= 1.2),
        ("最大回撤≤8%", drawdown >= -8),
        ("数据覆盖≥95%", verify["actual_pct_chg"].notna().mean() >= .95),
    ]
    for label, passed in checks:
        st.write(f"{'✅' if passed else '⏳'} {label}")
    st.caption("正式上线仍需使用TradePush自己的影子订单，而不是旧预测记录。")

section("错误样本")
bad = verify[labels.isin(["错", "大错特错"])].copy()
if bad.empty:
    st.success("当前数据没有严格标记的错误样本。")
else:
    cols = ["verify_date", "code", "name", "rating", "action", "actual_pct_chg", "direction_judgment", "mistake_reason", "model_fix"]
    st.dataframe(bad[[c for c in cols if c in bad]], use_container_width=True, hide_index=True, height=450)
