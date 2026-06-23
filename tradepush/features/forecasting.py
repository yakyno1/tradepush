from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradepush.collectors.common import date_from_path, latest_date
from tradepush.features.technical import enrich_history
from tradepush.models import MarketState

HORIZONS = (
    {"horizon": "一周", "days": 5, "min_history": 80, "min_samples": 14},
    {"horizon": "一个月", "days": 20, "min_history": 160, "min_samples": 18},
    {"horizon": "三个月", "days": 60, "min_history": 260, "min_samples": 22},
)

CONFIDENCE_FLOOR = 60.0
CONVICTION_FLOOR = 55.0

STOCK_FORECAST_COLUMNS = [
    "code",
    "name",
    "market",
    "theme",
    "horizon",
    "horizon_days",
    "result",
    "direction",
    "expected_return_pct",
    "range_low_pct",
    "range_high_pct",
    "price_low",
    "price_mid",
    "price_high",
    "confidence",
    "conviction",
    "sample_count",
    "historical_hit_rate",
    "data_points",
    "latest_date",
    "forecast_score",
    "reason",
    "confirmation",
    "invalidation",
    "quality_flags",
    "factor_details",
]

SECTOR_FORECAST_COLUMNS = [
    "name",
    "horizon",
    "horizon_days",
    "result",
    "direction",
    "forecast_score",
    "confidence",
    "conviction",
    "observations",
    "coverage_pct",
    "avg_pct_chg",
    "positive_flow_rate",
    "reason",
    "confirmation",
    "invalidation",
    "quality_flags",
    "factor_details",
]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        output = float(value)
        return default if np.isnan(output) else output
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return float(np.clip(value, low, high))


def _tanh(value: float, scale: float) -> float:
    return float(np.tanh(value / scale)) if scale else 0.0


def _market_context(market: MarketState) -> float:
    label = str(market.label)
    if label == "进攻":
        return 0.75
    if label == "谨慎":
        return 0.05
    return -0.9


def _sector_context(state: str) -> float:
    return {
        "主线进攻": 0.8,
        "轮动观察": 0.25,
        "防守方向": 0.0,
        "权重护盘": -0.05,
        "退潮回避": -0.9,
    }.get(str(state), 0.0)


def _role_context(role: str) -> float:
    return {"核心": 0.55, "中军": 0.3, "次核心": 0.0}.get(str(role), -0.1)


