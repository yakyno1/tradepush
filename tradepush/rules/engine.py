from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from tradepush.features.technical import latest_features
from tradepush.models import MarketState, TradeDecision
from tradepush.risk.positioning import calculate_position

SECTOR_POSITIVE = {"强", "主线进攻"}
ACTION_BUY = "条件买"
DECISION_COLUMNS = [
    "code",
    "name",
    "market",
    "theme",
    "sector_state",
    "role",
    "path",
    "action",
    "current_price",
    "trigger_price",
    "stop_price",
    "target_price",
    "risk_reward",
    "suggested_weight_pct",
    "suggested_shares",
    "score",
    "gate_passed",
    "cancel_condition",
    "reasons",
    "hard_vetoes",
    "evidence_time",
]


def empty_decisions() -> pd.DataFrame:
    return pd.DataFrame(columns=DECISION_COLUMNS)


def _number(value, default=0.0) -> float:
    try:
        result = float(value)
        return default if np.isnan(result) else result
    except (TypeError, ValueError):
        return default


def evaluate_market(indices: pd.DataFrame, prices: pd.DataFrame, sectors: pd.DataFrame) -> MarketState:
    score = 0.0
    reasons: list[str] = []

    index_pct = pd.to_numeric(indices.get("pct_chg", pd.Series(dtype=float)), errors="coerce").dropna()
    index_avg = float(index_pct.mean()) if not index_pct.empty else 0.0
    positive_indices = float((index_pct > 0).mean()) if not index_pct.empty else 0.0
    score += max(min(20 + index_avg * 8, 30), 0)
    score += positive_indices * 15
    reasons.append(f"指数平均涨跌 {index_avg:+.2f}%")

    stock_pct = pd.to_numeric(prices.get("pct_chg", pd.Series(dtype=float)), errors="coerce").dropna()
    breadth = float((stock_pct > 0).mean()) if not stock_pct.empty else 0.0
    score += breadth * 25
    reasons.append(f"自选池上涨占比 {breadth:.0%}")

    above_ma20 = positive_indices
    if {"close", "ma20"}.issubset(indices.columns):
        close = pd.to_numeric(indices["close"], errors="coerce")
        ma20 = pd.to_numeric(indices["ma20"], errors="coerce")
        valid = (close.notna() & ma20.notna())
        above_ma20 = float((close[valid] >= ma20[valid]).mean()) if valid.any() else 0.0
    score += above_ma20 * 20
    reasons.append(f"指数站上MA20比例 {above_ma20:.0%}")

    sector_strength = 0.0
    if not sectors.empty:
        line = sectors.get("line_state", pd.Series("", index=sectors.index)).astype(str)
        pct = pd.to_numeric(sectors.get("pct_chg", 0), errors="coerce").fillna(0)
        sector_strength = min(float(((line == "强") | (pct >= 2)).mean()), 1.0)
    score += sector_strength * 10
    reasons.append(f"强势板块覆盖 {sector_strength:.0%}")

    required = [not indices.empty, not prices.empty, not sectors.empty]
    data_quality = sum(required) / len(required) * 100
    if data_quality < 100:
        score -= 10
        reasons.append("核心数据存在缺口，市场评级降级")

    score = round(max(min(score, 100), 0), 1)
    if score >= 70:
        label, exposure = "进攻", 85.0
    elif score >= 45:
        label, exposure = "谨慎", 50.0
    else:
        label, exposure = "停止新开仓", 0.0
    return MarketState(label, score, exposure, breadth, index_avg, data_quality, reasons)


def classify_sectors(sectors: pd.DataFrame) -> pd.DataFrame:
    if sectors.empty:
        return pd.DataFrame(
            columns=[
                "name", "pct_chg", "net_amount", "amount", "leader", "leader_pct",
                "sector_state", "strength_score", "transmission_status",
            ]
        )
    out = sectors.copy()
    for col in ("pct_chg", "net_amount", "amount", "leader_pct"):
        out[col] = pd.to_numeric(out.get(col, 0), errors="coerce").fillna(0)
    line = out.get("line_state", pd.Series("", index=out.index)).astype(str)
    net_norm = np.tanh(out["net_amount"] / 100)
    out["strength_score"] = (
        out["pct_chg"].clip(-8, 8) * 6
        + net_norm * 25
        + out["leader_pct"].clip(-20, 20) * 1.2
    ).round(1)
    out["_strength_rank"] = out["strength_score"].rank(method="first", ascending=False)
    mainline_limit = max(5, min(20, int(np.ceil(len(out) * 0.10))))

    conditions = [
        (out["_strength_rank"] <= mainline_limit)
        & (out["pct_chg"] >= 1.5)
        & (out["net_amount"] > 0)
        & (line != "资金背离"),
        (out["pct_chg"] > 0) & (out["net_amount"] >= 0),
        (out["pct_chg"] > 0) & (out["net_amount"] < 0),
        (out["pct_chg"] <= -1.5) | ((out["pct_chg"] < 0) & (out["net_amount"] < 0)),
    ]
    choices = ["主线进攻", "轮动观察", "权重护盘", "退潮回避"]
    out["sector_state"] = np.select(conditions, choices, default="防守方向")
    out["transmission_status"] = np.where(
        (out["sector_state"] == "主线进攻") & (out["net_amount"] > 0),
        "已获资金验证",
        "待验证",
    )
    return out.drop(columns="_strength_rank").sort_values(
        ["strength_score", "pct_chg"], ascending=False
    ).reset_index(drop=True)


