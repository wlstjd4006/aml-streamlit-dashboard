"""[2/4] Mock API 연결만 담당하는 파일.

Alerts: 기간별 '탐지된 거래' 목록 (전체 은행 원장 아님)
Graph : 선택한 거래 주변 계좌/송금 관계
"""
import os
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote

import requests

ALERTS_URL = os.getenv("FRAUDGT_ALERTS_URL", "https://fraudgt-mock-alerts.onrender.com").rstrip("/")
GRAPH_URL = os.getenv("FRAUDGT_GRAPH_URL", "https://fraudgt-mock-graph.onrender.com").rstrip("/")


def get_json(base_url, path, params=None):
    """공통 GET 요청: Render 콜드스타트를 고려해 충분한 대기 시간 부여."""
    try:
        response = requests.get(
            base_url + path, params=params, timeout=(10, 90),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"Mock API 요청 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Mock API가 JSON 객체가 아닌 값을 반환했습니다.")
    return data


def utc_midnight(day: date) -> str:
    """날짜 -> API가 요구하는 UTC 시각 문자열."""
    return datetime.combine(day, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def get_alerts(start_day: date, end_day: date) -> list[dict]:
    """입력한 날짜(종료일 포함)의 탐지 Alert를 페이지 끝까지 가져옴.

    API end는 미포함이므로 사용자가 11/03까지 선택하면 11/04 00:00으로 요청.
    서버의 기본 탐지 임계값(0.8)은 변경할 수 없습니다.
    """
    if start_day > end_day:
        raise ValueError("시작일이 종료일보다 늦습니다.")

    params = {
        "start": utc_midnight(start_day),
        "end": utc_midnight(end_day + timedelta(days=1)),
        "page_size": 100,
        "min_score": 0.0,  # 서버 임계값 자체를 낮추는 옵션은 아님
    }
    alerts = []
    page = 1
    while True:
        response = get_json(ALERTS_URL, "/api/alerts", {**params, "page": page})
        batch = response.get("alerts", [])
        if not isinstance(batch, list):
            raise RuntimeError("Alerts 응답의 alerts 필드가 배열이 아닙니다.")
        alerts.extend(batch)
        if len(alerts) >= int(response.get("total", len(alerts))) or not batch:
            break
        page += 1
        if page > 20:  # 실습용 안전 장치(페이지당 100건, 최대 2,000건)
            raise RuntimeError("조회 결과가 2,000건을 넘었습니다. 조회 기간을 줄여 주세요.")
    return alerts


def get_graph(transaction_id: str) -> dict:
    """선택 거래 기준 2-hop / 과거 7일의 그래프 반환."""
    if not transaction_id or not all(c.isalnum() or c in "_-" for c in transaction_id):
        raise ValueError("거래 ID 형식이 올바르지 않습니다.")
    safe_id = quote(transaction_id, safe="")
    result = get_json(
        GRAPH_URL, f"/api/alerts/{safe_id}/graph",
        {"hops": 2, "lookback_days": 7},
    )
    graph = result.get("graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise RuntimeError("Graph API 응답의 nodes 또는 edges 형식이 잘못되었습니다.")
    return result
