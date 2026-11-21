"""[1/4] AML 대시보드: 조회 → 관계망 → STR 보고서.

처음 읽을 때는 아래 '1. 설정'부터 '5. 보고서'까지 순서대로 살펴보세요.
실제 API 요청은 api.py, 보고서 생성·PDF 변환은 reporting.py에 분리했습니다.
실행: python -m streamlit run app.py
"""
import json
from datetime import date
from html import escape

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from api import get_alerts, get_graph
from reporting import create_report, report_ready, to_pdf


# ─── 1. 화면 설정 · CSS: 원본 프로토타입의 색상/배치만 재현 ───────────────
st.set_page_config(page_title="FraudGT | AML 거래 모니터링", page_icon="🔎", layout="wide")
st.markdown("""
<style>
[data-testid="stAppViewContainer"] {background: #f5f7fb;}
[data-testid="stHeader"] {background: #f5f7fb;}
[data-testid="stMainBlockContainer"] {max-width:1480px; padding-top:1.4rem;}
.logo {font-size:13px;font-weight:750;color:#25354b;border-bottom:1px solid #e0e6ee;
       padding-bottom:16px;margin-bottom:22px;}
.hero {font-size:30px;font-weight:850;color:#18263b;letter-spacing:-.04em;margin:4px 0 2px;}
.sub {font-size:13px;color:#7d8da2;margin-bottom:20px;}
.kpi {background:white;border:1px solid #e2e8f1;border-radius:13px;
      padding:17px 18px;margin-bottom:9px;min-height:105px;}
.kpi-label {font-size:12px;color:#79899f;font-weight:650;margin-bottom:10px;}
.kpi-value {font-size:28px;color:#1b2a42;font-weight:800;line-height:1.2;}
.kpi.red {border-color:#ffced0;} .kpi.red .kpi-value {color:#e5434b;}
.panel {font-size:17px;font-weight:800;color:#1f2e43;margin:0 0 4px;}
.help {font-size:12px;color:#8795a8;margin-bottom:10px;}
div[data-testid="stVerticalBlockBorderWrapper"] {background:white;border-radius:13px;}
</style>
""", unsafe_allow_html=True)


def title(text: str, caption: str = ""):
    """작은 카드 제목을 표시하는 공통 함수."""
    st.markdown(f'<div class="panel">{escape(text)}</div>'
                f'<div class="help">{escape(caption)}</div>', unsafe_allow_html=True)


def metric(label: str, value, red=False):
    """원본 이미지의 흰색 숫자 카드."""
    cls = " red" if red else ""
    st.markdown(f'<div class="kpi{cls}"><div class="kpi-label">{escape(label)}</div>'
                f'<div class="kpi-value">{escape(str(value))}</div></div>', unsafe_allow_html=True)


# ─── 2. 조회: 날짜 → Alerts API (2분 캐시) ────────────────────────────
@st.cache_data(ttl=120, show_spinner=False)
def load_alerts(start_day: date, end_day: date):
    return get_alerts(start_day, end_day)


@st.cache_data(ttl=120, show_spinner=False)
def load_graph(transaction_id: str):
    return get_graph(transaction_id)


st.markdown('<div class="logo">이상거래 탐지 대시보드 · FRAUDGT</div>', unsafe_allow_html=True)
st.markdown('<div class="hero">이상거래 탐지 현황</div>', unsafe_allow_html=True)

with st.form("date_form"):
    a, b, c = st.columns([1.1, 1.1, 1.3], vertical_alignment="bottom")
    with a:
        start_day = st.date_input("시작일 (UTC)", date(2022, 10, 24))
    with b:
        end_day = st.date_input("종료일 (UTC, 포함)", date(2022, 11, 3))
    with c:
        submitted = st.form_submit_button("🔎 거래 조회", type="primary", use_container_width=True)

if submitted:
    if start_day > end_day:
        st.error("시작일은 종료일보다 늦을 수 없습니다.")
    else:
        try:
            with st.spinner("Mock Alerts API에서 거래를 조회하는 중..."):
                st.session_state["alerts"] = load_alerts(start_day, end_day)
            st.session_state.pop("selected_tx", None)
            st.session_state.pop("report_md", None)
        except (RuntimeError, ValueError) as exc:
            st.session_state.pop("alerts", None)
            st.error(str(exc))

alerts = st.session_state.get("alerts")
if alerts is None:
    st.info("기간을 선택하고 **거래 조회** 버튼을 눌러 주세요.")
    st.stop()