FORECAST_COLUMNS = [
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
]


def forecast_sectors(
    sectors: pd.DataFrame,
    history: list[tuple[pd.DataFrame, object]] | None = None,
) -> pd.DataFrame:
    """Build a conditional 1-3 session outlook, not a deterministic prediction."""
    current = classify_sectors(sectors)
    if current.empty:
        return pd.DataFrame(columns=FORECAST_COLUMNS)

    snapshots = history or []
    history_rows: list[dict] = []
    for frame, path in snapshots:
        if frame.empty or "name" not in frame:
            continue
        date_text = str(path)
        for _, row in frame.drop_duplicates("name").iterrows():
            history_rows.append(
                {
                    "name": str(row.get("name", "")),
                    "date": date_text,
                    "pct_chg": _number(row.get("pct_chg"), 0),
                    "net_amount": _number(row.get("net_amount"), 0),
                    "rank": _number(row.get("rank"), 99),
                }
            )
    hist = pd.DataFrame(history_rows)
    rows: list[dict] = []
    enough_history = len(snapshots) >= 3

    for _, row in current.iterrows():
        name = str(row.get("name", ""))
        pct_chg = _number(row.get("pct_chg"), 0)
        net_amount = _number(row.get("net_amount"), 0)
        leader_pct = _number(row.get("leader_pct"), 0)
        strength = _number(row.get("strength_score"), 0)
        own = hist[hist["name"] == name] if not hist.empty else pd.DataFrame()
        hits = int(own["date"].nunique()) if not own.empty else 0
        positive_rate = float((own["pct_chg"] > 0).mean()) if not own.empty else 0.0
        inflow_rate = float((own["net_amount"] > 0).mean()) if not own.empty else 0.0
        persistence = min(hits, 5) * 3 + positive_rate * 8 + inflow_rate * 9
        score = 50 + np.clip(strength, -40, 40) * 0.55 + persistence

        if pct_chg >= 1.5 and net_amount > 0 and score >= 68:
            state = "延续候选"
            why = "价格、资金与领涨结构共振，历史出现频率提供延续加分"
        elif -0.8 <= pct_chg < 1.8 and net_amount > 0:
            state = "升温候选"
            score += 5
            why = "涨幅尚未过热但已有净流入，具备从观察区向主线升级的条件"
        elif pct_chg < 0 and net_amount > 0:
            state = "修复观察"
            why = "价格仍弱但资金先行回流，只能等待价格确认"
        elif net_amount < 0 or pct_chg <= -1.5:
            state = "转弱风险"
            score -= 12
            why = "价格或资金至少一项转弱，优先防范冲高回落和后排补跌"
        else:
            state = "中性观察"
            why = "当前证据不足以支持升级，等待资金和价格同时改善"

        score += np.clip(leader_pct, -10, 15) * 0.35
        score = round(float(np.clip(score, 0, 100)), 1)
        if enough_history and hits >= 3:
            confidence = "中高"
        elif enough_history and hits >= 1:
            confidence = "中"
        else:
            confidence = "低（历史不足）"

        confirmation = "下一交易日继续净流入，板块收涨且领涨股不跌破当日低点"
        invalidation = "净流入转为明显流出，板块跌幅超过2%，或领涨股转弱"
        if state == "修复观察":
            confirmation = "板块翻红并站上前一日高点，同时净流入保持为正"
        elif state == "转弱风险":
            confirmation = "仅在资金重新转正且板块收复前一日跌幅后重新评估"
            invalidation = "继续净流出或跌幅扩大，维持回避"

        rows.append(
            {
                "name": name,
                "forecast_state": state,
                "forecast_score": score,
                "confidence": confidence,
                "current_state": str(row.get("sector_state", "")),
                "pct_chg": round(pct_chg, 2),
                "net_amount": round(net_amount, 2),
                "history_hits": hits,
                "why": why,
                "confirmation": confirmation,
                "invalidation": invalidation,
                "horizon": "未来1–3个交易日",
            }
        )

    output = pd.DataFrame(rows)
    priority = {"升温候选": 0, "延续候选": 1, "修复观察": 2, "中性观察": 3, "转弱风险": 4}
    output["_priority"] = output["forecast_state"].map(priority).fillna(9)
    output = output.sort_values(["_priority", "forecast_score"], ascending=[True, False]).drop(columns="_priority")
    output["forecast_rank"] = range(1, len(output) + 1)
    return output[FORECAST_COLUMNS].reset_index(drop=True)


