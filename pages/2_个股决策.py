from __future__ import annotations

import pandas as pd
import streamlit as st

from tradepush.collectors.local import load_history
from tradepush.features.forecasting import parse_factor_details
from tradepush.ui.charts import (
    candlestick,
    factor_contribution_chart,
    forecast_confidence_chart,
    forecast_range_chart,
)
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

setup_page("个股决策", "◉")
snapshot = cached_snapshot()
hero(
    "个股决策与预测审计",
    "点击股票查看原始数据、计算因子、历史相似样本和一周/一个月/三个月预测。低质量预测直接拒绝输出。",
)
freshness_notice(snapshot.data_date)
usage_note(
    "本页如何使用",
    [
        "在股票表中点击任意一行，页面会切换到该股票的完整分析。",
        "先看交易动作，再看三个预测周期；“分析不出结果”表示证据低于门槛，不应强行交易。",
        "置信度衡量数据和历史样本是否可靠；模型自信度衡量多项信号是否一致。两者不是一回事。",
        "在“计算审计”中核对每个因子的原始值、权重和方向贡献；在“原始数据”中发现缺失或异常。",
    ],
    opened=True,
)

if snapshot.decisions.empty:
    st.error("暂无个股决策。请先检查行情和自选池数据。")
    st.stop()

with st.sidebar:
    st.markdown("### 个股筛选")
    market_filter = st.multiselect("市场", ["A", "HK"], default=["A", "HK"])
    all_actions = ["条件买", "加仓", "持有", "等待", "禁止买入", "减仓", "清仓"]
    action_filter = st.multiselect(
        "动作",
        all_actions,
        default=["条件买", "加仓", "持有", "等待", "减仓", "清仓"],
    )
    horizon_filter = st.selectbox("重点预测周期", ["一周", "一个月", "三个月"], index=0)

filtered = snapshot.decisions[
    snapshot.decisions["market"].isin(market_filter)
    & snapshot.decisions["action"].isin(action_filter)
].copy()
if filtered.empty:
    st.info("当前筛选没有股票。")
    st.stop()

focus_forecast = snapshot.stock_forecasts[
    snapshot.stock_forecasts["horizon"].eq(horizon_filter)
][["code", "result", "confidence", "conviction"]].copy()
focus_forecast["code"] = focus_forecast["code"].astype(str)
filtered["code"] = filtered["code"].astype(str)
stock_table = filtered.merge(focus_forecast, on="code", how="left")
stock_table["_decision_index"] = stock_table.index
stock_table_display = stock_table[
    [
        "name",
        "code",
        "market",
        "theme",
        "action",
        "current_price",
        "score",
        "result",
        "confidence",
        "conviction",
        "_decision_index",
    ]
].rename(
    columns={
        "name": "股票",
        "code": "代码",
        "market": "市场",
        "theme": "主题",
        "action": "交易动作",
        "current_price": "现价",
        "score": "规则分",
        "result": f"{horizon_filter}预测",
        "confidence": "置信度",
        "conviction": "模型自信度",
    }
)

section("点击股票查看详情")
selection = st.dataframe(
    stock_table_display.drop(columns="_decision_index"),
    width="stretch",
    hide_index=True,
    height=360,
    key="stock_drilldown_table",
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "置信度": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
        "模型自信度": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
        "现价": st.column_config.NumberColumn(format="%.2f"),
        "规则分": st.column_config.NumberColumn(format="%.0f"),
    },
)
selected_rows = selection.selection.rows
if selected_rows:
    selected_position = int(selected_rows[0])
    st.session_state["selected_stock_code"] = str(stock_table.iloc[selected_position]["code"])

