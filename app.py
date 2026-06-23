from __future__ import annotations

import streamlit as st

from tradepush.ui.components import snapshot_history_selector

pages = [
    st.Page("pages/0_今日总览.py", title="今日总览", icon=":material/dashboard:"),
    st.Page("pages/1_板块轮动.py", title="板块轮动", icon=":material/grid_view:"),
    st.Page("pages/2_个股决策.py", title="个股决策", icon=":material/candlestick_chart:"),
    st.Page("pages/3_持仓与风控.py", title="持仓与风控", icon=":material/shield:"),
    st.Page("pages/4_AI复核.py", title="AI复核", icon=":material/psychology:"),
    st.Page("pages/5_复盘诊断.py", title="复盘诊断", icon=":material/analytics:"),
    st.Page("pages/6_数据中心与设置.py", title="数据中心与设置", icon=":material/settings:"),
    st.Page("pages/7_系统说明.py", title="系统说明", icon=":material/info:"),
]

with st.sidebar:
    snapshot_history_selector()
    st.divider()

navigation = st.navigation(pages, position="sidebar")
_ = navigation.run()
