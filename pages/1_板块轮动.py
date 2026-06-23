from __future__ import annotations

import pandas as pd
import streamlit as st

from tradepush.collectors.common import read_csv_safe
from tradepush.collectors.local import load_sector_history
from tradepush.config import CONFIG_DIR
from tradepush.features.forecasting import (
    parse_factor_details,
    related_stocks_for_sector,
    sector_history_for_name,
)
from tradepush.ui.charts import (
    factor_contribution_chart,
    forecast_confidence_chart,
    sector_forecast_heatmap,
    sector_heatmap,
    sector_history_chart,
    sector_ranking,
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

setup_page("板块轮动", "▦")
snapshot = cached_snapshot()
hero(
    "板块轮动与多周期预测",
    "点击板块查看资金、价格、预测因子、历史覆盖和对应股票。置信度不足时明确显示分析不出结果。",
)
freshness_notice(snapshot.data_date)
usage_note(
    "本页如何使用",
    [
        "热力图用于发现整体轮动，板块表用于点击钻取，不要只看单日涨幅。",
        "一周预测重点看价格与资金是否共振；一个月关注持续性；三个月需要更长历史，数据不足就拒绝输出。",
        "置信度衡量快照覆盖和数据一致性；模型自信度衡量价格、资金、排名等因子是否同向。",
        "点击对应股票后可查看该股票的多周期预测，或跳转到完整个股分析页面。",
    ],
    opened=True,
)

sectors = snapshot.sectors
forecasts = snapshot.sector_horizon_forecasts
if sectors.empty:
    st.error("没有找到板块资金流数据。")
    st.stop()

counts = sectors["sector_state"].value_counts()
valid_forecasts = forecasts[forecasts["result"] != "分析不出结果"]
cards(
    [
        {
            "label": "主线进攻",
            "value": counts.get("主线进攻", 0),
            "note": "强度前10%且价格资金共振",
            "color": "tp-red",
        },
        {
            "label": "一周有效预测",
            "value": int(valid_forecasts["horizon"].eq("一周").sum()),
            "note": f"总板块 {sectors['name'].nunique()}",
            "color": "tp-cyan",
        },
        {
            "label": "一个月有效预测",
            "value": int(valid_forecasts["horizon"].eq("一个月").sum()),
            "note": "要求至少12个有效快照",
            "color": "tp-purple",
        },
        {
            "label": "三个月有效预测",
            "value": int(valid_forecasts["horizon"].eq("三个月").sum()),
            "note": "历史不足时直接拒绝",
            "color": "tp-amber",
        },
    ]
)

section("多周期预测总图")
valid_count = int((forecasts["result"] != "分析不出结果").sum()) if not forecasts.empty else 0
if valid_count == 0:
    st.warning("当前没有任何板块达到多周期预测的门槛。历史快照不足或数据覆盖不够时，预测会被拒绝。")
else:
    st.plotly_chart(sector_forecast_heatmap(forecasts), width="stretch")
    st.caption(f"共 {valid_count} 条有效预测 / {len(forecasts)} 条总行数。空白格 = 该周期证据不足，拒绝输出。")

forecast_pivot = forecasts.pivot_table(
    index="name",
    columns="horizon",
    values=["result", "confidence", "conviction"],
    aggfunc="first",
)
forecast_pivot.columns = [f"{metric}_{horizon}" for metric, horizon in forecast_pivot.columns]
sector_table = sectors.merge(forecast_pivot.reset_index(), on="name", how="left")
sector_table["_sector_index"] = sector_table.index
display_columns = [
    "name",
    "sector_state",
    "pct_chg",
    "net_amount",
    "leader",
    "result_一周",
    "confidence_一周",
    "conviction_一周",
    "result_一个月",
    "result_三个月",
]
display = sector_table[[col for col in display_columns if col in sector_table]].rename(
    columns={
        "name": "板块",
        "sector_state": "当前状态",
        "pct_chg": "当日涨跌%",
        "net_amount": "净流入(亿)",
        "leader": "领涨股",
        "result_一周": "一周预测",
        "confidence_一周": "一周置信度",
        "conviction_一周": "一周自信度",
        "result_一个月": "一个月预测",
        "result_三个月": "三个月预测",
    }
)

section("点击板块查看分析过程")
selection = st.dataframe(
    display,
    width="stretch",
    hide_index=True,
    height=430,
    key="sector_drilldown_table",
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "一周置信度": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
        "一周自信度": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
        "当日涨跌%": st.column_config.NumberColumn(format="%+.2f"),
        "净流入(亿)": st.column_config.NumberColumn(format="%+.2f"),
    },
)
if selection.selection.rows:
    selected_position = int(selection.selection.rows[0])
    st.session_state["selected_sector_name"] = str(sector_table.iloc[selected_position]["name"])

