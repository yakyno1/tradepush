from __future__ import annotations

import math
import re

import pandas as pd
import streamlit as st

from tradepush.collectors.local import load_history
from tradepush.features.technical import latest_features
from tradepush.services.dashboard import DashboardSnapshot


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if pd.isna(result) else result
    except (TypeError, ValueError):
        return default


def _score_text(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _source_rows(snapshot: DashboardSnapshot, sources: list[str]) -> pd.DataFrame:
    health = snapshot.source_health.copy()
    if health.empty or "source" not in health:
        return pd.DataFrame()
    show = health[health["source"].astype(str).isin(sources)].copy()
    columns = [column for column in ("source", "status", "rows", "latest", "detail") if column in show]
    return show[columns].rename(
        columns={
            "source": "数据源",
            "status": "状态",
            "rows": "记录数",
            "latest": "数据日期",
            "detail": "说明",
        }
    )


def _market_components(snapshot: DashboardSnapshot) -> tuple[pd.DataFrame, pd.DataFrame]:
    indices = snapshot.indices.copy()
    prices = snapshot.prices.copy()
    sectors = snapshot.sectors.copy()

    index_pct = pd.to_numeric(indices.get("pct_chg", pd.Series(dtype=float)), errors="coerce").dropna()
    index_avg = float(index_pct.mean()) if not index_pct.empty else 0.0
    positive_indices = float((index_pct > 0).mean()) if not index_pct.empty else 0.0
    index_base = max(min(20 + index_avg * 8, 30), 0)

    stock_pct = pd.to_numeric(prices.get("pct_chg", pd.Series(dtype=float)), errors="coerce").dropna()
    breadth = float((stock_pct > 0).mean()) if not stock_pct.empty else 0.0

    above_ma20 = positive_indices
    if {"close", "ma20"}.issubset(indices.columns):
        close = pd.to_numeric(indices["close"], errors="coerce")
        ma20 = pd.to_numeric(indices["ma20"], errors="coerce")
        valid = close.notna() & ma20.notna()
        above_ma20 = float((close[valid] >= ma20[valid]).mean()) if valid.any() else 0.0

    sector_strength = 0.0
    if not sectors.empty:
        line = sectors.get("line_state", pd.Series("", index=sectors.index)).astype(str)
        pct = pd.to_numeric(sectors.get("pct_chg", 0), errors="coerce").fillna(0)
        sector_strength = min(float(((line == "强") | (pct >= 2)).mean()), 1.0)

    data_penalty = -10.0 if any(frame.empty for frame in (indices, prices, sectors)) else 0.0
    components = [
        {
            "评分项": "指数平均表现",
            "原始值": f"{index_avg:+.2f}%",
            "计算": "限制在0–30分：20 + 指数平均涨跌×8",
            "贡献分": round(index_base, 1),
        },
        {
            "评分项": "上涨指数占比",
            "原始值": f"{positive_indices:.0%}",
            "计算": "上涨指数占比×15",
            "贡献分": round(positive_indices * 15, 1),
        },
        {
            "评分项": "自选池上涨广度",
            "原始值": f"{breadth:.0%}",
            "计算": "上涨股票占比×25",
            "贡献分": round(breadth * 25, 1),
        },
        {
            "评分项": "指数MA20覆盖",
            "原始值": f"{above_ma20:.0%}",
            "计算": "站上MA20指数占比×20",
            "贡献分": round(above_ma20 * 20, 1),
        },
        {
            "评分项": "强势板块覆盖",
            "原始值": f"{sector_strength:.0%}",
            "计算": "强势板块占比×10",
            "贡献分": round(sector_strength * 10, 1),
        },
        {
            "评分项": "核心数据缺口",
            "原始值": f"{snapshot.market.data_quality:.0f}%完整",
            "计算": "任一核心表为空时扣10分",
            "贡献分": data_penalty,
        },
    ]
    component_df = pd.DataFrame(components)
    explained_score = float(component_df["贡献分"].sum())
    adjustment = round(float(snapshot.market.score) - explained_score, 1)
    if abs(adjustment) >= 0.1:
        component_df.loc[len(component_df)] = {
            "评分项": "时效/硬门槛调整",
            "原始值": "见市场原因",
            "计算": "例如板块资金过期时额外降级",
            "贡献分": adjustment,
        }

    raw_indices = indices.copy()
    if not raw_indices.empty:
        raw_indices["站上MA20"] = (
            pd.to_numeric(raw_indices.get("close"), errors="coerce")
            >= pd.to_numeric(raw_indices.get("ma20"), errors="coerce")
        ).map({True: "是", False: "否"})
        keep = [
            column
            for column in ("name", "code", "pct_chg", "close", "ma20", "站上MA20", "trade_date", "source_used")
            if column in raw_indices
        ]
        raw_indices = raw_indices[keep].rename(
            columns={
                "name": "指数",
                "code": "代码",
                "pct_chg": "涨跌幅%",
                "close": "收盘/现价",
                "ma20": "MA20",
                "trade_date": "数据日期",
                "source_used": "来源",
            }
        )
    return component_df, raw_indices


def _render_market(snapshot: DashboardSnapshot) -> None:
    st.markdown(
        f"### 市场总开关：{snapshot.market.label} · {_score_text(snapshot.market.score)}"
    )
    st.caption("评级阈值：70分及以上=进攻；45–69.9=谨慎；低于45=停止新开仓。硬门槛可覆盖分数。")
    components, raw_indices = _market_components(snapshot)
    st.markdown("**评分拆解**")
    st.dataframe(components, use_container_width=True, hide_index=True)
    if snapshot.market.reasons:
        st.markdown("**系统记录的判断原因**")
        for reason in snapshot.market.reasons:
            st.write(f"- {reason}")
    st.markdown("**指数原始数据**")
    if raw_indices.empty:
        st.warning("当前版本没有指数明细。")
    else:
        st.dataframe(raw_indices, use_container_width=True, hide_index=True)
    sources = _source_rows(snapshot, ["市场指数", "股票日行情", "板块资金流"])
    if not sources.empty:
        st.markdown("**数据来源与新鲜度**")
        st.dataframe(sources, use_container_width=True, hide_index=True)


def _render_data_quality(snapshot: DashboardSnapshot) -> None:
    health = snapshot.source_health.copy()
    st.markdown("### 数据门槛：核心表到位情况")
    st.caption(
        "市场评分中的“数据完整度”只检查股票行情、市场指数、板块资金三张核心表是否非空；"
        "不等于每个字段、每只股票和每段历史都100%齐全。"
    )
    core_sources = ["股票日行情", "市场指数", "板块资金流"]
    if health.empty or "source" not in health:
        st.warning("当前版本没有来源健康记录。")
        return
    core = health[health["source"].astype(str).isin(core_sources)].copy()
    rows = []
    for source in core_sources:
        found = core[core["source"].astype(str).eq(source)]
        row = found.iloc[0] if not found.empty else pd.Series(dtype=object)
        count = int(_number(row.get("rows"), 0))
        rows.append(
            {
                "核心表": source,
                "是否到位": "是" if count > 0 else "否",
                "记录数": count,
                "状态": str(row.get("status", "缺失")),
                "数据日期": str(row.get("latest", "")),
                "来源说明": str(row.get("detail", "")),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    available = sum(item["是否到位"] == "是" for item in rows)
    st.markdown(
        f"**当前口径：{available}/3张核心表到位 = {snapshot.market.data_quality:.0f}%数据门槛。**"
    )
    other = health[~health["source"].astype(str).isin(core_sources)].copy()
    if not other.empty:
        st.markdown("**辅助来源状态**")
        st.dataframe(
            other.rename(
                columns={
                    "source": "数据源",
                    "status": "状态",
                    "rows": "记录数",
                    "latest": "数据日期",
                    "detail": "说明",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )


def _render_sector(snapshot: DashboardSnapshot) -> None:
    if snapshot.sectors.empty:
        st.warning("当前版本没有可用板块数据，无法解释最强方向。")
        return
    top = snapshot.sectors.iloc[0]
    pct = _number(top.get("pct_chg"))
    net = _number(top.get("net_amount"))
    leader_pct = _number(top.get("leader_pct"))
    price_points = max(min(pct, 8), -8) * 6
    fund_points = math.tanh(net / 100) * 25
    leader_points = max(min(leader_pct, 20), -20) * 1.2
    strength = _number(top.get("strength_score"))

    st.markdown(f"### 今日最强方向：{top.get('name', '—')}")
    st.caption("按板块强度分排序第一；这只是相对最强，不代表一定适合买入。")
    breakdown = pd.DataFrame(
        [
            {
                "评分项": "板块涨跌",
                "原始值": f"{pct:+.2f}%",
                "计算": "涨跌幅限制在±8%后×6",
                "贡献分": round(price_points, 1),
            },
            {
                "评分项": "主力净流入",
                "原始值": f"{net:+.2f}亿元",
                "计算": "tanh(净流入/100)×25",
                "贡献分": round(fund_points, 1),
            },
            {
                "评分项": "领涨股表现",
                "原始值": f"{leader_pct:+.2f}%",
                "计算": "领涨股涨跌限制在±20%后×1.2",
                "贡献分": round(leader_points, 1),
            },
            {
                "评分项": "板块强度总分",
                "原始值": str(top.get("sector_state", "—")),
                "计算": "以上三项相加",
                "贡献分": round(strength, 1),
            },
        ]
    )
    st.dataframe(breakdown, use_container_width=True, hide_index=True)
    st.markdown(
        "**状态门槛：** 排名前10%（至少前5、至多前20）、涨幅≥1.5%、主力净流入>0且无资金背离，"
        "才会标记为“主线进攻”。"
    )

    top_sectors = snapshot.sectors.head(10).copy()
    keep = [
        column
        for column in (
            "name",
            "sector_state",
            "strength_score",
            "pct_chg",
            "net_amount",
            "leader",
            "leader_pct",
            "transmission_status",
            "source",
            "trade_date",
        )
        if column in top_sectors
    ]
    top_sectors = top_sectors[keep].rename(
        columns={
            "name": "板块",
            "sector_state": "状态",
            "strength_score": "强度分",
            "pct_chg": "涨跌幅%",
            "net_amount": "主力净流入(亿元)",
            "leader": "领涨股",
            "leader_pct": "领涨股涨跌%",
            "transmission_status": "资金验证",
            "source": "来源",
            "trade_date": "数据日期",
        }
    )
    st.markdown("**前10名对比数据**")
    st.dataframe(top_sectors, use_container_width=True, hide_index=True)
    sources = _source_rows(snapshot, ["板块资金流"])
    if not sources.empty:
        st.markdown("**数据来源与新鲜度**")
        st.dataframe(sources, use_container_width=True, hide_index=True)


def _render_sector_rotation(snapshot: DashboardSnapshot) -> None:
    _render_sector(snapshot)
    forecast = snapshot.sector_forecast.copy()
    if forecast.empty:
        st.info("当前没有1–3日板块前瞻候选。")
        return
    keep = [
        column
        for column in (
            "forecast_rank",
            "name",
            "forecast_state",
            "forecast_score",
            "confidence",
            "current_state",
            "pct_chg",
            "net_amount",
            "history_hits",
            "why",
            "confirmation",
            "invalidation",
            "horizon",
        )
        if column in forecast
    ]
    show = forecast[keep].head(10).rename(
        columns={
            "forecast_rank": "前瞻排名",
            "name": "板块",
            "forecast_state": "前瞻状态",
            "forecast_score": "前瞻分",
            "confidence": "置信度",
            "current_state": "当前状态",
            "pct_chg": "当前涨跌%",
            "net_amount": "主力净流入(亿元)",
            "history_hits": "历史样本",
            "why": "候选原因",
            "confirmation": "确认条件",
            "invalidation": "失效条件",
            "horizon": "观察周期",
        }
    )
    for column in show.select_dtypes(include="object").columns:
        show[column] = show[column].fillna("").astype(str)
    st.markdown("**未来1–3日条件候选及确认条件**")
    st.dataframe(show, use_container_width=True, hide_index=True)


def _render_decisions(snapshot: DashboardSnapshot) -> None:
    decisions = snapshot.decisions.copy()
    st.markdown("### 条件买 / 等待")
    st.caption(
        "未持仓股票只有在“无硬否决且评分≥75”时才是条件买；评分<45或硬否决≥3项时为禁止买入，其余为等待。"
    )
    if decisions.empty:
        st.warning("当前版本没有生成个股决策。")
        return
    counts = (
        decisions["action"]
        .astype(str)
        .value_counts()
        .rename_axis("动作")
        .reset_index(name="数量")
    )
    st.markdown("**动作分布**")
    st.dataframe(counts, use_container_width=True, hide_index=True)

    show = decisions.copy()
    veto_count = show.get("hard_vetoes", pd.Series("", index=show.index)).fillna("").astype(str)
    show["硬否决数"] = veto_count.map(lambda value: len([item for item in value.split("；") if item.strip()]))
    keep = [
        column
        for column in (
            "name",
            "code",
            "theme",
            "action",
            "score",
            "sector_state",
            "role",
            "gate_passed",
            "硬否决数",
            "hard_vetoes",
            "reasons",
            "evidence_time",
        )
        if column in show
    ]
    show = show[keep].head(20).rename(
        columns={
            "name": "股票",
            "code": "代码",
            "theme": "主题",
            "action": "动作",
            "score": "评分",
            "sector_state": "板块状态",
            "role": "地位",
            "gate_passed": "硬门槛通过",
            "hard_vetoes": "硬否决",
            "reasons": "数据证据",
            "evidence_time": "证据日期",
        }
    )
    st.markdown("**排序靠前的20只股票及否决原因**")
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.markdown(
        "**个股评分框架：** 基础45分 + 市场状态 + 板块状态 + 核心/中军地位 + 趋势 + 安全区 "
        "+ 收益风险比 − 每项硬否决8分。最终动作仍由硬门槛优先决定。"
    )
    sources = _source_rows(snapshot, ["股票日行情", "板块资金流", "账户与自选池"])
    if not sources.empty:
        st.markdown("**数据来源与新鲜度**")
        st.dataframe(sources, use_container_width=True, hide_index=True)


def _render_portfolio(snapshot: DashboardSnapshot) -> None:
    account_cap = _number(snapshot.account.get("max_total_pct"), 0)
    market_cap = _number(snapshot.market.max_exposure_pct, 0)
    effective_cap = min(account_cap, market_cap)
    exposure = _number(snapshot.portfolio.get("exposure_pct"), 0)
    remaining = max(effective_cap - exposure, 0)
    st.markdown(f"### 已用仓位 {exposure:.1f}% / 有效剩余额度 {remaining:.1f}%")
    st.caption("有效仓位上限取“账户总仓位上限”和“市场总开关上限”的较低值。")
    account_table = pd.DataFrame(
        [
            {"项目": "账户权益", "当前值": f"{_number(snapshot.account.get('equity')):,.2f}元", "来源": "账户配置"},
            {"项目": "现金", "当前值": f"{_number(snapshot.account.get('cash')):,.2f}元", "来源": "账户配置"},
            {"项目": "持仓市值", "当前值": f"{_number(snapshot.portfolio.get('market_value')):,.2f}元", "来源": "持仓×当前价"},
            {"项目": "已用仓位", "当前值": f"{exposure:.1f}%", "来源": "持仓市值÷账户权益"},
            {"项目": "账户总仓位上限", "当前值": f"{account_cap:.1f}%", "来源": "账户配置"},
            {
                "项目": "市场仓位上限",
                "当前值": f"{market_cap:.1f}%",
                "来源": f"市场评级“{snapshot.market.label}”",
            },
            {"项目": "有效仓位上限", "当前值": f"{effective_cap:.1f}%", "来源": "取两项上限较低值"},
            {"项目": "有效剩余额度", "当前值": f"{remaining:.1f}%", "来源": "有效上限−已用仓位"},
            {
                "项目": "账户参数确认",
                "当前值": "已确认" if bool(snapshot.account.get("confirmed")) else "未确认",
                "来源": "账户配置",
            },
        ]
    )
    st.dataframe(account_table, use_container_width=True, hide_index=True)
    rows = snapshot.portfolio.get("rows", [])
    if rows:
        st.markdown("**持仓明细**")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("当前持仓表为空，因此已用仓位为0%。")
    sources = _source_rows(snapshot, ["股票日行情", "账户与自选池"])
    if not sources.empty:
        st.markdown("**数据来源与新鲜度**")
        st.dataframe(sources, use_container_width=True, hide_index=True)


def _decision_row(snapshot: DashboardSnapshot, token: str) -> pd.Series | None:
    if snapshot.decisions.empty or not token:
        return None
    if "|" in token:
        market, code = token.split("|", 1)
    else:
        market, code = "", token
    code_series = snapshot.decisions["code"].astype(str).str.replace(r"\.0$", "", regex=True)
    mask = code_series.eq(str(code).replace(".0", ""))
    if market and "market" in snapshot.decisions:
        mask &= snapshot.decisions["market"].astype(str).eq(market)
    found = snapshot.decisions[mask]
    return found.iloc[0] if not found.empty else None


def _price_row(snapshot: DashboardSnapshot, decision: pd.Series) -> pd.Series:
    if snapshot.prices.empty:
        return pd.Series(dtype=object)
    market = str(decision.get("market", "A")).upper()
    width = 5 if market == "HK" else 6
    target = str(decision.get("code", "")).replace(".0", "").zfill(width)
    codes = snapshot.prices["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(width)
    found = snapshot.prices[codes.eq(target)]
    return found.iloc[0] if not found.empty else pd.Series(dtype=object)


def _safety_row(snapshot: DashboardSnapshot, decision: pd.Series) -> pd.Series:
    if snapshot.safety_zones.empty or "code" not in snapshot.safety_zones:
        return pd.Series(dtype=object)
    market = str(decision.get("market", "A")).upper()
    width = 5 if market == "HK" else 6
    target = str(decision.get("code", "")).replace(".0", "").zfill(width)
    codes = (
        snapshot.safety_zones["code"]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(width)
    )
    found = snapshot.safety_zones[codes.eq(target)]
    return found.iloc[0] if not found.empty else pd.Series(dtype=object)


def _reason_number(reasons: str, label: str, default: float = 0.0) -> float:
    match = re.search(rf"{re.escape(label)}：([+-]?\d+(?:\.\d+)?)", reasons)
    return _number(match.group(1), default) if match else default


def _stock_history_features(
    snapshot: DashboardSnapshot,
    decision: pd.Series,
    price: pd.Series,
) -> tuple[dict, str]:
    code = str(decision.get("code", ""))
    name = str(decision.get("name", code))
    history, path = load_history(code, name, as_of=snapshot.data_date)
    if not history.empty:
        latest_date = pd.to_datetime(history["trade_date"], errors="coerce").max()
        current_date = pd.to_datetime(price.get("trade_date") or snapshot.data_date, errors="coerce")
        if pd.notna(current_date) and (pd.isna(latest_date) or current_date > latest_date):
            current = _number(decision.get("current_price"))
            current_row = pd.DataFrame(
                [
                    {
                        "trade_date": current_date,
                        "open": _number(price.get("open"), current),
                        "high": _number(price.get("high"), current),
                        "low": _number(price.get("low"), current),
                        "close": current,
                        "volume": _number(price.get("volume"), 0),
                        "amount": _number(price.get("amount"), 0),
                    }
                ]
            )
            history = pd.concat([history, current_row], ignore_index=True)
    return latest_features(history), str(path or "")


def render_stock_evidence(snapshot: DashboardSnapshot, token: str) -> None:
    decision = _decision_row(snapshot, token)
    if decision is None:
        return
    price = _price_row(snapshot, decision)
    safety = _safety_row(snapshot, decision)
    features, history_path = _stock_history_features(snapshot, decision, price)

    current = _number(decision.get("current_price"))
    trigger = _number(decision.get("trigger_price"))
    stop = _number(decision.get("stop_price"))
    target = _number(decision.get("target_price"))
    rr = _number(decision.get("risk_reward"))
    reasons = str(decision.get("reasons", ""))
    ma20 = _number(price.get("ma20"), _number(features.get("ma20"), _reason_number(reasons, "MA20")))
    ma60 = _number(price.get("ma60"), _number(features.get("ma60"), 0))
    atr = _number(features.get("atr14"), current * 0.035)
    amount_ratio = _number(features.get("amount_ratio20"), _reason_number(reasons, "量能比", 1))
    position_20d = _number(
        price.get("position_20d"),
        _reason_number(reasons, "20日位置") / 100,
    )
    pct_chg = _number(price.get("pct_chg"), 0)
    normal_low = _number(safety.get("normal_safe_low"), 0)
    normal_high = _number(safety.get("normal_safe_high"), 0)
    deep_low = _number(safety.get("deep_safe_low"), 0)
    in_safe_zone = bool(normal_low and normal_high and normal_low <= current <= normal_high)
    below_safe = bool(deep_low and current < deep_low)
    trend_ok = current >= ma20 and ma20 >= ma60 * 0.97
    position_ok = position_20d <= 0.88 or pct_chg <= 2
    volume_ok = amount_ratio >= 0.75
    role = str(decision.get("role", ""))
    role_ok = role in {"核心", "中军"}
    sector_state = str(decision.get("sector_state", ""))
    vetoes = [
        item.strip()
        for item in str(decision.get("hard_vetoes", "")).split("；")
        if item.strip()
    ]

    market_points = 15 if snapshot.market.label == "进攻" else 5 if snapshot.market.label == "谨慎" else -20
    sector_points = 15 if sector_state == "主线进攻" else 7 if sector_state == "轮动观察" else -8
    role_points = 15 if role == "核心" else 10 if role == "中军" else 0
    rr_points = min(max((rr - 1) * 5, 0), 10)
    score_rows = [
        {"评分项": "基础分", "判断": "所有股票起点", "加减分": 45.0},
        {"评分项": "市场状态", "判断": snapshot.market.label, "加减分": market_points},
        {"评分项": "板块状态", "判断": sector_state, "加减分": sector_points},
        {"评分项": "个股地位", "判断": role or "未分类", "加减分": role_points},
        {"评分项": "趋势通过", "判断": "是" if trend_ok else "否", "加减分": 10 if trend_ok else 0},
        {"评分项": "安全区内", "判断": "是" if in_safe_zone else "否", "加减分": 10 if in_safe_zone else 0},
        {"评分项": "收益风险比", "判断": f"{rr:.2f}", "加减分": round(rr_points, 1)},
        {"评分项": "硬否决惩罚", "判断": f"{len(vetoes)}项×8", "加减分": -len(vetoes) * 8},
    ]
    explained = sum(float(row["加减分"]) for row in score_rows)
    stored_score = _number(decision.get("score"))
    adjustment = round(stored_score - max(min(explained, 100), 0), 1)
    if abs(adjustment) >= 0.1:
        score_rows.append({"评分项": "舍入/归档差异", "判断": "以保存结果为准", "加减分": adjustment})

    with st.container(border=True):
        st.markdown(
            f"### {decision.get('name', '')} {decision.get('code', '')}："
            f"{decision.get('action', '')} · 排序分{stored_score:.1f}"
        )
        st.warning(
            "排序分只决定查看顺序；只要存在硬否决，系统就不会把高分股票升级为条件买。"
        )
        st.markdown("**评分拆解**")
        st.dataframe(pd.DataFrame(score_rows), use_container_width=True, hide_index=True)

        gates = pd.DataFrame(
            [
                {"硬门槛": "账户参数已确认", "当前数据": "是" if snapshot.account.get("confirmed") else "否", "通过": bool(snapshot.account.get("confirmed"))},
                {"硬门槛": "市场允许新开仓", "当前数据": snapshot.market.label, "通过": snapshot.market.label != "停止新开仓"},
                {"硬门槛": "板块未退潮", "当前数据": sector_state, "通过": sector_state != "退潮回避"},
                {"硬门槛": "核心或中军", "当前数据": role or "未分类", "通过": role_ok},
                {"硬门槛": "趋势或安全区合格", "当前数据": f"趋势{'是' if trend_ok else '否'} / 安全区{'是' if in_safe_zone else '否'}", "通过": trend_ok or in_safe_zone},
                {"硬门槛": "位置不过高", "当前数据": f"20日位置{position_20d:.0%}，当日{pct_chg:+.2f}%", "通过": position_ok or in_safe_zone},
                {"硬门槛": "量能承接≥0.75", "当前数据": f"{amount_ratio:.2f}", "通过": volume_ok or str(decision.get("path")) != "趋势回踩"},
                {"硬门槛": "收益风险比≥2", "当前数据": f"{rr:.2f}", "通过": rr >= 2},
                {"硬门槛": "止损距离≤8%", "当前数据": f"{((trigger-stop)/trigger*100 if trigger else 0):.2f}%", "通过": bool(trigger and (trigger-stop)/trigger*100 <= 8)},
                {"硬门槛": "未跌破深度安全区", "当前数据": f"深度下沿{deep_low:.2f}" if deep_low else "未配置", "通过": not below_safe},
            ]
        )
        gates["结果"] = gates["通过"].map({True: "通过", False: "不通过"})
        st.markdown("**硬门槛逐项检查**")
        st.dataframe(gates.drop(columns="通过"), use_container_width=True, hide_index=True)
        if vetoes:
            st.error("本次硬否决：" + "；".join(vetoes))

        support = max(ma20, current - atr * 1.2)
        plan = pd.DataFrame(
            [
                {"项目": "现价", "保存值": f"{current:.3f}", "计算依据": "当前股票行情"},
                {
                    "项目": "触发价",
                    "保存值": f"{trigger:.3f}",
                    "计算依据": (
                        "安全区内直接使用现价"
                        if str(decision.get("path")) == "安全区低吸"
                        else f"min(现价, max(MA20, 现价−1.2×ATR)×1.01)；支撑≈{support:.3f}"
                    ),
                },
                {
                    "项目": "止损价",
                    "保存值": f"{stop:.3f}",
                    "计算依据": (
                        "深度安全区下沿与现价−8%中取更严格值"
                        if str(decision.get("path")) == "安全区低吸"
                        else "max(支撑−1.2×ATR, 现价×0.92)"
                    ),
                },
                {"项目": "目标价", "保存值": f"{target:.3f}", "计算依据": "max(触发价+2.2R, 最近60日高点)"},
                {"项目": "收益风险比", "保存值": f"{rr:.2f}", "计算依据": "(目标−触发)÷(触发−止损)"},
                {"项目": "建议仓位", "保存值": f"{_number(decision.get('suggested_weight_pct')):.1f}%", "计算依据": "仅条件买/加仓时按1R和单票上限计算；否则归零"},
            ]
        )
        st.markdown("**交易价格与仓位计算**")
        st.dataframe(plan, use_container_width=True, hide_index=True)

        raw = pd.DataFrame(
            [
                {"字段": "数据日期", "数值": str(decision.get("evidence_time", snapshot.data_date)), "来源": str(price.get("source_used", "归档行情"))},
                {"字段": "现价", "数值": f"{current:.3f}", "来源": "股票日行情"},
                {"字段": "MA20 / MA60", "数值": f"{ma20:.3f} / {ma60:.3f}", "来源": "截至当日历史K线"},
                {"字段": "ATR14", "数值": f"{atr:.3f}", "来源": "截至当日历史K线"},
                {"字段": "20日位置", "数值": f"{position_20d:.0%}", "来源": "截至当日历史K线"},
                {"字段": "20日量能比", "数值": f"{amount_ratio:.2f}", "来源": "成交额÷20日均额"},
                {"字段": "安全区", "数值": f"{normal_low:.2f}–{normal_high:.2f}" if normal_low and normal_high else "未配置", "来源": "安全区配置"},
                {"字段": "历史K线文件", "数值": history_path or "未找到", "来源": "本地数据"},
            ]
        )
        st.markdown("**原始指标与来源**")
        st.dataframe(raw, use_container_width=True, hide_index=True)


def render_process_evidence(snapshot: DashboardSnapshot, selected: str, stock_token: str) -> None:
    if not selected:
        return
    if selected == "stock":
        render_stock_evidence(snapshot, stock_token)
        return
    with st.container(border=True):
        if selected == "data":
            _render_data_quality(snapshot)
        elif selected == "market":
            _render_market(snapshot)
        elif selected == "sector":
            _render_sector_rotation(snapshot)
        elif selected == "portfolio":
            _render_portfolio(snapshot)


def render_overview_evidence(snapshot: DashboardSnapshot, selected: str) -> None:
    renderers = {
        "market": _render_market,
        "sector": _render_sector,
        "decisions": _render_decisions,
        "portfolio": _render_portfolio,
    }
    renderer = renderers.get(selected)
    if renderer is None:
        return
    with st.container(border=True):
        renderer(snapshot)
