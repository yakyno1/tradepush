from __future__ import annotations

import pandas as pd
import streamlit as st

from tradepush.services.dashboard import DashboardSnapshot
from tradepush.services.reconstruction import (
    reconstruct_and_archive,
    reconstruct_range,
    reconstruction_date_bounds,
)
from tradepush.ui.components import refresh_snapshot


def render_history_backfill_tools(
    snapshot: DashboardSnapshot,
    *,
    expanded: bool = False,
) -> None:
    """Render the historical reconstruction controls on the overview page."""
    with st.expander("历史数据补录（单日 / 批量）", expanded=expanded):
        min_date_text, max_date_text = reconstruction_date_bounds()
        if not min_date_text:
            st.error("没有可用的本地指数历史，暂时无法补录。")
            return

        min_date = pd.Timestamp(min_date_text).date()
        max_date = pd.Timestamp(max_date_text).date()
        st.write(
            f"本地可回溯区间：`{min_date_text}` 至 `{max_date_text}`。"
            "周末、休市日会自动跳过；缺失数据不会用其他日期冒充。"
        )
        st.caption(
            "补录会生成一个可追溯的新版本，不覆盖旧版本。完成后可从左侧日期和版本选择器查看。"
        )

        single_tab, range_tab = st.tabs(["补单日", "批量补区间"])
        with single_tab:
            default_text = snapshot.data_date if snapshot.data_date != "未知" else max_date_text
            parsed_default = pd.to_datetime(default_text, errors="coerce")
            default_date = max_date if pd.isna(parsed_default) else parsed_default.date()
            default_date = min(max(default_date, min_date), max_date)
            reconstruct_date = st.date_input(
                "补录日期",
                value=default_date,
                min_value=min_date,
                max_value=max_date,
                key="overview_single_reconstruct_date",
            )
            force_single = st.checkbox(
                "已有重建版时仍生成一个新版本",
                value=False,
                key="overview_force_single_reconstruction",
            )
            fetch_sector_single = st.checkbox(
                "联网补录东方财富历史板块涨跌和资金流",
                value=True,
                key="overview_fetch_sector_single",
                help="会比本地补录慢；当日领涨股无法可靠回溯，将保持为空。",
            )
            if st.button(
                "生成单日历史重建版",
                use_container_width=True,
                key="overview_run_single_reconstruction",
            ):
                date_text = reconstruct_date.isoformat()
                with st.spinner(f"正在重建 {date_text} 收盘数据…"):
                    result = reconstruct_and_archive(
                        date_text,
                        force=force_single,
                        fetch_sector_history=fetch_sector_single,
                    )
                if result.status == "CREATED":
                    st.session_state["tp_pending_snapshot_id"] = result.snapshot_id
                    st.session_state["tp_backfill_notice"] = (
                        f"{date_text} 已生成新版本：{result.stocks}只股票、"
                        f"{result.indices}个指数、{result.sectors}个板块。"
                    )
                    refresh_snapshot()
                    st.rerun()
                elif result.status == "EXISTS":
                    st.info(result.message)
                elif result.status == "SKIPPED":
                    st.warning(result.message)
                else:
                    st.error(result.message)

        with range_tab:
            range_value = st.date_input(
                "补录区间",
                value=(
                    max(min_date, (pd.Timestamp(max_date) - pd.Timedelta(days=6)).date()),
                    max_date,
                ),
                min_value=min_date,
                max_value=max_date,
                key="overview_range_reconstruct_dates",
            )
            force_range = st.checkbox(
                "已有日期也生成新版本",
                value=False,
                key="overview_force_range_reconstruction",
            )
            fetch_sector_range = st.checkbox(
                "联网补录区间内板块历史",
                value=True,
                key="overview_fetch_sector_range",
                help="板块接口会一次读取当前板块体系的历史序列，批量日期不会逐日重复抓取。",
            )
            st.caption("界面单次最多处理31个交易日；更长区间请拆分处理。")
            if st.button(
                "批量生成历史重建版",
                use_container_width=True,
                key="overview_run_range_reconstruction",
            ):
                if not isinstance(range_value, (tuple, list)) or len(range_value) != 2:
                    st.warning("请选择起始日期和结束日期。")
                else:
                    start_text = range_value[0].isoformat()
                    end_text = range_value[1].isoformat()
                    with st.spinner(f"正在批量重建 {start_text} 至 {end_text}…"):
                        results = reconstruct_range(
                            start_text,
                            end_text,
                            force=force_range,
                            fetch_sector_history=fetch_sector_range,
                            max_dates=31,
                        )
                    st.dataframe(
                        results.rename(
                            columns={
                                "data_date": "日期",
                                "status": "状态",
                                "stocks": "股票数",
                                "indices": "指数数",
                                "sectors": "板块数",
                                "snapshot_id": "快照编号",
                                "message": "说明",
                            }
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
                    created_count = int(results["status"].eq("CREATED").sum())
                    if created_count:
                        refresh_snapshot()
                        st.success(f"已新建 {created_count} 个历史重建版本，可从左侧选择查看。")