available_names = sector_table["name"].astype(str).tolist()
valid_default_names = (
    forecasts[
        forecasts["horizon"].eq("一周")
        & forecasts["result"].ne("分析不出结果")
    ]
    .sort_values(["confidence", "conviction"], ascending=False)["name"]
    .astype(str)
    .tolist()
)
default_sector_name = valid_default_names[0] if valid_default_names else available_names[0]
selected_name = str(st.session_state.get("selected_sector_name", default_sector_name))
if selected_name not in available_names:
    selected_name = available_names[0]
    st.session_state["selected_sector_name"] = selected_name

sector = sectors[sectors["name"].astype(str) == selected_name].iloc[0]
sector_forecasts = forecasts[forecasts["name"].astype(str) == selected_name].sort_values("horizon_days")
sector_history_sources = load_sector_history(as_of=snapshot.data_date)
sector_history = sector_history_for_name(selected_name, sector_history_sources)
usable_count = int((sector_forecasts["result"] != "分析不出结果").sum())

conclusion_panel(
    "板块结论",
    f"{selected_name} 当前属于“{sector['sector_state']}”；3个周期中有 {usable_count} 个达到预测门槛。",
    [
        f"当日涨跌 {sector['pct_chg']:+.2f}%，净流入 {sector['net_amount']:+.2f} 亿。",
        f"当前领涨股：{sector.get('leader', '未知')}，综合强度 {sector['strength_score']:+.1f}。",
        "预测仅在价格、资金与领涨结构继续确认时成立。",
    ],
    "先核对预测质量，再查看对应股票；没有对应股票或低置信预测时继续观察。",
    tone="purple",
)

forecast_cards: list[dict] = []
for _, row in sector_forecasts.iterrows():
    usable = row["result"] != "分析不出结果"
    forecast_cards.append(
        {
            "label": row["horizon"],
            "value": row["result"],
            "note": (
                f"置信 {row['confidence']:.0f} · 自信 {row['conviction']:.0f} · "
                f"{int(row['observations'])}个快照"
            ),
            "color": "tp-red" if usable and "多" in str(row["result"]) else "tp-green" if usable else "tp-amber",
        }
    )
cards(forecast_cards)

tab_detail, tab_rotation, tab_stocks, tab_transmission = st.tabs(
    ["预测与审计", "轮动可视化", "对应股票", "传导验证"]
)

with tab_detail:
    left, right = st.columns([1.35, 1], gap="large")
    with left:
        st.plotly_chart(sector_history_chart(sector_history, selected_name), width="stretch")
    with right:
        confidence_input = sector_forecasts.rename(
            columns={"horizon_days": "horizon_days", "confidence": "confidence", "conviction": "conviction"}
        )
        st.plotly_chart(forecast_confidence_chart(confidence_input), width="stretch")

    audit_horizon = st.radio(
        "审计周期",
        ["一周", "一个月", "三个月"],
        horizontal=True,
        key="sector_audit_horizon",
    )
    audit = sector_forecasts[sector_forecasts["horizon"] == audit_horizon].iloc[0]
    factors = parse_factor_details(str(audit["factor_details"]))
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        st.plotly_chart(
            factor_contribution_chart(factors, f"{selected_name} · {audit_horizon}因子贡献"),
            width="stretch",
        )
    with right:
        process_steps(
            [
                {
                    "title": "历史覆盖",
                    "status": f"{int(audit['observations'])}个快照",
                    "detail": f"覆盖率 {audit['coverage_pct']:.0f}%；覆盖不足会降低置信度。",
                    "color": "cyan",
                },
                {
                    "title": "价格持续",
                    "status": f"{audit['avg_pct_chg']:+.2f}%",
                    "detail": "使用周期内平均涨跌、近期加速度与排名持续性。",
                    "color": "red",
                },
                {
                    "title": "资金持续",
                    "status": f"{audit['positive_flow_rate']:.0f}%为正",
                    "detail": "净流入持续率低时，不允许仅凭价格上涨给出高质量看多。",
                    "color": "purple",
                },
                {
                    "title": "输出门槛",
                    "status": f"{audit['confidence']:.0f}/{audit['conviction']:.0f}",
                    "detail": "置信度低于60或模型自信度低于55，显示分析不出结果。",
                    "color": "green" if audit["result"] != "分析不出结果" else "amber",
                },
            ]
        )
    st.dataframe(
        sector_forecasts[
            [
                "horizon",
                "result",
                "forecast_score",
                "confidence",
                "conviction",
                "observations",
                "coverage_pct",
                "reason",
                "quality_flags",
            ]
        ].rename(
            columns={
                "horizon": "周期",
                "result": "预测",
                "forecast_score": "方向分",
                "confidence": "置信度",
                "conviction": "模型自信度",
                "observations": "有效快照",
                "coverage_pct": "覆盖率%",
                "reason": "结论说明",
                "quality_flags": "质量问题",
            }
        ),
        width="stretch",
        hide_index=True,
    )

