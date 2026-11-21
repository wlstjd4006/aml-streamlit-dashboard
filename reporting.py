"""[3/4] Graph API -> STR 입력 변환 -> 보고서 작성 -> PDF 출력.

OpenAI 키가 있으면 C 담당자의 LangGraph 모듈을 사용합니다.
키가 없으면 계산 가능한 거래 사실만 담은 '비LLM 데모 초안'을 생성합니다.
"""
import os
from io import BytesIO
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def make_payload(alert: dict, graph_response: dict) -> dict:
    """팀 C의 ReportInput에 맞는 JSON을 실제 Graph 응답으로 생성."""
    graph = graph_response["graph"]
    target_id = str(alert["transaction_id"])
    transactions = []
    for edge in graph["edges"]:
        transactions.append({
            "tx_id": str(edge["id"]),
            "sender": str(edge["source"]),
            "receiver": str(edge["target"]),
            "timestamp": str(edge["timestamp"]),
            "amount_paid": str(edge["amount"]),
            "payment_currency": str(edge["currency"]),
            "amount_received": str(edge["amount"]),
            "receiving_currency": str(edge["currency"]),
        })
    if not any(t["tx_id"] == target_id for t in transactions):
        raise ValueError("대상 거래가 선택 그래프에 없습니다.")
    return {
        "case_id": f"fraudgt_demo_{target_id}_h2_d7",
        "data_source": "synthetic_demo",
        "target_tx_id": target_id,
        "analysis_start": graph["window_start"],
        "analysis_end": graph["window_end"],
        "transactions": transactions,
        "anomaly_score": float(alert["fraud_score"]),
        "score_source": "mock",
        "xai": None,
    }


def report_ready() -> bool:
    """C 조원의 LangGraph 실행을 위한 API 키 설정 확인."""
    return bool(
        os.getenv("OPENAI_API_KEY", "").strip()
        or Path(__file__).with_name("str_api_config.py").is_file()
    )


def create_report(alert: dict, graph_response: dict) -> str:
    """Mock API 거래 데이터로 C 조원의 보고서 생성 함수를 실행."""

    if not report_ready():
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다."
        )

    # 1. Mock API 데이터를 보고서 입력 형식으로 변환
    payload = make_payload(alert, graph_response)

    # 2. C 조원의 LangGraph 함수 불러오기
    from str_report import generate_str_report

    # 3. 실제 AI 보고서 생성
    result = generate_str_report(payload)

    # 4. 보고서 검증 결과 확인
    if (
        result.get("status") != "validated_draft"
        or result.get("verification_passed") is not True
    ):
        errors = result.get("errors", [])
        raise RuntimeError(
            f"보고서 검증 실패: {errors}"
        )

    # 5. 생성된 Markdown 보고서 반환
    report_md = result.get("final_report_md")

    if not isinstance(report_md, str) or not report_md.strip():
        raise RuntimeError("보고서 내용이 비어 있습니다.")

    return report_md


def to_pdf(markdown_text: str) -> bytes:
    """편집된 Markdown을 한글을 지원하는 A4 PDF로 내보냅니다."""
    pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))
    font = "HYGothic-Medium"
    basic = ParagraphStyle("body", fontName=font, fontSize=9, leading=15, wordWrap="CJK", alignment=TA_LEFT, spaceAfter=5)
    heading = ParagraphStyle("heading", parent=basic, fontSize=13, leading=20, spaceBefore=13, spaceAfter=8)
    title = ParagraphStyle("title", parent=heading, fontSize=18, leading=25)
    small = ParagraphStyle("small", parent=basic, fontSize=7, leading=11)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=18*mm, bottomMargin=18*mm,
                            title="STR 거래 분석 보고서")
    blocks = []
    table_rows = []

    def write_table():
        if not table_rows:
            return
        # Markdown의 | --- | 구분선은 출력에서 제거합니다.
        rows = [r for r in table_rows if not all(set(cell) <= set(":- ") for cell in r)]
        if rows:
            width = (A4[0] - 36*mm) / max(len(r) for r in rows)
            cells = [[Paragraph(escape(cell).replace("\\|", "|"), small) for cell in row] for row in rows]
            tbl = Table(cells, colWidths=[width]*max(len(r) for r in rows), repeatRows=1, hAlign="LEFT")
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0,0),(-1,0), colors.HexColor("#EDF2F8")),
                ("GRID", (0,0),(-1,-1), 0.35, colors.HexColor("#D5DFEB")),
                ("VALIGN",(0,0),(-1,-1),"TOP"),
                ("LEFTPADDING",(0,0),(-1,-1),5),
                ("RIGHTPADDING",(0,0),(-1,-1),5),
                ("TOPPADDING",(0,0),(-1,-1),5),
                ("BOTTOMPADDING",(0,0),(-1,-1),5),
            ]))
            blocks.append(tbl)
            blocks.append(Spacer(1, 6))
        table_rows.clear()

    for raw in markdown_text.splitlines():
        line = raw.strip()
        if line.startswith("|") and line.endswith("|"):
            table_rows.append([cell.strip() for cell in line.strip("|").split("|")])
            continue
        write_table()
        if not line:
            blocks.append(Spacer(1, 5))
            continue
        if line.startswith("# "):
            style, line = title, line[2:]
        elif line.startswith("## "):
            style, line = heading, line[3:]
        else:
            style = basic
            line = line.lstrip("- ").replace("**", "").replace("\\", "")
        blocks.append(Paragraph(escape(line), style))
    write_table()
    doc.build(blocks or [Paragraph("내용 없음", basic)])
    return buf.getvalue()
