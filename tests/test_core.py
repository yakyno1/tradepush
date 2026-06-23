from __future__ import annotations

import json
import unittest

import numpy as np
import pandas as pd

from tradepush.ai.review import reconcile_reviews, validate_review
from tradepush.features.technical import enrich_history
from tradepush.features.forecasting import (
    build_sector_horizon_forecasts,
    forecast_stock,
)
from tradepush.models import MarketState
from tradepush.risk.positioning import calculate_position
from tradepush.rules.engine import build_decisions, classify_sectors, forecast_sectors
from tradepush.collectors.local import project_is_self_contained


class TechnicalTests(unittest.TestCase):
    def test_enrich_history_builds_moving_averages_and_atr(self):
        dates = pd.date_range("2026-01-01", periods=80, freq="B")
        close = pd.Series(range(100, 180), dtype=float)
        df = pd.DataFrame(
            {
                "trade_date": dates,
                "open": close - 1,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "amount": 1_000_000,
            }
        )
        out = enrich_history(df)
        self.assertIn("ma20", out)
        self.assertIn("atr14", out)
        self.assertGreater(out.iloc[-1]["ma20"], 0)


class ForecastTests(unittest.TestCase):
    def test_missing_stock_history_rejects_all_horizons(self):
        decision = pd.Series(
            {
                "code": "300001",
                "name": "测试股票",
                "market": "A",
                "theme": "测试",
                "sector_state": "轮动观察",
                "role": "核心",
                "current_price": 100,
                "stop_price": 94,
                "evidence_time": "2026-06-22",
            }
        )
        market = MarketState("进攻", 80, 85, .6, 1, 100, [])
        out = forecast_stock(decision, pd.DataFrame(), market, "2026-06-22")
        self.assertEqual(len(out), 3)
        self.assertTrue((out["result"] == "分析不出结果").all())

    def test_stock_forecast_exposes_confidence_and_audit(self):
        dates = pd.date_range("2024-01-01", periods=400, freq="B")
        close = pd.Series(np.linspace(50, 120, 400) + np.sin(np.arange(400) / 8), dtype=float)
        history = pd.DataFrame(
            {
                "trade_date": dates,
                "open": close * .995,
                "high": close * 1.02,
                "low": close * .98,
                "close": close,
                "volume": 1_000_000,
                "amount": 100_000_000,
            }
        )
        decision = pd.Series(
            {
                "code": "300001",
                "name": "测试股票",
                "market": "A",
                "theme": "半导体",
                "sector_state": "主线进攻",
                "role": "核心",
                "current_price": float(close.iloc[-1]),
                "stop_price": float(close.iloc[-1] * .94),
                "evidence_time": dates[-1].strftime("%Y-%m-%d"),
            }
        )
        market = MarketState("进攻", 80, 85, .6, 1, 100, [])
        out = forecast_stock(decision, history, market, dates[-1].strftime("%Y-%m-%d"))
        self.assertEqual(set(out["horizon"]), {"一周", "一个月", "三个月"})
        self.assertTrue((out["confidence"] >= 0).all())
        self.assertTrue(out["factor_details"].str.startswith("[").all())

    def test_sector_three_month_rejects_short_history(self):
        sectors = pd.DataFrame(
            [
                {
                    "name": "半导体",
                    "pct_chg": 2.0,
                    "net_amount": 30,
                    "amount": 500,
                    "leader": "测试股票",
                    "leader_pct": 5,
                    "sector_state": "主线进攻",
                    "strength_score": 60,
                }
            ]
        )
        history = []
        for index in range(15):
            date = pd.Timestamp("2026-06-01") + pd.Timedelta(days=index)
            history.append(
                (
                    pd.DataFrame(
                        [
                            {
                                "name": "半导体",
                                "pct_chg": 1.2,
                                "net_amount": 20,
                                "rank": 3,
                            }
                        ]
                    ),
                    f"sector_summary_{date:%Y%m%d}.csv",
                )
            )
        out = build_sector_horizon_forecasts(sectors, history)
        three_month = out[out["horizon"] == "三个月"].iloc[0]
        self.assertEqual(three_month["result"], "分析不出结果")


class RiskTests(unittest.TestCase):
    def test_a_share_position_rounds_to_100(self):
        result = calculate_position(
            equity=1_000_000,
            entry=100,
            stop=95,
            market="A",
            risk_per_trade_pct=1,
            max_stock_pct=20,
        )
        self.assertEqual(result["shares"] % 100, 0)
        self.assertLessEqual(result["planned_loss"], 10_000)
        self.assertLessEqual(result["weight_pct"], 20)