with tab_rotation:
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        st.plotly_chart(sector_heatmap(sectors), width="stretch")
    with right:
        st.plotly_chart(sector_ranking(sectors), width="stretch")
    st.caption("A股习惯：红色为上涨/偏多，绿色为下跌/偏空；同时显示文字，避免只靠颜色判断。")

with tab_stocks:
    related = related_stocks_for_sector(
        selected_name,
        str(sector.get("leader", "")),
        snapshot.decisions,
    )
    if related.empty:
        st.info("自选池中没有可靠匹配的对应股票。系统不会为了填满列表而强行关联。")
    else:
        week_stock = snapshot.stock_forecasts[
            snapshot.stock_forecasts["horizon"].eq("一周")
        ][["code", "result", "confidence", "conviction"]]
        related["code"] = related["code"].astype(str)
        related = related.merge(week_stock.assign(code=week_stock["code"].astype(str)), on="code", how="left")
        stock_display = related[
            [
                "name",
                "code",
                "theme",
                "action",
                "current_price",
                "score",
                "result",
                "confidence",
                "conviction",
                "match_reason",
            ]
        ].rename(
            columns={
                "name": "股票",
                "code": "代码",
                "theme": "主题",
                "action": "交易动作",
                "current_price": "现价",
                "score": "规则分",
                "result": "一周预测",
                "confidence": "置信度",
                "conviction": "模型自信度",
                "match_reason": "关联依据",
            }
        )
        stock_selection = st.dataframe(
            stock_display,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="sector_stock_drilldown",
        )
        if stock_selection.selection.rows:
            stock_position = int(stock_selection.selection.rows[0])
            stock_code = str(related.iloc[stock_position]["code"])
            st.session_state["selected_stock_code"] = stock_code
            stock_forecasts = snapshot.stock_forecasts[
                snapshot.stock_forecasts["code"].astype(str).eq(stock_code)
            ].sort_values("horizon_days")
            st.dataframe(
                stock_forecasts[
                    ["horizon", "result", "expected_return_pct", "confidence", "conviction", "reason"]
                ].rename(
                    columns={
                        "horizon": "周期",
                        "result": "预测",
                        "expected_return_pct": "历史中位收益%",
                        "confidence": "置信度",
                        "conviction": "模型自信度",
                        "reason": "说明",
                    }
                ),
                width="stretch",
                hide_index=True,
            )
            if st.button("打开完整个股分析", type="primary", width="stretch"):
                st.switch_page("pages/2_个股决策.py")

with tab_transmission:
    mapping = read_csv_safe(CONFIG_DIR / "sector_transmission.csv")
    if mapping.empty:
        st.info("暂无板块传导配置。")
    else:
        active_names = set(
            sectors.loc[sectors["sector_state"] == "主线进攻", "name"].astype(str)
        )
        mapping["当前验证"] = mapping["trigger_sector"].apply(
            lambda value: (
                "触发端已强，继续验证传导端"
                if any(str(value) in name or name in str(value) for name in active_names)
                else "待验证：需要资金与核心股响应"
            )
        )
        st.dataframe(mapping, width="stretch", hide_index=True)
        st.caption("传导关系只生成观察候选，不直接产生个股买入动作。")
