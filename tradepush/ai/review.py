from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

ALLOWED_AI_ACTIONS = {"同意", "否决", "降级等待"}


def build_ai_packet(
    market: dict,
    sectors: pd.DataFrame,
    decisions: pd.DataFrame,
    source_health: pd.DataFrame,
) -> dict:
    candidates = decisions[decisions["action"].isin(["条件买", "加仓"])].copy()
    return {
        "schema": "tradepush.ai_review.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "instruction": (
            "只复核候选逻辑。你可以同意、否决或降级等待；不得修改行情价格、"
            "止损、仓位上限和硬门槛。"
        ),
        "market": market,
        "top_sectors": sectors.head(15).to_dict(orient="records"),
        "candidates": candidates.to_dict(orient="records"),
        "source_health": source_health.to_dict(orient="records"),
        "review_contract": {
            "required_fields": ["code", "action", "reason", "evidence", "vetoes"],
            "allowed_actions": sorted(ALLOWED_AI_ACTIONS),
        },
    }


def validate_review(payload: dict, candidate_codes: set[str]) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    rows = payload.get("reviews", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return [], ["reviews 必须是数组"]
    valid: list[dict] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"第{index}条不是对象")
            continue
        code = str(row.get("code", "")).strip()
        action = str(row.get("action", "")).strip()
        if code not in candidate_codes:
            errors.append(f"{code or '空代码'} 不是本次候选")
            continue
        if action not in ALLOWED_AI_ACTIONS:
            errors.append(f"{code} 动作不允许：{action}")
            continue
        valid.append(
            {
                "code": code,
                "action": action,
                "reason": str(row.get("reason", "")),
                "evidence": str(row.get("evidence", "")),
                "vetoes": str(row.get("vetoes", "")),
            }
        )
    return valid, errors


def reconcile_reviews(
    decisions: pd.DataFrame,
    main_reviews: list[dict],
    second_reviews: list[dict],
) -> pd.DataFrame:
    result = decisions.copy()
    main_map = {str(row["code"]): row for row in main_reviews}
    second_map = {str(row["code"]): row for row in second_reviews}
    result["main_ai"] = ""
    result["second_ai"] = ""
    result["ai_final"] = result["action"]
    result["ai_reason"] = ""
    for idx, row in result.iterrows():
        code = str(row["code"])
        if code not in main_map and code not in second_map:
            continue
        main = main_map.get(code)
        second = second_map.get(code)
        result.at[idx, "main_ai"] = main["action"] if main else "未复核"
        result.at[idx, "second_ai"] = second["action"] if second else "未复核"
        opinions = [x["action"] for x in (main, second) if x]
        if "否决" in opinions:
            result.at[idx, "ai_final"] = "等待"
        elif len(set(opinions)) > 1 or "降级等待" in opinions:
            result.at[idx, "ai_final"] = "等待"
        elif opinions and all(op == "同意" for op in opinions):
            result.at[idx, "ai_final"] = row["action"]
        else:
            result.at[idx, "ai_final"] = "等待"
        result.at[idx, "ai_reason"] = " | ".join(
            filter(None, [main.get("reason", "") if main else "", second.get("reason", "") if second else ""])
        )
    return result


def parse_review_text(text: str) -> tuple[dict, str]:
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as exc:
        return {}, f"JSON解析失败：{exc}"


def save_packet(packet: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "ai_review_packet.json"
    md_path = output_dir / "ai_review_packet.md"
    json_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# TradePush AI复核包",
        "",
        f"生成时间：{packet['generated_at']}",
        "",
        packet["instruction"],
        "",
        "## 市场状态",
        "",
        "```json",
        json.dumps(packet["market"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 待复核候选",
        "",
    ]
    candidates = pd.DataFrame(packet["candidates"])
    lines.append(candidates.to_markdown(index=False) if not candidates.empty else "当前没有拟买入/加仓候选。")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path