available_codes = filtered["code"].astype(str).tolist()
valid_default_codes = (
    snapshot.stock_forecasts[
        snapshot.stock_forecasts["horizon"].eq("一周")
        & snapshot.stock_forecasts["result"].ne("分析不出结果")
        & snapshot.stock_forecasts["code"].astype(str).isin(available_codes)
    ]
    .sort_values(["confidence", "conviction"], ascending=False)["code"]
    .astype(str)
    .tolist()
)
default_stock_code = valid_default_codes[0] if valid_default_codes else available_codes[0]
selected_code = str(st.session_state.get("selected_stock_code", default_stock_code))
if selected_code not in available_codes:
    selected_code = available_codes[0]
    st.session_state["selected_stock_code"] = selected_code

decision = filtered[filtered["code"].astype(str) == selected_code].iloc[0]
history, history_path = load_history(str(decision["code"]), str(decision["name"]))
forecasts = snapshot.stock_forecasts[
    snapshot.stock_forecasts["code"].astype(str).eq(selected_code)
].sort_values("horizon_days")

vetoes = [item for item in str(decision.get("hard_vetoes", "")).split("；") if item]
usable_count = int((forecasts["result"] != "分析不出结果").sum()) if not forecasts.empty else 0
conclusion_panel(
    "个股结论",
    f"{decision['name']} 当前交易动作是“{decision['action']}”；3个周期中有 {usable_count} 个达到预测门槛。",
    [
        f"现价 {decision['current_price']:.2f}，触发 {decision['trigger_price']:.2f}，失效 {decision['stop_price']:.2f}。",
        f"板块状态 {decision['sector_state']}，个股地位 {decision['role']}，规则分 {decision['score']:.0f}。",
        f"硬否决：{'；'.join(vetoes) if vetoes else '无'}。",
    ],
    "只采用达到置信度与模型自信度门槛的周期结论；其余周期视为未知。",
    tone="red" if decision["action"] in {"条件买", "加仓"} else "amber",
)

forecast_cards: list[dict] = []
for _, row in forecasts.iterrows():
    usable = row["result"] != "分析不出结果"
    expected_text = (
        f"{row['expected_return_pct']:+.1f}%"
        if usable and pd.notna(row["expected_return_pct"])
        else "不输出"
    )
    forecast_cards.append(
        {
            "label": row["horizon"],
            "value": str(row["result"]),
            "note": (
                f"历史中位 {expected_text} · 置信 {row['confidence']:.0f} · "
                f"自信 {row['conviction']:.0f}"
            ),
            "color": "tp-red" if usable and "多" in str(row["result"]) else "tp-green" if usable else "tp-amber",
        }
    )
cards(forecast_cards)

tab_chart, tab_forecast, tab_audit, tab_raw = st.tabs(
    ["K线与区间", "多周期预测", "计算审计", "原始数据与错误"]
)

with tab_chart:
    left, right = st.columns([1.55, 1], gap="large")
    with left:
        if history.empty:
            st.warning("没有该股票的历史K线，三个周期均应显示“分析不出结果”。")
        else:
            st.plotly_chart(candlestick(history, decision), width="stretch")
            st.caption(f"历史数据源：{history_path}")
    with right:
        st.plotly_chart(
            forecast_range_chart(forecasts, float(decision["current_price"])),
            width="stretch",
        )
        st.caption("区间来自历史相似状态的25%–75%分位，不是保证到达的目标价。")

with tab_forecast:
    left, right = st.columns([1.25, 1], gap="large")
    with left:
        st.plotly_chart(forecast_confidence_chart(forecasts), width="stretch")
    with right:
        display_forecasts = forecasts[
            [
                "horizon",
                "result",
                "expected_return_pct",
                "range_low_pct",
                "range_high_pct",
                "confidence",
                "conviction",
                "sample_count",
                "historical_hit_rate",
                "reason",
            ]
        ].rename(
            columns={
                "horizon": "周期",
                "result": "预测",
                "expected_return_pct": "历史中位收益%",
                "range_low_pct": "区间下沿%",
                "range_high_pct": "区间上沿%",
                "confidence": "置信度",
                "conviction": "模型自信度",
                "sample_count": "相似样本",
                "historical_hit_rate": "历史方向命中率%",
                "reason": "结论说明",
            }
        )
        st.dataframe(display_forecasts, width="stretch", hide_index=True)
    for _, row in forecasts.iterrows():
        icon = "✅" if row["result"] != "分析不出结果" else "⛔"
        with st.expander(f"{icon} {row['horizon']} · {row['result']}", expanded=row["horizon"] == horizon_filter):
            st.write(f"**确认条件：** {row['confirmation']}")
            st.write(f"**失效条件：** {row['invalidation']}")
            st.write(f"**质量问题：** {row['quality_flags'] or '未发现明显问题'}")