def _sector_for_stock(theme: str, sectors: pd.DataFrame) -> tuple[str, str]:
    if sectors.empty:
        return "轮动观察", "无匹配板块"
    theme_text = str(theme)
    tokens = [x.strip() for x in theme_text.replace("／", "/").split("/") if len(x.strip()) >= 2]
    for _, row in sectors.iterrows():
        name = str(row.get("name", ""))
        if any(token in name or name in token for token in tokens):
            return str(row.get("sector_state", "轮动观察")), name
    strongest = sectors.iloc[0]
    return "轮动观察", str(strongest.get("name", "市场强势方向"))


def _role(track_level: str) -> str:
    text = str(track_level)
    if "核心" in text:
        return "核心"
    if "重点" in text:
        return "中军"
    return "次核心"


def _position_row(positions: pd.DataFrame, code: str, market: str) -> pd.Series | None:
    if positions.empty or "code" not in positions:
        return None
    width = 5 if market == "HK" else 6
    normalized = positions["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(width)
    found = positions.loc[normalized == str(code).zfill(width)]
    return found.iloc[0] if not found.empty else None


def build_decisions(
    prices: pd.DataFrame,
    sectors: pd.DataFrame,
    market_state: MarketState,
    safety_zones: pd.DataFrame,
    positions: pd.DataFrame,
    account: dict,
    history_loader,
    data_date: str,
    global_vetoes: list[str] | None = None,
) -> pd.DataFrame:
    if prices.empty:
        return empty_decisions()
    sector_signals = classify_sectors(sectors)
    safety = safety_zones.copy()
    if not safety.empty:
        safety["code"] = safety["code"].astype(str).str.replace(r"\.0$", "", regex=True)

    decisions: list[dict] = []
    for _, row in prices.iterrows():
        market = str(row.get("market", "A")).upper()
        width = 5 if market == "HK" else 6
        code = str(row.get("code", "")).replace(".0", "").zfill(width)
        name = str(row.get("name", code))
        theme = str(row.get("base_theme", "未分类"))
        current = _number(row.get("close"), None)
        if current is None or current <= 0:
            continue

        hist, _ = history_loader(code, name)
        if not hist.empty:
            latest_hist_date = pd.to_datetime(hist["trade_date"], errors="coerce").max()
            current_date = pd.to_datetime(row.get("trade_date") or data_date, errors="coerce")
            if pd.notna(current_date) and (pd.isna(latest_hist_date) or current_date > latest_hist_date):
                current_row = pd.DataFrame(
                    [
                        {
                            "trade_date": current_date,
                            "open": _number(row.get("open"), current),
                            "high": _number(row.get("high"), current),
                            "low": _number(row.get("low"), current),
                            "close": current,
                            "volume": _number(row.get("volume"), 0),
                            "amount": _number(row.get("amount"), 0),
                        }
                    ]
                )
                hist = pd.concat([hist, current_row], ignore_index=True)
        feat = latest_features(hist)
        ma20 = _number(row.get("ma20"), _number(feat.get("ma20"), current))
        ma60 = _number(row.get("ma60"), _number(feat.get("ma60"), current))
        atr = _number(feat.get("atr14"), current * 0.035)
        amount_ratio = _number(feat.get("amount_ratio20"), 1.0)
        pos20 = _number(row.get("position_20d"), _number(feat.get("position_20d"), 0.5))
        pct_chg = _number(row.get("pct_chg"), 0.0)
        role = _role(row.get("track_level", ""))
        sector_state, matched_sector = _sector_for_stock(theme, sector_signals)

        zone_row = None
        if not safety.empty:
            candidates = safety[safety["code"].str.zfill(width) == code]
            if not candidates.empty:
                zone_row = candidates.iloc[0]
        normal_low = _number(zone_row.get("normal_safe_low"), None) if zone_row is not None else None
        normal_high = _number(zone_row.get("normal_safe_high"), None) if zone_row is not None else None
        deep_low = _number(zone_row.get("deep_safe_low"), None) if zone_row is not None else None
        in_safe_zone = bool(normal_low and normal_high and normal_low <= current <= normal_high)
        below_safe = bool(deep_low and current < deep_low)

        trend_ok = current >= ma20 and ma20 >= ma60 * 0.97
        position_ok = pos20 <= 0.88 or pct_chg <= 2
        volume_ok = amount_ratio >= 0.75
        role_ok = role in {"核心", "中军"}

        if in_safe_zone:
            path = "安全区低吸"
            trigger = current
            stop = max((deep_low or current * 0.92) * 0.98, current * 0.92)
        else:
            path = "趋势回踩"
            support = max(ma20, current - atr * 1.2)
            trigger = min(current, support * 1.01)
            stop = max(support - atr * 1.2, current * 0.92)
        if stop >= trigger:
            stop = trigger * 0.94
        risk = trigger - stop
        recent_high = _number(feat.get("high"), current)
        if not hist.empty and "high" in hist:
            recent_high = _number(pd.to_numeric(hist["high"], errors="coerce").tail(60).max(), current)
        target = max(trigger + risk * 2.2, recent_high)
        rr = (target - trigger) / risk if risk > 0 else 0.0
        stop_pct = risk / trigger * 100 if trigger else 100.0

        vetoes: list[str] = list(global_vetoes or [])
        if not bool(account.get("confirmed", False)):
            vetoes.append("账户参数未确认")
        status = str(row.get("status", ""))
        if "ERROR" in status or "FAIL" in status:
            vetoes.append("行情状态异常")
        if market_state.label == "停止新开仓":
            vetoes.append("市场停止新开仓")
        if sector_state == "退潮回避" and not (role == "核心" and in_safe_zone):
            vetoes.append("板块退潮")
        if not role_ok:
            vetoes.append("个股不是核心或中军")
        if not (trend_ok or in_safe_zone):
            vetoes.append("趋势与安全区均不合格")
        if not position_ok and not in_safe_zone:
            vetoes.append("位置过高")
        if not volume_ok and path == "趋势回踩":
            vetoes.append("量能承接不足")
        if rr < 2:
            vetoes.append("收益风险比不足2")
        if stop_pct > 8:
            vetoes.append("止损距离超过8%")
        if below_safe:
            vetoes.append("跌破深度安全区，需先排查新风险")

        score = 45.0
        score += 15 if market_state.label == "进攻" else 5 if market_state.label == "谨慎" else -20
        score += 15 if sector_state == "主线进攻" else 7 if sector_state == "轮动观察" else -8
        score += 15 if role == "核心" else 10 if role == "中军" else 0
        score += 10 if trend_ok else 0
        score += 10 if in_safe_zone else 0
        score += min(max((rr - 1) * 5, 0), 10)
        score -= len(vetoes) * 8
        score = round(max(min(score, 100), 0), 1)

        holding = _position_row(positions, code, market)
        if holding is not None:
            cost = _number(holding.get("cost"), current)
            if current <= stop:
                action = "清仓"
            elif current >= target:
                action = "减仓"
            elif not vetoes and current <= trigger * 1.01:
                action = "加仓"
            else:
                action = "持有"
        else:
            if not vetoes and score >= 75:
                action = ACTION_BUY
            elif score < 45 or len(vetoes) >= 3:
                action = "禁止买入"
            else:
                action = "等待"

        sizing = calculate_position(
            equity=_number(account.get("equity"), 0),
            entry=trigger,
            stop=stop,
            market=market,
            risk_per_trade_pct=_number(account.get("risk_per_trade_pct"), 1),
            max_stock_pct=_number(account.get("max_stock_pct"), 20),
        )
        if action not in {ACTION_BUY, "加仓"}:
            sizing = {**sizing, "shares": 0, "weight_pct": 0.0}

        reasons = [
            f"匹配板块：{matched_sector}（{sector_state}）",
            f"MA20/MA60：{ma20:.2f}/{ma60:.2f}",
            f"20日位置：{pos20:.0%}",
            f"量能比：{amount_ratio:.2f}",
        ]
        decisions.append(
            TradeDecision(
                code=code,
                name=name,
                market=market,
                theme=theme,
                sector_state=sector_state,
                role=role,
                path=path,
                action=action,
                current_price=round(current, 3),
                trigger_price=round(trigger, 3),
                stop_price=round(stop, 3),
                target_price=round(target, 3),
                risk_reward=round(rr, 2),
                suggested_weight_pct=float(sizing["weight_pct"]),
                suggested_shares=int(sizing["shares"]),
                score=score,
                gate_passed=not vetoes,
                cancel_condition="跌破失效位、板块转退潮、重大风险或数据失真",
                reasons=reasons,
                hard_vetoes=vetoes,
                evidence_time=data_date or date.today().isoformat(),
            ).to_dict()
        )
    if not decisions:
        return empty_decisions()
    return pd.DataFrame(decisions, columns=DECISION_COLUMNS).sort_values(
        ["gate_passed", "score"], ascending=[False, False]
    ).reset_index(drop=True)
