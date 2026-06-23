from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

PLOT_BG = "rgba(0,0,0,0)"
GRID = "rgba(134,162,188,.13)"
TEXT = "#cfe4f7"


def base_layout(fig, height: int = 430):
    fig.update_layout(
        height=height,
        paper_bgcolor=PLOT_BG,
        plot_bgcolor=PLOT_BG,
        font=dict(color=TEXT, family="Arial"),
        margin=dict(l=20, r=20, t=45, b=20),
        hoverlabel=dict(bgcolor="#10243a", font_color="#ffffff"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID)
    return fig


def sector_heatmap(sectors: pd.DataFrame):
    if sectors.empty:
        return go.Figure()
    data = sectors.head(30).copy()
    data["size"] = pd.to_numeric(data.get("amount", 1), errors="coerce").abs().fillna(1).clip(lower=1)
    fig = px.treemap(
        data,
        path=["sector_state", "name"],
        values="size",
        color="pct_chg",
        color_continuous_scale=["#20c997", "#25344b", "#ff5468"],
        color_continuous_midpoint=0,
        hover_data=["net_amount", "leader", "leader_pct", "transmission_status"],
    )
    fig.update_traces(textinfo="label+value", textfont_color="#f2f8ff")
    return base_layout(fig, 500)


def sector_ranking(sectors: pd.DataFrame):
    if sectors.empty:
        return go.Figure()
    data = sectors.head(18).sort_values("strength_score")
    colors = ["#ff5468" if x >= 0 else "#2ed99f" for x in data["strength_score"]]
    fig = go.Figure(
        go.Bar(
            x=data["strength_score"],
            y=data["name"],
            orientation="h",
            marker_color=colors,
            customdata=data[["pct_chg", "net_amount", "sector_state"]],
            hovertemplate="%{y}<br>强度 %{x:.1f}<br>涨跌 %{customdata[0]:.2f}%<br>净流 %{customdata[1]:.2f}<br>%{customdata[2]}<extra></extra>",
        )
    )
    fig.update_layout(title="板块综合强度")
    return base_layout(fig, 520)


def candlestick(history: pd.DataFrame, decision: pd.Series | dict | None = None):
    if history.empty:
        return go.Figure()
    data = history.tail(180).copy()
    for window in (20, 60, 120):
        col = f"ma{window}"
        if col not in data:
            data[col] = pd.to_numeric(data["close"], errors="coerce").rolling(window, min_periods=5).mean()
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.74, 0.26],
    )
    fig.add_trace(
        go.Candlestick(
            x=data["trade_date"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            increasing_line_color="#ff5468",
            decreasing_line_color="#2ed99f",
            name="K线",
        ),
        row=1,
        col=1,
    )
    for window, color in ((20, "#20d9ff"), (60, "#9a6bff"), (120, "#ffbe55")):
        fig.add_trace(
            go.Scatter(x=data["trade_date"], y=data[f"ma{window}"], name=f"MA{window}", line=dict(color=color, width=1.4)),
            row=1,
            col=1,
        )
    if "volume" in data:
        colors = ["#ff5468" if c >= o else "#2ed99f" for c, o in zip(data["close"], data["open"])]
        fig.add_trace(
            go.Bar(x=data["trade_date"], y=data["volume"], marker_color=colors, name="成交量", opacity=.55),
            row=2,
            col=1,
        )
    if decision is not None:
        levels = [
            ("trigger_price", "触发", "#20d9ff", "dash"),
            ("stop_price", "失效", "#2ed99f", "dot"),
            ("target_price", "目标", "#ff5468", "dashdot"),
        ]
        for key, label, color, dash in levels:
            try:
                value = float(decision[key])
            except (KeyError, TypeError, ValueError):
                continue
            fig.add_hline(y=value, line_color=color, line_dash=dash, annotation_text=label, row=1, col=1)
    fig.update_layout(xaxis_rangeslider_visible=False)
    return base_layout(fig, 650)


def equity_curve(verification: pd.DataFrame):
    if verification.empty or "actual_pct_chg" not in verification:
        return go.Figure()
    data = verification.copy()
    data["actual_pct_chg"] = pd.to_numeric(data["actual_pct_chg"], errors="coerce").fillna(0)
    date_col = "verify_date" if "verify_date" in data else "prediction_date"
    data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
    daily = data.dropna(subset=[date_col]).groupby(date_col)["actual_pct_chg"].mean().sort_index()
    curve = (1 + daily / 100).cumprod() * 100
    fig = go.Figure(go.Scatter(x=curve.index, y=curve.values, fill="tozeroy", line=dict(color="#20d9ff", width=2)))
    fig.update_layout(title="历史验证参考曲线（非真实成交净值）")
    return base_layout(fig, 390)


def forecast_range_chart(forecasts: pd.DataFrame, current_price: float):
    usable = forecasts[
        (forecasts["result"] != "分析不出结果")
        & pd.to_numeric(forecasts["price_low"], errors="coerce").notna()
    ].copy()
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[0],
            y=[current_price],
            mode="markers+text",
            text=["当前价"],
            textposition="top center",
            marker=dict(color="#20d9ff", size=10),
            name="当前价",
        )
    )
    if usable.empty:
        fig.add_annotation(
            text="没有达到置信度与自信度门槛的预测",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(color="#ffbe55", size=15),
        )
        fig.update_layout(title="多周期价格区间（拒绝低质量预测）")
        return base_layout(fig, 410)
    usable = usable.sort_values("horizon_days")
    x = [0, *usable["horizon_days"].astype(int).tolist()]
    mid = [current_price, *pd.to_numeric(usable["price_mid"], errors="coerce").tolist()]
    low = [current_price, *pd.to_numeric(usable["price_low"], errors="coerce").tolist()]
    high = [current_price, *pd.to_numeric(usable["price_high"], errors="coerce").tolist()]
    fig.add_trace(
        go.Scatter(
            x=x,
            y=high,
            line=dict(width=0),
            mode="lines",
            name="区间上沿",
            hovertemplate="%{y:.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=low,
            fill="tonexty",
            fillcolor="rgba(88,135,255,.18)",
            line=dict(width=0),
            mode="lines",
            name="历史相似区间",
            hovertemplate="%{y:.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=mid,
            line=dict(color="#ff5468", width=2.5),
            mode="lines+markers",
            name="历史中位路径",
            hovertemplate="%{y:.2f}<extra></extra>",
        )
    )
    fig.update_xaxes(
        tickmode="array",
        tickvals=[0, 5, 20, 60],
        ticktext=["当前", "一周", "一个月", "三个月"],
        title="预测周期（交易日）",
    )
    fig.update_yaxes(title="价格")
    fig.update_layout(title="多周期条件价格区间")
    return base_layout(fig, 440)


