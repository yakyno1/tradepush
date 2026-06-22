from __future__ import annotations

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