class RuleTests(unittest.TestCase):
    def test_project_data_paths_are_local(self):
        self.assertTrue(project_is_self_contained())

    def test_empty_or_unusable_prices_return_stable_schema(self):
        market = MarketState("谨慎", 55, 50, .5, 0, 100, [])
        out = build_decisions(
            prices=pd.DataFrame([{"code": "300001", "name": "空行情", "market": "A"}]),
            sectors=pd.DataFrame(),
            market_state=market,
            safety_zones=pd.DataFrame(),
            positions=pd.DataFrame(),
            account={"equity": 1_000_000},
            history_loader=lambda code, name: (pd.DataFrame(), None),
            data_date="2026-06-22",
        )
        self.assertTrue(out.empty)
        self.assertIn("gate_passed", out.columns)
        self.assertIn("score", out.columns)

    def test_sector_classification_requires_fund_confirmation(self):
        sectors = pd.DataFrame(
            [
                {
                    "name": "半导体",
                    "pct_chg": 3.2,
                    "net_amount": 88,
                    "amount": 500,
                    "leader": "龙头",
                    "leader_pct": 8,
                    "line_state": "强",
                },
                {
                    "name": "元件",
                    "pct_chg": 4.0,
                    "net_amount": -20,
                    "amount": 400,
                    "leader": "后排",
                    "leader_pct": 12,
                    "line_state": "资金背离",
                },
            ]
        )
        out = classify_sectors(sectors)
        semiconductor = out[out["name"] == "半导体"].iloc[0]
        component = out[out["name"] == "元件"].iloc[0]
        self.assertEqual(semiconductor["sector_state"], "主线进攻")
        self.assertNotEqual(component["transmission_status"], "已获资金验证")

    def test_sector_forecast_is_conditional_and_has_invalidation(self):
        current = pd.DataFrame(
            [
                {
                    "name": "半导体",
                    "pct_chg": 0.8,
                    "net_amount": 30,
                    "amount": 500,
                    "leader": "龙头",
                    "leader_pct": 3,
                    "line_state": "观察",
                }
            ]
        )
        history = [
            (
                pd.DataFrame([{"name": "半导体", "pct_chg": 1.0, "net_amount": 20, "rank": 3}]),
                "sector_summary_20260620.csv",
            ),
            (
                pd.DataFrame([{"name": "半导体", "pct_chg": 0.5, "net_amount": 10, "rank": 5}]),
                "sector_summary_20260619.csv",
            ),
            (
                pd.DataFrame([{"name": "半导体", "pct_chg": -0.2, "net_amount": 5, "rank": 8}]),
                "sector_summary_20260618.csv",
            ),
        ]
        out = forecast_sectors(current, history)
        self.assertEqual(out.iloc[0]["forecast_state"], "升温候选")
        self.assertTrue(out.iloc[0]["confirmation"])
        self.assertTrue(out.iloc[0]["invalidation"])
        self.assertEqual(out.iloc[0]["horizon"], "未来1–3个交易日")

    def test_hard_veto_overrides_high_score(self):
        prices = pd.DataFrame(
            [
                {
                    "code": "300001",
                    "name": "测试股票",
                    "market": "A",
                    "track_level": "核心跟踪",
                    "base_theme": "半导体",
                    "trade_date": "2026-06-22",
                    "open": 100,
                    "high": 105,
                    "low": 99,
                    "close": 104,
                    "pct_chg": 4,
                    "amount": 1_000_000,
                    "status": "OK",
                }
            ]
        )
        sectors = pd.DataFrame(
            [
                {
                    "name": "半导体",
                    "pct_chg": 4,
                    "net_amount": 100,
                    "amount": 500,
                    "leader": "测试股票",
                    "leader_pct": 4,
                    "line_state": "强",
                }
            ]
        )
        history = pd.DataFrame(
            {
                "trade_date": pd.date_range("2026-01-01", periods=80, freq="B"),
                "open": range(50, 130),
                "high": range(52, 132),
                "low": range(48, 128),
                "close": range(50, 130),
                "amount": 1_000_000,
            }
        )
        market = MarketState("进攻", 85, 85, .7, 1.2, 100, [])
        out = build_decisions(
            prices=prices,
            sectors=sectors,
            market_state=market,
            safety_zones=pd.DataFrame(),
            positions=pd.DataFrame(),
            account={"equity": 1_000_000, "risk_per_trade_pct": 1, "max_stock_pct": 20},
            history_loader=lambda code, name: (history, None),
            data_date="2026-06-22",
            global_vetoes=["板块资金流过期"],
        )
        self.assertNotEqual(out.iloc[0]["action"], "条件买")
        self.assertIn("板块资金流过期", out.iloc[0]["hard_vetoes"])


class AIReviewTests(unittest.TestCase):
    def test_ai_cannot_review_non_candidate(self):
        valid, errors = validate_review(
            {"reviews": [{"code": "999999", "action": "同意"}]},
            {"300001"},
        )
        self.assertFalse(valid)
        self.assertTrue(errors)

    def test_disagreement_downgrades_to_wait(self):
        decisions = pd.DataFrame([{"code": "300001", "name": "测试", "action": "条件买"}])
        out = reconcile_reviews(
            decisions,
            [{"code": "300001", "action": "同意", "reason": "通过"}],
            [{"code": "300001", "action": "否决", "reason": "事件风险"}],
        )
        self.assertEqual(out.iloc[0]["ai_final"], "等待")


if __name__ == "__main__":
    unittest.main()