def _prepare_history(history: pd.DataFrame, decision: pd.Series, data_date: str) -> pd.DataFrame:
    if history.empty:
        return history
    frame = history.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    current_date = pd.to_datetime(decision.get("evidence_time") or data_date, errors="coerce")
    latest_hist = frame["trade_date"].max()
    if pd.notna(current_date) and (pd.isna(latest_hist) or current_date > latest_hist):
        current = _number(decision.get("current_price"), 0)
        if current > 0:
            frame = pd.concat(
                [
                    frame,
                    pd.DataFrame(
                        [
                            {
                                "trade_date": current_date,
                                "open": current,
                                "high": current,
                                "low": current,
                                "close": current,
                                "volume": np.nan,
                                "amount": np.nan,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
    frame = enrich_history(frame)
    close = pd.to_numeric(frame["close"], errors="coerce")
    daily_return = close.pct_change(fill_method=None)
    frame["vol20"] = daily_return.rolling(20, min_periods=12).std() * math.sqrt(252)
    frame["vol60"] = daily_return.rolling(60, min_periods=30).std() * math.sqrt(252)
    for window in (20, 60, 120):
        ma = frame.get(f"ma{window}", close.rolling(window, min_periods=5).mean())
        frame[f"dist_ma{window}"] = close / ma.replace(0, np.nan) - 1
    frame["ma20_slope"] = frame["ma20"].pct_change(5, fill_method=None)
    frame["ma60_slope"] = frame["ma60"].pct_change(10, fill_method=None)
    return frame


def _stock_score_series(frame: pd.DataFrame, days: int) -> tuple[pd.Series, list[dict[str, Any]]]:
    if frame.empty:
        return pd.Series(dtype=float), []
    if days == 5:
        specs = [
            ("5日动量", "ret_5d", 0.06, 0.24),
            ("20日动量", "ret_20d", 0.14, 0.18),
            ("MA20偏离", "dist_ma20", 0.08, 0.19),
            ("MA20斜率", "ma20_slope", 0.035, 0.14),
            ("量能", "amount_ratio20", 0.8, 0.10, 1.0),
        ]
    elif days == 20:
        specs = [
            ("20日动量", "ret_20d", 0.16, 0.24),
            ("60日动量", "ret_60d", 0.30, 0.18),
            ("MA20偏离", "dist_ma20", 0.10, 0.15),
            ("MA20/MA60趋势", "ma20_vs_ma60", 0.10, 0.18),
            ("MA60斜率", "ma60_slope", 0.06, 0.10),
        ]
    else:
        specs = [
            ("60日动量", "ret_60d", 0.35, 0.24),
            ("120日动量", "ret_120d", 0.55, 0.18),
            ("MA60偏离", "dist_ma60", 0.16, 0.15),
            ("MA60/MA120趋势", "ma60_vs_ma120", 0.14, 0.18),
            ("MA60斜率", "ma60_slope", 0.08, 0.10),
        ]

    work = frame.copy()
    work["ma20_vs_ma60"] = work["ma20"] / work["ma60"].replace(0, np.nan) - 1
    work["ma60_vs_ma120"] = work["ma60"] / work["ma120"].replace(0, np.nan) - 1
    row = work.iloc[-1]
    score = pd.Series(0.0, index=work.index)
    factors: list[dict[str, Any]] = []
    for spec in specs:
        if len(spec) == 5:
            label, column, scale, weight, center = spec
        else:
            label, column, scale, weight = spec
            center = 0.0
        values = pd.to_numeric(work.get(column), errors="coerce")
        normalized = np.tanh((values - center) / scale)
        score = score + normalized.fillna(0) * weight
        current_value = _number(row.get(column), 0)
        current_normalized = _tanh(current_value - center, scale)
        factors.append(
            {
                "factor": label,
                "raw_value": round(current_value, 4),
                "normalized": round(current_normalized, 3),
                "weight": weight,
                "contribution": round(current_normalized * weight * 100, 1),
                "source": column,
            }
        )
    return score, factors


def _confidence_label(value: float) -> str:
    if value >= 80:
        return "高"
    if value >= 70:
        return "中高"
    if value >= CONFIDENCE_FLOOR:
        return "中"
    return "低"


def _direction_from_return(expected: float, days: int) -> str:
    strong = {5: 2.0, 20: 5.0, 60: 10.0}[days]
    weak = {5: 0.6, 20: 1.8, 60: 3.5}[days]
    if expected >= strong:
        return "看多"
    if expected >= weak:
        return "震荡偏多"
    if expected <= -strong:
        return "看空"
    if expected <= -weak:
        return "震荡偏弱"
    return "震荡"


def _empty_stock_forecast(
    decision: pd.Series,
    horizon: dict[str, Any],
    reason: str,
    data_points: int = 0,
    latest: str = "",
) -> dict[str, Any]:
    return {
        "code": str(decision.get("code", "")),
        "name": str(decision.get("name", "")),
        "market": str(decision.get("market", "")),
        "theme": str(decision.get("theme", "")),
        "horizon": horizon["horizon"],
        "horizon_days": horizon["days"],
        "result": "分析不出结果",
        "direction": "分析不出结果",
        "expected_return_pct": np.nan,
        "range_low_pct": np.nan,
        "range_high_pct": np.nan,
        "price_low": np.nan,
        "price_mid": np.nan,
        "price_high": np.nan,
        "confidence": 0.0,
        "conviction": 0.0,
        "sample_count": 0,
        "historical_hit_rate": np.nan,
        "data_points": data_points,
        "latest_date": latest,
        "forecast_score": 0.0,
        "reason": reason,
        "confirmation": "补齐历史数据并重新计算",
        "invalidation": "当前无有效预测，不应用于交易",
        "quality_flags": reason,
        "factor_details": "[]",
    }


def forecast_stock(
    decision: pd.Series,
    history: pd.DataFrame,
    market: MarketState,
    data_date: str,
) -> pd.DataFrame:
    latest = latest_date(history)
    rows: list[dict[str, Any]] = []
    if history.empty:
        return pd.DataFrame(
            [_empty_stock_forecast(decision, horizon, "缺少历史K线") for horizon in HORIZONS],
            columns=STOCK_FORECAST_COLUMNS,
        )
    frame = _prepare_history(history, decision, data_date)
    current_price = _number(decision.get("current_price"), 0)
    completeness_columns = ["close", "high", "low", "volume", "amount"]
    available_fields = sum(col in frame and frame[col].notna().mean() >= 0.7 for col in completeness_columns)
    field_completeness = available_fields / len(completeness_columns)

    for horizon in HORIZONS:
        days = int(horizon["days"])
        if len(frame) < int(horizon["min_history"]):
            rows.append(
                _empty_stock_forecast(
                    decision,
                    horizon,
                    f"历史仅{len(frame)}日，低于{horizon['min_history']}日门槛",
                    len(frame),
                    latest,
                )
            )
            continue

        base_series, factors = _stock_score_series(frame, days)
        if base_series.empty or pd.isna(base_series.iloc[-1]):
            rows.append(_empty_stock_forecast(decision, horizon, "技术特征无法计算", len(frame), latest))
            continue

        context_factors = [
            ("市场环境", _market_context(market), 0.08),
            ("板块状态", _sector_context(str(decision.get("sector_state", ""))), 0.08),
            ("个股地位", _role_context(str(decision.get("role", ""))), 0.04),
        ]
        current_score = float(base_series.iloc[-1])
        for label, normalized, weight in context_factors:
            current_score += normalized * weight
            factors.append(
                {
                    "factor": label,
                    "raw_value": round(normalized, 3),
                    "normalized": round(normalized, 3),
                    "weight": weight,
                    "contribution": round(normalized * weight * 100, 1),
                    "source": "规则上下文",
                }
            )
        current_score = _clip(current_score)

        future_return = pd.to_numeric(frame["close"], errors="coerce").shift(-days) / pd.to_numeric(
            frame["close"], errors="coerce"
        ) - 1
        calibration = pd.DataFrame({"score": base_series, "forward": future_return}).dropna()
        calibration["distance"] = (calibration["score"] - float(base_series.iloc[-1])).abs()
        similar = calibration[calibration["distance"] <= 0.22].copy()
        if len(similar) < int(horizon["min_samples"]):
            similar = calibration.sort_values("distance").head(max(int(horizon["min_samples"]), 24))
        if similar.empty:
            rows.append(_empty_stock_forecast(decision, horizon, "缺少可比历史样本", len(frame), latest))
            continue

        expected = float(similar["forward"].median() * 100)
        low = float(similar["forward"].quantile(0.25) * 100)
        high = float(similar["forward"].quantile(0.75) * 100)
        score_sign = np.sign(similar["score"])
        forward_sign = np.sign(similar["forward"])
        hit_rate = float((score_sign == forward_sign).mean() * 100)
        non_context = [factor for factor in factors if factor["source"] != "规则上下文"]
        directional_sign = np.sign(current_score)
        agreement = (
            sum(np.sign(factor["normalized"]) == directional_sign for factor in non_context) / len(non_context)
            if directional_sign and non_context
            else 0.0
        )
        conviction = 100 * (min(abs(current_score) / 0.55, 1) * 0.65 + agreement * 0.35)

        age_days = 0
        if latest:
            age_days = max((pd.Timestamp(data_date) - pd.Timestamp(latest)).days, 0)
        coverage_score = min(len(frame) / int(horizon["min_history"]), 1) * 25
        sample_score = min(len(similar) / 30, 1) * 25
        freshness_score = max(0, 1 - age_days / 15) * 15
        field_score = field_completeness * 15
        calibration_score = np.clip((hit_rate - 40) / 25, 0, 1) * 20
        confidence = float(coverage_score + sample_score + freshness_score + field_score + calibration_score)

        flags: list[str] = []
        if age_days > 5:
            flags.append(f"历史末日距行情日{age_days}天")
        if field_completeness < 0.8:
            flags.append("成交量/成交额字段覆盖不足")
        if len(similar) < int(horizon["min_samples"]):
            flags.append("相似历史样本不足")
        if np.sign(expected) != np.sign(current_score) and abs(expected) >= 0.5:
            conviction = max(conviction - 22, 0)
            flags.append("技术信号与历史样本收益方向冲突")

        confidence = round(float(np.clip(confidence, 0, 100)), 1)
        conviction = round(float(np.clip(conviction, 0, 100)), 1)
        expected = round(expected, 2)
        low, high = sorted((round(low, 2), round(high, 2)))
        direction = _direction_from_return(expected, days)
        result = direction
        reason = (
            f"历史相似样本{len(similar)}个，方向命中率{hit_rate:.0f}%；"
            f"置信度{_confidence_label(confidence)}，模型自信度{_confidence_label(conviction)}"
        )
        if confidence < CONFIDENCE_FLOOR or conviction < CONVICTION_FLOOR or direction == "震荡":
            result = "分析不出结果"
            direction = "分析不出结果"
            flags.append("置信度、自信度或方向强度低于输出门槛")
            reason = (
                f"证据不足：置信度{confidence:.0f}、模型自信度{conviction:.0f}；"
                "低于门槛时拒绝给出方向"
            )

        if days == 5:
            confirmation = "收盘守住MA20，量能不低于20日均量的80%，板块不转弱"
            invalidation = f"跌破{_number(decision.get('stop_price'), current_price * 0.94):.2f}或板块转入退潮"
        elif days == 20:
            confirmation = "MA20保持上行且价格不跌破MA60，板块资金持续为正"
            invalidation = "MA20下穿MA60，或20日相对强度显著转负"
        else:
            confirmation = "MA60保持上行、价格位于MA120上方，并有基本面/产业证据配合"
            invalidation = "MA60下穿MA120，或长期趋势与产业逻辑同时转弱"

        price_low = current_price * (1 + low / 100) if current_price else np.nan
        price_mid = current_price * (1 + expected / 100) if current_price else np.nan
        price_high = current_price * (1 + high / 100) if current_price else np.nan
        rows.append(
            {
                "code": str(decision.get("code", "")),
                "name": str(decision.get("name", "")),
                "market": str(decision.get("market", "")),
                "theme": str(decision.get("theme", "")),
                "horizon": horizon["horizon"],
                "horizon_days": days,
                "result": result,
                "direction": direction,
                "expected_return_pct": expected,
                "range_low_pct": low,
                "range_high_pct": high,
                "price_low": round(price_low, 3) if pd.notna(price_low) else np.nan,
                "price_mid": round(price_mid, 3) if pd.notna(price_mid) else np.nan,
                "price_high": round(price_high, 3) if pd.notna(price_high) else np.nan,
                "confidence": confidence,
                "conviction": conviction,
                "sample_count": len(similar),
                "historical_hit_rate": round(hit_rate, 1),
                "data_points": len(frame),
                "latest_date": latest,
                "forecast_score": round(current_score * 100, 1),
                "reason": reason,
                "confirmation": confirmation,
                "invalidation": invalidation,
                "quality_flags": "；".join(flags),
                "factor_details": json.dumps(factors, ensure_ascii=False),
            }
        )
    return pd.DataFrame(rows, columns=STOCK_FORECAST_COLUMNS)


def build_stock_forecasts(
    decisions: pd.DataFrame,
    history_loader,
    market: MarketState,
    data_date: str,
) -> pd.DataFrame:
    if decisions.empty:
        return pd.DataFrame(columns=STOCK_FORECAST_COLUMNS)
    frames: list[pd.DataFrame] = []
    for _, decision in decisions.iterrows():
        history, _ = history_loader(str(decision.get("code", "")), str(decision.get("name", "")))
        frames.append(forecast_stock(decision, history, market, data_date))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=STOCK_FORECAST_COLUMNS)


def _sector_timeseries(history: list[tuple[pd.DataFrame, Path]]) -> tuple[pd.DataFrame, int]:
    rows: list[pd.DataFrame] = []
    snapshot_dates: set[str] = set()
    for frame, path in history:
        if frame.empty or "name" not in frame:
            continue
        date_value = latest_date(frame) or date_from_path(Path(path))
        if not date_value:
            continue
        snapshot_dates.add(date_value)
        work = frame.copy()
        for col in ("pct_chg", "net_amount", "rank"):
            work[col] = pd.to_numeric(work.get(col), errors="coerce")
        work["snapshot_date"] = pd.Timestamp(date_value)
        work = (
            work.groupby(["snapshot_date", "name"], as_index=False)
            .agg(pct_chg=("pct_chg", "mean"), net_amount=("net_amount", "mean"), rank=("rank", "min"))
        )
        rows.append(work)
    if not rows:
        return pd.DataFrame(), 0
    return pd.concat(rows, ignore_index=True).sort_values(["name", "snapshot_date"]), len(snapshot_dates)


def _sector_horizon(
    row: pd.Series,
    series: pd.DataFrame,
    snapshot_count: int,
    horizon: dict[str, Any],
) -> dict[str, Any]:
    days = int(horizon["days"])
    required = {5: 4, 20: 12, 60: 35}[days]
    name = str(row.get("name", ""))
    own = series[series["name"].astype(str) == name].tail(days) if not series.empty else pd.DataFrame()
    observations = len(own)
    coverage = observations / min(days, max(snapshot_count, 1)) * 100
    if observations < required:
        reason = f"仅有{observations}个有效快照，低于{required}个门槛"
        return {
            "name": name,
            "horizon": horizon["horizon"],
            "horizon_days": days,
            "result": "分析不出结果",
            "direction": "分析不出结果",
            "forecast_score": 0.0,
            "confidence": round(min(coverage, 59), 1),
            "conviction": 0.0,
            "observations": observations,
            "coverage_pct": round(coverage, 1),
            "avg_pct_chg": np.nan,
            "positive_flow_rate": np.nan,
            "reason": reason,
            "confirmation": "积累更多连续板块快照后重新计算",
            "invalidation": "当前无有效预测，不应用于交易",
            "quality_flags": "板块历史覆盖不足",
            "factor_details": "[]",
        }

    pct = pd.to_numeric(own["pct_chg"], errors="coerce").dropna()
    flow = pd.to_numeric(own["net_amount"], errors="coerce").dropna()
    rank = pd.to_numeric(own["rank"], errors="coerce").dropna()
    avg_pct = float(pct.mean()) if not pct.empty else 0.0
    median_pct = float(pct.median()) if not pct.empty else 0.0
    positive_price_rate = float((pct > 0).mean()) if not pct.empty else 0.0
    positive_flow_rate = float((flow > 0).mean()) if not flow.empty else 0.0
    avg_flow = float(flow.mean()) if not flow.empty else 0.0
    recent = float(pct.tail(min(3, len(pct))).mean()) if not pct.empty else 0.0
    prior = float(pct.iloc[-6:-3].mean()) if len(pct) >= 6 else avg_pct
    acceleration = recent - prior
    rank_strength = 1 - min(float(rank.median()) / 100, 1) if not rank.empty else 0.0
    current_strength = _number(row.get("strength_score"), 0)
    factors = [
        ("当日强度", _tanh(current_strength, 45), 0.24),
        ("平均涨跌", _tanh(avg_pct, 2.5), 0.20),
        ("资金持续", positive_flow_rate * 2 - 1, 0.20),
        ("价格持续", positive_price_rate * 2 - 1, 0.16),
        ("动量加速度", _tanh(acceleration, 2.0), 0.10),
        ("排名持续", rank_strength * 2 - 1, 0.10),
    ]
    score = sum(normalized * weight for _, normalized, weight in factors)
    directional_sign = np.sign(score)
    agreement = (
        sum(np.sign(normalized) == directional_sign for _, normalized, _ in factors) / len(factors)
        if directional_sign
        else 0
    )
    conviction = 100 * (min(abs(score) / 0.55, 1) * 0.65 + agreement * 0.35)
    consistency = (positive_price_rate + positive_flow_rate) / 2
    confidence = (
        min(coverage / 100, 1) * 35
        + min(observations / max(required * 1.5, 1), 1) * 25
        + consistency * 25
        + (1 if not pct.empty and not flow.empty else 0) * 15
    )
    confidence = round(float(np.clip(confidence, 0, 100)), 1)
    conviction = round(float(np.clip(conviction, 0, 100)), 1)
    expected_proxy = median_pct * min(math.sqrt(days), 4)
    direction = _direction_from_return(expected_proxy, days)
    result = direction
    flags: list[str] = []
    if coverage < 70:
        flags.append("历史快照覆盖偏低")
    if confidence < CONFIDENCE_FLOOR or conviction < CONVICTION_FLOOR or direction == "震荡":
        result = "分析不出结果"
        direction = "分析不出结果"
        flags.append("置信度、自信度或方向强度低于输出门槛")
        reason = f"证据不足：置信度{confidence:.0f}、模型自信度{conviction:.0f}"
    else:
        reason = (
            f"{observations}个快照中价格上涨率{positive_price_rate:.0%}、"
            f"资金净流入率{positive_flow_rate:.0%}，平均涨跌{avg_pct:+.2f}%"
        )
    factor_details = [
        {
            "factor": label,
            "normalized": round(normalized, 3),
            "weight": weight,
            "contribution": round(normalized * weight * 100, 1),
        }
        for label, normalized, weight in factors
    ]
    return {
        "name": name,
        "horizon": horizon["horizon"],
        "horizon_days": days,
        "result": result,
        "direction": direction,
        "forecast_score": round(score * 100, 1),
        "confidence": confidence,
        "conviction": conviction,
        "observations": observations,
        "coverage_pct": round(coverage, 1),
        "avg_pct_chg": round(avg_pct, 2),
        "positive_flow_rate": round(positive_flow_rate * 100, 1),
        "reason": reason,
        "confirmation": "价格与净流入方向继续一致，领涨结构没有明显退化",
        "invalidation": "净流入连续转负、价格趋势反向，或领涨股显著走弱",
        "quality_flags": "；".join(flags),
        "factor_details": json.dumps(factor_details, ensure_ascii=False),
    }


def build_sector_horizon_forecasts(
    sectors: pd.DataFrame,
    history: list[tuple[pd.DataFrame, Path]],
) -> pd.DataFrame:
    if sectors.empty:
        return pd.DataFrame(columns=SECTOR_FORECAST_COLUMNS)
    series, snapshot_count = _sector_timeseries(history)
    rows: list[dict[str, Any]] = []
    for _, row in sectors.iterrows():
        for horizon in HORIZONS:
            rows.append(_sector_horizon(row, series, snapshot_count, horizon))
    return pd.DataFrame(rows, columns=SECTOR_FORECAST_COLUMNS)


def sector_history_for_name(
    name: str,
    history: list[tuple[pd.DataFrame, Path]],
) -> pd.DataFrame:
    series, _ = _sector_timeseries(history)
    if series.empty:
        return series
    return series[series["name"].astype(str) == str(name)].sort_values("snapshot_date").reset_index(drop=True)


def parse_factor_details(value: str) -> pd.DataFrame:
    try:
        rows = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        rows = []
    return pd.DataFrame(rows)


def related_stocks_for_sector(
    sector_name: str,
    leader: str,
    decisions: pd.DataFrame,
) -> pd.DataFrame:
    if decisions.empty:
        return pd.DataFrame()
    clean = str(sector_name)
    for suffix in ("概念", "行业", "设备", "服务", "Ⅱ", "Ⅲ"):
        clean = clean.replace(suffix, "")
    aliases = {
        "半导体": ["半导体", "芯片", "存储", "CPU", "晶圆", "封装"],
        "计算机": ["AI", "服务器", "算力", "软件", "互联网", "数据中心"],
        "通信": ["通信", "光模块", "CPO", "光芯片", "PCB"],
        "消费电子": ["消费电子", "手机", "智能硬件", "光学"],
        "机器人": ["机器人", "减速器", "丝杠", "电机"],
        "证券": ["券商", "证券", "金融"],
        "券商": ["券商", "证券", "金融"],
        "汽车": ["汽车", "智能驾驶", "新能源车"],
        "医药": ["医药", "生物", "创新药"],
    }
    tokens = [clean] if len(clean) >= 2 else []
    for key, values in aliases.items():
        if key in sector_name or sector_name in key:
            tokens.extend(values)
    tokens = list(dict.fromkeys(token for token in tokens if token))
    rows: list[dict[str, Any]] = []
    for _, decision in decisions.iterrows():
        theme = str(decision.get("theme", ""))
        name = str(decision.get("name", ""))
        reasons: list[str] = []
        if leader and name == str(leader):
            reasons.append("板块领涨股")
        matched = [token for token in tokens if token in theme or token in name]
        if matched:
            reasons.append(f"主题关键词：{'/'.join(matched)}")
        if not reasons:
            continue
        record = decision.to_dict()
        record["match_reason"] = "；".join(reasons)
        rows.append(record)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["gate_passed", "score"], ascending=[False, False])
