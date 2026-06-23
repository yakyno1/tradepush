from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

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
from tradepush.collectors.local import load_history
from tradepush.collectors.common import deduplicate_securities
from tradepush.services.dashboard import DashboardSnapshot
from tradepush.services.reconstruction import (
    available_reconstruction_dates,
    reconstruct_and_archive,
    reconstruct_range,
    reconstruction_date_bounds,
)
from tradepush.storage.snapshots import (
    latest_formal_close,
    list_snapshot_records,
    load_dashboard_snapshot,
    save_dashboard_snapshot,
    snapshot_calendar,
)
from tradepush.time_context import market_phase, snapshot_kind


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


class SnapshotTests(unittest.TestCase):
    def _snapshot(self, date_value: str = "2026-06-23") -> DashboardSnapshot:
        frame = pd.DataFrame([{"code": "300001", "name": "测试股票", "market": "A", "close": 100}])
        return DashboardSnapshot(
            market=MarketState("谨慎", 55, 50, .5, .2, 100, ["测试"]),
            sectors=pd.DataFrame([{"name": "半导体", "sector_state": "轮动观察"}]),
            sector_forecast=pd.DataFrame(),
            sector_horizon_forecasts=pd.DataFrame(),
            stock_forecasts=pd.DataFrame(),
            decisions=pd.DataFrame([{"code": "300001", "name": "测试股票", "action": "等待"}]),
            prices=frame,
            indices=pd.DataFrame([{"name": "上证指数", "close": 3000}]),
            positions=pd.DataFrame(),
            safety_zones=pd.DataFrame(),
            source_health=pd.DataFrame([{"source": "股票日行情", "status": "可用"}]),
            portfolio={"market_value": 0.0, "rows": []},
            account={"equity": 1_000_000, "confirmed": True},
            data_date=date_value,
            generated_at=f"{date_value}T15:30:00",
        )

    def test_snapshot_round_trip_and_calendar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            intraday = save_dashboard_snapshot(
                self._snapshot(),
                kind="intraday",
                formal=False,
                reason="测试盘中",
                origin="test",
                root=root,
            )
            close = save_dashboard_snapshot(
                self._snapshot(),
                kind="close",
                formal=True,
                reason="测试收盘",
                origin="test",
                root=root,
            )
            loaded = load_dashboard_snapshot(close)
            self.assertEqual(loaded.data_date, "2026-06-23")
            self.assertEqual(loaded.snapshot_label, "收盘正式版")
            self.assertEqual(loaded.decisions.iloc[0]["action"], "等待")
            self.assertEqual(len(list_snapshot_records(root)), 2)
            self.assertEqual(latest_formal_close(root=root).snapshot_id, close.snapshot_id)
            calendar = snapshot_calendar(root)
            self.assertEqual(int(calendar.iloc[0]["intraday_count"]), 1)
            self.assertTrue(bool(calendar.iloc[0]["formal_close"]))
            self.assertNotEqual(intraday.snapshot_id, close.snapshot_id)

    def test_market_phase_separates_intraday_and_close(self):
        self.assertEqual(market_phase(datetime(2026, 6, 23, 14, 30)), "盘中")
        self.assertEqual(snapshot_kind(datetime(2026, 6, 23, 14, 30)), "intraday")
        self.assertEqual(market_phase(datetime(2026, 6, 23, 16, 30)), "收盘后")
        self.assertEqual(snapshot_kind(datetime(2026, 6, 23, 16, 30)), "close")
        self.assertEqual(snapshot_kind(datetime(2026, 6, 27, 10, 0)), "close")

    def test_legacy_xueqiu_utc_bar_maps_to_shanghai_trade_date(self):
        history, _ = load_history("300308", "中际旭创", as_of="2026-06-22")
        self.assertFalse(history.empty)
        self.assertEqual(history["trade_date"].max(), pd.Timestamp("2026-06-22"))
        hsi, _ = load_history("HKHSI", "恒生指数", as_of="2026-06-22")
        self.assertFalse(hsi.empty)
        self.assertEqual(hsi["trade_date"].max(), pd.Timestamp("2026-06-22"))

    def test_reconstruction_service_handles_create_existing_and_closed_day(self):
        min_date, max_date = reconstruction_date_bounds()
        self.assertTrue(min_date)
        self.assertTrue(max_date)
        self.assertIn("2026-06-22", available_reconstruction_dates("2026-06-22", "2026-06-22"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = reconstruct_and_archive("2026-06-22", root=root)
            self.assertEqual(created.status, "CREATED")
            self.assertEqual(created.stocks, 49)
            self.assertEqual(created.indices, 7)
            existing = reconstruct_and_archive("2026-06-22", root=root)
            self.assertEqual(existing.status, "EXISTS")
            closed = reconstruct_and_archive("2026-06-20", root=root)
            self.assertEqual(closed.status, "SKIPPED")

    def test_reconstruction_range_enforces_ui_batch_limit(self):
        result = reconstruct_range(
            "2026-05-01",
            "2026-06-22",
            max_dates=2,
        )
        self.assertEqual(result.iloc[0]["status"], "ERROR")
        self.assertIn("超过单次上限", result.iloc[0]["message"])


class RuleTests(unittest.TestCase):
    def test_watchlist_deduplicates_by_market_and_code(self):
        rows = pd.DataFrame(
            [
                {"code": "600183", "market": "A", "name": "生益科技", "note": "first"},
                {"code": "600183", "market": "A", "name": "生益科技", "note": "duplicate"},
                {"code": "600183", "market": "HK", "name": "港股示例", "note": "different market"},
            ]
        )
        out = deduplicate_securities(rows)
        self.assertEqual(len(out), 2)
        self.assertEqual(out.iloc[0]["note"], "first")

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

    def test_unconfirmed_account_no_longer_a_veto(self):
        """Account confirmation is no longer a hard veto — removed per user request."""
        prices = pd.DataFrame(
            [
                {
                    "code": "300001",
                    "name": "测试股票",
                    "market": "A",
                    "track_level": "核心跟踪",
                    "base_theme": "半导体",
                    "trade_date": "2026-06-22",
                    "close": 100,
                    "pct_chg": 1,
                    "amount": 1_000_000,
                    "status": "OK",
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
            sectors=pd.DataFrame(),
            market_state=market,
            safety_zones=pd.DataFrame(),
            positions=pd.DataFrame(),
            account={
                "equity": 1_000_000,
                "risk_per_trade_pct": 1,
                "max_stock_pct": 20,
                "confirmed": False,
            },
            history_loader=lambda code, name: (history, None),
            data_date="2026-06-22",
        )
        # Account unconfirmed no longer blocks — it was removed as a hard veto
        self.assertNotIn("账户参数未确认", out.iloc[0]["hard_vetoes"])


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
