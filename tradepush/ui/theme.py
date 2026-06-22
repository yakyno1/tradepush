from __future__ import annotations

import streamlit as st

THEME_CSS = """
<style>
:root {
  --tp-bg: #07111f;
  --tp-panel: rgba(13, 27, 46, .84);
  --tp-panel-2: rgba(10, 22, 39, .95);
  --tp-cyan: #20d9ff;
  --tp-blue: #5887ff;
  --tp-purple: #9a6bff;
  --tp-red: #ff5468;
  --tp-green: #2ed99f;
  --tp-amber: #ffbe55;
  --tp-text: #e8f3ff;
  --tp-muted: #86a2bc;
  --tp-border: rgba(88, 135, 255, .25);
}
.stApp {
  background:
    radial-gradient(circle at 15% 0%, rgba(32, 217, 255, .08), transparent 28rem),
    radial-gradient(circle at 85% 10%, rgba(154, 107, 255, .10), transparent 30rem),
    linear-gradient(180deg, #07111f 0%, #050b14 100%);
}
[data-testid="stHeader"] { background: rgba(5, 11, 20, .72); backdrop-filter: blur(12px); }
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #091627 0%, #07111f 100%);
  border-right: 1px solid var(--tp-border);
}
.block-container { max-width: 1500px; padding-top: 1.5rem; padding-bottom: 3rem; }
h1, h2, h3 { color: var(--tp-text); letter-spacing: -.02em; }
.tp-hero {
  position: relative; overflow: hidden;
  padding: 1.4rem 1.5rem; margin-bottom: 1rem;
  border: 1px solid rgba(32, 217, 255, .28); border-radius: 18px;
  background: linear-gradient(135deg, rgba(16, 37, 61, .95), rgba(11, 21, 38, .9));
  box-shadow: 0 18px 60px rgba(0, 0, 0, .28), inset 0 1px rgba(255,255,255,.04);
}
.tp-hero:after {
  content: ""; position: absolute; inset: auto -8% -80% 45%; height: 220px;
  background: radial-gradient(circle, rgba(32,217,255,.20), transparent 65%);
}
.tp-kicker { color: var(--tp-cyan); text-transform: uppercase; letter-spacing: .16em; font-size: .72rem; font-weight: 700; }
.tp-title { color: var(--tp-text); font-size: 2rem; line-height: 1.12; font-weight: 800; margin: .25rem 0; }
.tp-subtitle { color: var(--tp-muted); font-size: .92rem; }
.tp-grid { display: grid; gap: .8rem; grid-template-columns: repeat(4, minmax(0,1fr)); margin: .7rem 0 1rem; }
.tp-card {
  padding: 1rem 1.05rem; min-height: 104px;
  border-radius: 15px; border: 1px solid var(--tp-border);
  background: linear-gradient(160deg, rgba(15,31,52,.96), rgba(8,19,34,.92));
  box-shadow: inset 0 1px rgba(255,255,255,.03), 0 12px 32px rgba(0,0,0,.18);
}
.tp-label { color: var(--tp-muted); font-size: .78rem; }
.tp-value { color: var(--tp-text); font-size: 1.55rem; font-weight: 760; margin-top: .25rem; }
.tp-note { color: var(--tp-muted); font-size: .72rem; margin-top: .25rem; }
.tp-cyan { color: var(--tp-cyan); } .tp-red { color: var(--tp-red); }
.tp-green { color: var(--tp-green); } .tp-amber { color: var(--tp-amber); }
.tp-purple { color: var(--tp-purple); }
.tp-badge {
  display: inline-flex; align-items: center; gap: .35rem; border-radius: 999px;
  padding: .25rem .58rem; font-size: .75rem; font-weight: 700;
  border: 1px solid var(--tp-border); background: rgba(88,135,255,.10);
}
.tp-section {
  color: var(--tp-text); font-weight: 760; font-size: 1.05rem;
  margin: 1.1rem 0 .55rem; padding-left: .7rem; border-left: 3px solid var(--tp-cyan);
}
.tp-trade-card {
  border: 1px solid var(--tp-border); border-radius: 14px; padding: .9rem 1rem;
  margin-bottom: .6rem; background: rgba(11,24,42,.86);
}
.tp-trade-name { font-weight: 750; color: var(--tp-text); }
.tp-trade-meta { color: var(--tp-muted); font-size: .78rem; margin-top: .2rem; }
.tp-warning {
  border: 1px solid rgba(255,190,85,.32); background: rgba(255,190,85,.08);
  color: #ffdca2; padding: .75rem .9rem; border-radius: 12px; margin-bottom: .8rem;
}
.tp-danger {
  border: 1px solid rgba(255,84,104,.32); background: rgba(255,84,104,.08);
  color: #ffb6bf; padding: .75rem .9rem; border-radius: 12px; margin-bottom: .8rem;
}
.tp-conclusion {
  border: 1px solid rgba(32,217,255,.30); border-radius: 15px;
  padding: 1rem 1.1rem; margin: .8rem 0 1rem;
  background: linear-gradient(135deg, rgba(14,34,55,.96), rgba(9,20,35,.92));
}
.tp-conclusion-cyan { box-shadow: inset 3px 0 var(--tp-cyan); }
.tp-conclusion-amber { box-shadow: inset 3px 0 var(--tp-amber); }
.tp-conclusion-red { box-shadow: inset 3px 0 var(--tp-red); }
.tp-conclusion-green { box-shadow: inset 3px 0 var(--tp-green); }
.tp-conclusion-title { color: var(--tp-muted); font-size: .78rem; }
.tp-conclusion-main { color: var(--tp-text); font-size: 1.15rem; font-weight: 760; margin: .28rem 0; }
.tp-conclusion ul { color: var(--tp-muted); margin: .45rem 0 .55rem 1.15rem; padding: 0; font-size: .84rem; }
.tp-conclusion-action { color: var(--tp-cyan); font-size: .84rem; font-weight: 700; }
.tp-process { display: grid; gap: .55rem; margin: .6rem 0 1rem; }
.tp-process-step {
  display: grid; grid-template-columns: 34px 1fr; gap: .7rem; align-items: start;
  padding: .7rem .8rem; border: 1px solid var(--tp-border);
  border-radius: 12px; background: rgba(11,24,42,.72);
}
.tp-process-index {
  width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center;
  color: #06111d; font-weight: 800; font-size: .75rem; background: var(--tp-cyan);
}
.tp-dot-red { background: var(--tp-red); } .tp-dot-green { background: var(--tp-green); }
.tp-dot-amber { background: var(--tp-amber); } .tp-dot-purple { background: var(--tp-purple); }
.tp-process-title { color: var(--tp-text); font-weight: 720; font-size: .86rem; }
.tp-process-status {
  margin-left: .5rem; color: var(--tp-cyan); font-size: .7rem; font-weight: 600;
}
.tp-process-detail { color: var(--tp-muted); font-size: .76rem; margin-top: .18rem; }
div[data-testid="stMetric"] {
  background: var(--tp-panel); border: 1px solid var(--tp-border);
  padding: .85rem 1rem; border-radius: 14px;
}
div[data-testid="stMetricLabel"] { color: var(--tp-muted); }
div[data-testid="stDataFrame"] { border: 1px solid var(--tp-border); border-radius: 12px; overflow: hidden; }
.stButton>button, .stDownloadButton>button {
  border-radius: 10px; border: 1px solid rgba(32,217,255,.34);
  background: linear-gradient(135deg, rgba(32,217,255,.16), rgba(88,135,255,.16));
  color: var(--tp-text); font-weight: 700;
}
.stButton>button:hover, .stDownloadButton>button:hover {
  border-color: var(--tp-cyan); color: white; box-shadow: 0 0 24px rgba(32,217,255,.16);
}
@media(max-width: 900px) {
  .tp-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .tp-title { font-size: 1.55rem; }
}
@media(max-width: 560px) { .tp-grid { grid-template-columns: 1fr; } }
</style>
"""


def apply_theme() -> None:
    st.markdown(THEME_CSS, unsafe_allow_html=True)


def setup_page(title: str, icon: str = "◈") -> None:
    st.set_page_config(
        page_title=f"{title} · TradePush",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_theme()