if not alerts:
    st.warning("선택한 기간에 탐지된 거래가 없습니다. 2022년 10월 24일~11월 3일을 조회해 보세요.")
    st.stop()

# 목록 조회 응답에는 거래 금액, 계좌, 위험 점수 등이 포함됩니다.
frame = pd.DataFrame(alerts)
frame["fraud_score"] = pd.to_numeric(frame["fraud_score"], errors="coerce")
frame["날짜"] = pd.to_datetime(frame["timestamp"], utc=True).dt.strftime("%Y-%m-%d")
ids = frame["transaction_id"].astype(str).tolist()
if st.session_state.get("selected_tx") not in ids:
    st.session_state["selected_tx"] = "TX000003" if "TX000003" in ids else ids[0]

# 거래를 한 곳에서만 선택합니다. 선택하면 관련 Graph를 자동 조회합니다.
selected_id = st.session_state["selected_tx"]
selected_alert = next(item for item in alerts if str(item["transaction_id"]) == selected_id)
try:
    with st.spinner("선택 거래의 계좌 관계망을 조회하는 중..."):
        graph_response = load_graph(selected_id)
except (RuntimeError, ValueError) as exc:
    graph_response = None
    st.error(f"Graph API 오류: {exc}")

graph = (graph_response or {}).get("graph", {})
nodes = graph.get("nodes", [])
edges = graph.get("edges", [])


# ─── 3. 원본 프로토타입: 숫자 카드 / 추이 / 거래 선택 ─────────────────
k1, k2, k3, k4 = st.columns(4)
with k1:
    metric("조회 기간 탐지 Alert", f"{len(alerts):,}건")
with k2:
    metric("고위험 Alert (0.90 이상)", f"{sum(float(a['fraud_score']) >= 0.90 for a in alerts):,}건", red=True)
with k3:
    metric("선택 거래 주변 계좌", f"{len(nodes):,}개" if graph_response else "—")
with k4:
    metric("선택 거래 주변 송금", f"{len(edges):,}건" if graph_response else "—")

left, right = st.columns([2.15, 1], gap="large")
with left:
    with st.container(border=True):
        title("탐지 거래 추이", "날짜별 탐지 Alert / 고위험 Alert 건수")
        trend = frame.groupby("날짜").agg(
            **{"탐지 Alert": ("fraud_score", "size"),
               "고위험 Alert": ("fraud_score", lambda values: (values >= .90).sum())}
        )
        st.line_chart(trend, height=295, color=["#3e68ff", "#ef4754"])
with right:
    with st.container(border=True):
        title("거래 선택", "보고서를 만들 거래를 하나 선택하세요.")
        # 선택 창이 유일한 거래 선택 조작입니다. 별도의 상세 보기 버튼은 없습니다.
        st.selectbox("분석 대상 거래", ids, key="selected_tx")
        score = float(selected_alert["fraud_score"])
        st.markdown(f"**현재 선택:** `{escape(selected_id)}`")
        st.write(f"송금: **{selected_alert.get('sender_account', '-')} → {selected_alert.get('receiver_account', '-')}**")
        st.write(f"금액: **{float(selected_alert.get('amount', 0)):,.2f} {selected_alert.get('currency', '')}**")
        st.write(f"Mock 이상 점수: **{score:.2f}**")
        st.caption("다른 거래를 선택하면 자동으로 화면을 갱신합니다.")


