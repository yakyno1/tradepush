from __future__ import annotations

import json

import streamlit as st

from tradepush.ai.review import (
    build_ai_packet,
    parse_review_text,
    reconcile_reviews,
    save_packet,
    validate_review,
)
from tradepush.storage.repository import output_folder
from tradepush.ui.components import cached_snapshot, freshness_notice, hero, section
from tradepush.ui.theme import setup_page

setup_page("AI复核", "✦")
snapshot = cached_snapshot()
hero("AI 双重复核", "AI只允许同意、否决或降级等待；价格、止损、仓位和硬门槛由机器规则锁定。")
freshness_notice(snapshot.data_date)

packet = build_ai_packet(
    snapshot.market.to_dict(),
    snapshot.sectors,
    snapshot.decisions,
    snapshot.source_health,
    snapshot.stock_forecasts,
    snapshot.sector_horizon_forecasts,
)
folder = output_folder(snapshot.data_date)

c1, c2 = st.columns([1, 1])
with c1:
    if st.button("生成 Codex / Hermes 分析包", type="primary", use_container_width=True):
        json_path, md_path = save_packet(packet, folder)
        st.success(f"已生成：{json_path.name}、{md_path.name}")
with c2:
    st.download_button(
        "下载 AI JSON 包",
        data=json.dumps(packet, ensure_ascii=False, indent=2),
        file_name="ai_review_packet.json",
        mime="application/json",
        use_container_width=True,
    )

section("机器候选")
candidates = snapshot.decisions[snapshot.decisions["action"].isin(["条件买", "加仓"])]
if candidates.empty:
    st.info("当前没有需要双审的拟买入/加仓候选。")
else:
    st.dataframe(
        candidates[["code", "name", "action", "sector_state", "path", "trigger_price", "stop_price", "target_price", "suggested_weight_pct", "hard_vetoes"]],
        use_container_width=True,
        hide_index=True,
    )

example = json.dumps(
    {
        "reviews": [
            {
                "code": str(candidates.iloc[0]["code"]) if not candidates.empty else "300308",
                "action": "同意",
                "reason": "板块和个股条件一致",
                "evidence": "引用本次分析包中的具体字段",
                "vetoes": "",
            }
        ]
    },
    ensure_ascii=False,
    indent=2,
)
tab1, tab2 = st.tabs(["主AI返回", "第二AI复核"])
with tab1:
    main_text = st.text_area("粘贴主AI JSON", value=example, height=260, key="main_ai_review")
with tab2:
    second_text = st.text_area("粘贴第二AI JSON", value=example, height=260, key="second_ai_review")

if st.button("校验并合并双审结果", use_container_width=True):
    candidate_codes = set(candidates["code"].astype(str))
    main_payload, main_parse_error = parse_review_text(main_text)
    second_payload, second_parse_error = parse_review_text(second_text)
    main, main_errors = validate_review(main_payload, candidate_codes)
    second, second_errors = validate_review(second_payload, candidate_codes)
    errors = [x for x in [main_parse_error, second_parse_error] if x] + main_errors + second_errors
    if errors:
        for error in errors:
            st.error(error)
    else:
        merged = reconcile_reviews(snapshot.decisions, main, second)
        st.session_state["ai_merged"] = merged
        st.success("双审合并完成；分歧和否决已自动降级为等待。")

if "ai_merged" in st.session_state:
    section("最终复核结果")
    merged = st.session_state["ai_merged"]
    st.dataframe(
        merged[["code", "name", "action", "main_ai", "second_ai", "ai_final", "ai_reason"]],
        use_container_width=True,
        hide_index=True,
    )