with tab_audit:
    chosen_horizon = st.radio(
        "审计周期",
        ["一周", "一个月", "三个月"],
        horizontal=True,
        key="stock_audit_horizon",
    )
    chosen = forecasts[forecasts["horizon"] == chosen_horizon].iloc[0]
    factors = parse_factor_details(str(chosen["factor_details"]))
    left, right = st.columns([1.25, 1], gap="large")
    with left:
        st.plotly_chart(
            factor_contribution_chart(factors, f"{chosen_horizon} · 因子方向贡献"),
            width="stretch",
        )
    with right:
        section("预测如何形成")
        process_steps(
            [
                {
                    "title": "历史覆盖",
                    "status": f"{int(chosen['data_points'])}日",
                    "detail": f"历史末日 {chosen['latest_date']}，不同周期使用不同最低历史门槛。",
                    "color": "cyan",
                },
                {
                    "title": "因子合成",
                    "status": f"{chosen['forecast_score']:+.0f}",
                    "detail": "动量、均线、量能、市场、板块和个股地位按固定权重合成。",
                    "color": "purple",
                },
                {
                    "title": "历史校准",
                    "status": f"{int(chosen['sample_count'])}个样本",
                    "detail": f"相似状态历史方向命中率 {chosen['historical_hit_rate']:.1f}%。",
                    "color": "amber",
                },
                {
                    "title": "质量门槛",
                    "status": f"{chosen['confidence']:.0f}/{chosen['conviction']:.0f}",
                    "detail": "置信度低于60或模型自信度低于55，直接拒绝输出方向。",
                    "color": "green" if chosen["result"] != "分析不出结果" else "amber",
                },
            ]
        )
    if not factors.empty:
        st.dataframe(factors, width="stretch", hide_index=True)

with tab_raw:
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        section("最近30日原始K线")
        if history.empty:
            st.info("无历史数据。")
        else:
            st.dataframe(history.tail(30), width="stretch", hide_index=True, height=520)
    with right:
        section("数据与规则错误诊断")
        issues: list[str] = []
        if history.empty:
            issues.append("缺少历史K线")
        else:
            latest_hist = pd.to_datetime(history["trade_date"], errors="coerce").max()
            gap = (pd.Timestamp(snapshot.data_date) - latest_hist).days if pd.notna(latest_hist) else 999
            if gap > 5:
                issues.append(f"历史K线比行情日期早 {gap} 天")
            if len(history) < 260:
                issues.append("历史不足260日，三个月预测可靠性受限")
            for column in ("open", "high", "low", "close", "volume", "amount"):
                if column not in history:
                    issues.append(f"缺少字段：{column}")
                elif history[column].isna().mean() > 0.2:
                    issues.append(f"{column} 缺失率超过20%")
        if vetoes:
            issues.extend(f"规则否决：{veto}" for veto in vetoes)
        forecast_issues = forecasts.loc[
            forecasts["quality_flags"].astype(str).ne(""), ["horizon", "quality_flags"]
        ]
        issues.extend(
            f"{row['horizon']}预测：{row['quality_flags']}"
            for _, row in forecast_issues.iterrows()
        )
        if issues:
            for issue in dict.fromkeys(issues):
                st.warning(issue)
        else:
            st.success("未发现明显数据或规则错误。")
        section("规则证据")
        for reason in str(decision["reasons"]).split("；"):
            if reason:
                st.write(f"- {reason}")