def forecast_confidence_chart(forecasts: pd.DataFrame):
    data = forecasts.copy().sort_values("horizon_days")
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=data["horizon"],
            y=data["confidence"],
            name="置信度",
            marker_color="#20d9ff",
            text=data["confidence"].round(0),
            textposition="outside",
        )
    )
    fig.add_trace(
        go.Bar(
            x=data["horizon"],
            y=data["conviction"],
            name="模型自信度",
            marker_color="#9a6bff",
            text=data["conviction"].round(0),
            textposition="outside",
        )
    )
    fig.add_hline(y=60, line_color="#ffbe55", line_dash="dash", annotation_text="置信度门槛")
    fig.add_hline(y=55, line_color="#ff5468", line_dash="dot", annotation_text="自信度门槛")
    fig.update_layout(title="预测质量门槛", barmode="group")
    fig.update_yaxes(range=[0, 110], title="分数")
    return base_layout(fig, 390)


def sector_forecast_heatmap(forecasts: pd.DataFrame, limit: int = 35):
    if forecasts.empty:
        return go.Figure()
    work = forecasts.copy()
    usable = work[work["result"] != "分析不出结果"]
    top_names = (
        usable.groupby("name")["confidence"].max().sort_values(ascending=False).head(limit).index.tolist()
    )
    if not top_names:
        top_names = work["name"].drop_duplicates().head(limit).tolist()
    work = work[work["name"].isin(top_names)].copy()
    work["heat_score"] = np.where(
        work["result"] == "分析不出结果",
        np.nan,
        pd.to_numeric(work["forecast_score"], errors="coerce"),
    )
    pivot = work.pivot_table(index="name", columns="horizon", values="heat_score", aggfunc="first")
    pivot = pivot.reindex(columns=["一周", "一个月", "三个月"])
    hover = work.pivot_table(index="name", columns="horizon", values="result", aggfunc="first").reindex(
        index=pivot.index, columns=pivot.columns
    )
    fig = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=pivot.columns,
            y=pivot.index,
            zmin=-100,
            zmax=100,
            zmid=0,
            colorscale=[[0, "#2ed99f"], [0.5, "#25344b"], [1, "#ff5468"]],
            customdata=hover.values,
            hovertemplate="%{y}<br>%{x}<br>得分 %{z:.1f}<br>%{customdata}<extra></extra>",
            colorbar=dict(title="方向分"),
        )
    )
    fig.update_layout(title="板块多周期预测热力图（空白=分析不出结果）")
    return base_layout(fig, max(480, len(pivot) * 23))


def sector_history_chart(history: pd.DataFrame, name: str):
    if history.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="缺少该板块的连续历史快照",
            x=.5,
            y=.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
        return base_layout(fig, 420)
    data = history.copy()
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    colors = ["#ff5468" if value >= 0 else "#2ed99f" for value in data["pct_chg"]]
    fig.add_trace(
        go.Bar(
            x=data["snapshot_date"],
            y=data["pct_chg"],
            marker_color=colors,
            name="当日涨跌%",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=data["snapshot_date"],
            y=data["net_amount"],
            line=dict(color="#20d9ff", width=2),
            mode="lines+markers",
            name="净流入",
        ),
        secondary_y=True,
    )
    fig.update_yaxes(title_text="涨跌幅%", secondary_y=False)
    fig.update_yaxes(title_text="净流入（亿）", secondary_y=True)
    fig.update_layout(title=f"{name} · 价格与资金历史")
    return base_layout(fig, 430)


def factor_contribution_chart(factors: pd.DataFrame, title: str = "因子贡献"):
    if factors.empty or "contribution" not in factors:
        return go.Figure()
    data = factors.sort_values("contribution")
    colors = ["#ff5468" if value >= 0 else "#2ed99f" for value in data["contribution"]]
    fig = go.Figure(
        go.Bar(
            x=data["contribution"],
            y=data["factor"],
            orientation="h",
            marker_color=colors,
            hovertemplate="%{y}<br>贡献 %{x:.1f}<extra></extra>",
        )
    )
    fig.add_vline(x=0, line_color="#86a2bc", line_width=1)
    fig.update_layout(title=title)
    fig.update_xaxes(title="方向贡献（正=偏多，负=偏空）")
    return base_layout(fig, max(360, len(data) * 45))