# ─── 4. 거래 시각화: Cytoscape.js 관계망 + 실제 주변 거래 표 ──────────
def show_network(nodes: list, edges: list):
    """Graph API의 nodes/edges를 웹브라우저의 Cytoscape.js에 전달합니다."""
    elements = ([{"data": {"id": str(n["id"])}} for n in nodes] + [
        {"data": {"id": str(e["id"]), "source": str(e["source"]),
                  "target": str(e["target"]), "amount": e.get("amount"),
                  "currency": e.get("currency", "")},
         "classes": "target" if e.get("is_target") else ""}
        for e in edges
    ])
    # JSON을 HTML의 <script>에 넣을 때 태그 종료 구문은 반드시 이스케이프합니다.
    data = json.dumps(elements, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = """<!doctype html><html><head><meta charset="utf-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.4/cytoscape.min.js"></script>
<style>body{margin:0;font:12px sans-serif;background:#111827;color:white}
#cy{height:335px}#info{background:#1e293b;padding:10px;min-height:20px}</style>
</head><body><div id="cy"></div><div id="info">계좌 또는 송금선을 클릭해 상세 정보를 확인하세요.</div>
<script>
const items=__DATA__;
if(typeof cytoscape==='undefined'){
  document.getElementById('info').textContent='그래프 라이브러리를 불러오지 못했습니다.';
}else{
const cy=cytoscape({container:document.getElementById('cy'),elements:items,
 layout:{name:'cose',animate:false,padding:32},
 style:[
  {selector:'node',style:{'label':'data(id)','background-color':'#4363af','color':'white',
    'text-valign':'center','text-halign':'center','width':38,'height':38}},
  {selector:'edge',style:{'width':2,'line-color':'#8191a9','target-arrow-color':'#8191a9',
    'target-arrow-shape':'triangle','curve-style':'bezier','opacity':0.85}},
  {selector:'edge.target',style:{'width':5,'line-color':'#ef4444','target-arrow-color':'#ef4444'}}]});
cy.on('tap','edge',ev=>{const d=ev.target.data();
 document.getElementById('info').textContent=d.id+' · '+d.source+' → '+d.target+
 ' · '+Number(d.amount).toLocaleString('ko-KR')+' '+d.currency;});
cy.on('tap','node',ev=>{document.getElementById('info').textContent='계좌: '+ev.target.id();});
}
</script></body></html>""".replace("__DATA__", data)
    components.html(html, height=385, scrolling=False)


st.markdown("### 계좌 거래 관계망 및 송금 내역")
network_col, table_col = st.columns([1, 1.7], gap="large")
with network_col:
    with st.container(border=True):
        title("이상거래 Subgraph", "빨간 선: 선택 거래 / 파란 원: 계좌 / 회색 선: 관련 송금")
        if graph_response:
            show_network(nodes, edges)
        else:
            st.info("계좌 관계망을 불러올 수 없습니다.")
with table_col:
    with st.container(border=True):
        title("선택 거래 주변 송금 내역", "선택 거래 기준 과거 7일 · 2-Hop (기간 필터와 별개의 그래프 분석 범위)")
        if graph_response:
            transactions = pd.DataFrame(edges)
            view_cols = ["id", "source", "target", "amount", "currency", "timestamp"]
            st.dataframe(transactions.reindex(columns=view_cols).rename(columns={
                "id": "거래 ID", "source": "송금", "target": "수취",
                "amount": "금액", "currency": "통화", "timestamp": "시각 (UTC)",
            }), hide_index=True, use_container_width=True, height=383)
        else:
            st.info("관련 거래 내역을 불러올 수 없습니다.")



# ─── 5. STR 보고서 생성 ────────────────────────────

st.markdown("### STR 거래 분석 보고서")

# API 키 설정 여부 확인
if not report_ready():
    st.info(
        "OpenAI API 키가 설정되지 않아 "
        "보고서 생성 기능을 사용할 수 없습니다."
    )

# 보고서 생성 버튼
if st.button(
    "📄 선택 거래 보고서 생성",
    type="primary",
    disabled=not bool(graph_response) or not report_ready()
):
    # 이전 보고서가 남지 않도록 초기화
    st.session_state.pop("report_tx", None)
    st.session_state.pop("report_md", None)

    try:
        with st.spinner("AI 보고서를 생성하고 있습니다..."):
            # C 조원의 LangGraph 실행
            report_md = create_report(
                selected_alert,
                graph_response
            )

        # 생성 결과 저장
        st.session_state["report_tx"] = selected_id
        st.session_state["report_md"] = report_md

        # 이전에 편집한 내용 초기화
        st.session_state.pop(f"edit_{selected_id}", None)

        st.success("STR 보고서 생성 및 자동 검증 완료!")

    except Exception as exc:
        st.error(f"보고서 생성 실패: {exc}")


# 생성된 보고서 표시
if (
    st.session_state.get("report_tx") == selected_id
    and st.session_state.get("report_md")
):
    st.caption("AI 생성 초안입니다. 검토 후 수정할 수 있습니다.")

    # 보고서 편집
    edited = st.text_area(
        "보고서 내용 (Markdown)",
        value=st.session_state["report_md"],
        height=350,
        key=f"edit_{selected_id}"
    )

    # 미리보기
    st.markdown("**보고서 미리보기**")

    with st.container(border=True):
        st.markdown(edited)

    # PDF 저장
    try:
        pdf_data = to_pdf(edited)

        st.download_button(
            "⬇ 보고서 PDF 저장",
            data=pdf_data,
            file_name=f"STR_{selected_id}.pdf",
            mime="application/pdf",
            type="primary"
        )

    except Exception as exc:
        st.error(f"PDF 생성 실패: {exc}")

else:
    st.info("거래를 선택한 뒤 보고서를 생성해 주세요.")