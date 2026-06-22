from __future__ import annotations

import numpy as np
import pandas as pd


def enrich_history(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy().sort_values("trade_date")
    close = pd.to_numeric(out["close"], errors="coerce")
    for window in (5, 10, 20, 60, 120):
        out[f"ma{window}"] = close.rolling(window, min_periods=min(window, 5)).mean()
        out[f"ret_{window}d"] = close.pct_change(window)
    for window in (20, 60):
        low = close.rolling(window, min_periods=min(window, 5)).min()
        high = close.rolling(window, min_periods=min(window, 5)).max()
        out[f"position_{window}d"] = ((close - low) / (high - low).replace(0, np.nan)).clip(0, 1)
    if {"high", "low"}.issubset(out.columns):
        high = pd.to_numeric(out["high"], errors="coerce")
        low = pd.to_numeric(out["low"], errors="coerce")
        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        out["atr14"] = tr.rolling(14, min_periods=5).mean()
        out["atr14_pct"] = out["atr14"] / close * 100
    if "amount" in out:
        amount = pd.to_numeric(out["amount"], errors="coerce")
        out["amount_ma20"] = amount.rolling(20, min_periods=5).mean()
        out["amount_ratio20"] = amount / out["amount_ma20"].replace(0, np.nan)
    return out


def latest_features(df: pd.DataFrame) -> dict:
    enriched = enrich_history(df)
    if enriched.empty:
        return {}
    row = enriched.iloc[-1]
    return {
        key: (None if pd.isna(value) else value)
        for key, value in row.to_dict().items()
    }


def relative_strength(stock_df: pd.DataFrame, benchmark_df: pd.DataFrame, window: int = 20) -> float | None:
    if stock_df.empty or benchmark_df.empty:
        return None
    s = stock_df[["trade_date", "close"]].dropna().copy()
    b = benchmark_df[["trade_date", "close"]].dropna().copy()
    merged = s.merge(b, on="trade_date", suffixes=("_stock", "_benchmark"))
    if len(merged) <= window:
        return None
    stock_ret = merged["close_stock"].iloc[-1] / merged["close_stock"].iloc[-1 - window] - 1
    bench_ret = merged["close_benchmark"].iloc[-1] / merged["close_benchmark"].iloc[-1 - window] - 1
    return float((stock_ret - bench_ret) * 100)

