"""기존 app.py가 그대로 import할 수 있는 새 API 호환 계층.

get_alerts(date, date): 기존 화면에 표시할 실제 Alert 목록 반환
get_graph(tx_id): Results + Explanation을 기존 Graph API 응답 형태로 변환

app.py의 CSS, HTML, Streamlit 레이아웃은 수정하지 않는다.
"""
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dt_time, timedelta, timezone
from functools import lru_cache
from threading import Lock
from urllib.parse import quote

import requests

from subgraph import build_subgraph

RESULTS_URL = os.getenv(
    "FRAUDGT_RESULTS_URL", "https://fraudgt-results-api.onrender.com"
).rstrip("/")
EXPLANATION_URL = os.getenv(
    "FRAUDGT_EXPLANATION_URL", "https://fraudgt-explanation-api.onrender.com"
).rstrip("/")

# 동일한 모델을 사용하는 두 API의 버전 이름 매핑
MODEL_VERSION_MAP = {
    "fraudgt-two-head-fbeb06e2-825a38b0d255": "fraudgt-two-head-v1"
}

# 기간 조회에서 이미 받은 거래 정보는 다시 API에 요청하지 않는다.
_known_transactions = {}
_cache_lock = Lock()
MAX_GRAPH_LOOKUPS = 120  # 과도한 외부 API 호출 방지


def _id(transaction_id: str) -> str:
    if not isinstance(transaction_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", transaction_id):
        raise ValueError("올바르지 않은 거래 ID입니다.")
    return quote(transaction_id, safe="")


def _request(method: str, base_url: str, path: str, *, params=None, body=None, allow_404=False):
    try:
        response = requests.request(
            method, base_url + path, params=params, json=body,
            timeout=(10, 75), headers={"Accept": "application/json"},
        )
        if allow_404 and response.status_code == 404:
            return None
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"{method} {path} 호출 실패: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError(f"{path}에서 올바른 JSON을 받지 못했습니다.") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"{path}의 JSON 형식이 올바르지 않습니다.")
    return result


def _utc_midnight(day: date) -> str:
    return datetime.combine(day, dt_time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def get_alerts(start_day: date, end_day: date) -> list[dict]:
    """기존 화면은 '탐지 Alert'로 집계하므로 is_alert=True만 반환.

    새로운 Results API는 alerts 배열 안에 비Alert 유형 후보도 반환하기 때문에
    이 필터가 없으면 기존 KPI와 거래 추이가 잘못 계산된다.
    """
    if start_day > end_day:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")

    params = {
        "start": _utc_midnight(start_day),
        "end": _utc_midnight(end_day + timedelta(days=1)),
        "page_size": 100,
        # min_score 생략: 응답의 is_alert 플래그 기준으로 정확히 구분
    }
    all_items = []
    for page in range(1, 101):
        data = _request("GET", RESULTS_URL, "/api/alerts", params={**params, "page": page})
        batch = data.get("alerts")
        if not isinstance(batch, list):
            raise RuntimeError("Results API의 alerts가 배열이 아닙니다.")
        all_items.extend(batch)
        total = data.get("total")
        if not batch or (isinstance(total, int) and len(all_items) >= total):
            break
    else:
        raise RuntimeError("거래 목록이 10,000건을 초과합니다. 조회 기간을 줄여 주세요.")

    # 저장된 후보 중 비Alert도 주변 거래로 쓰일 수 있으므로 캐시에 보관.
    with _cache_lock:
        for tx in all_items:
            if isinstance(tx, dict) and tx.get("transaction_id"):
                _known_transactions[str(tx["transaction_id"])] = tx

    return [item for item in all_items if isinstance(item, dict) and item.get("is_alert") is True]


@lru_cache(maxsize=4096)
def get_transaction(transaction_id: str) -> dict | None:
    """저장된 유형 후보의 상세 조회. 404면 None, 오류는 예외 발생."""
    with _cache_lock:
        known = _known_transactions.get(transaction_id)
    if known is not None:
        return known
    return _request("GET", RESULTS_URL, f"/api/alerts/{_id(transaction_id)}", allow_404=True)


def get_explanation(transaction_id: str, model_version: str) -> dict:
    """XAI의 not_requested / pending / completed / failed 확인."""
    return _request(
        "GET", EXPLANATION_URL, f"/api/alerts/{_id(transaction_id)}/explanation",
        params={"model_version": model_version},
    )


def request_explanation(transaction_id: str, model_version: str) -> dict:
    """아직 설명 결과가 없는 경우에만 작업 요청."""
    return _request(
        "POST", EXPLANATION_URL, f"/api/alerts/{_id(transaction_id)}/explanation",
        body={"model_version": model_version},
    )


def _wait_for_explanation(transaction_id: str, model_version: str) -> dict:
    """기존 UI에 새 버튼을 추가하지 않기 위해 내부에서 요청/조회 처리."""
    result = get_explanation(transaction_id, model_version)
    if result.get("status") == "not_requested":
        request_explanation(transaction_id, model_version)
        result = get_explanation(transaction_id, model_version)

    for wait_seconds in (1, 2, 3):
        if result.get("status") != "pending":
            break
        time.sleep(wait_seconds)
        result = get_explanation(transaction_id, model_version)

    status = result.get("status")
    if status == "pending":
        raise RuntimeError("XAI 설명 작업 진행 중입니다. 잠시 뒤 다른 거래를 선택했다가 다시 선택해 주세요.")
    if status == "failed":
        raise RuntimeError("XAI 설명 작업이 실패했습니다. API 서버 상태를 확인해 주세요.")
    if status != "completed":
        raise RuntimeError(f"알 수 없는 XAI 상태: {status!r}")
    return result


def get_graph(transaction_id: str) -> dict:
    """이름은 기존 app.py 호환을 위해 유지, 옛 Mock Graph API는 호출하지 않음.

    1) Results API에서 실제 거래 및 모델 버전 확인
    2) Explanation API GET (+ 미요청 시 POST)으로 스냅샷 획득
    3) 각 스냅샷 거래의 상세 정보를 Results API에서 조회
    4) Cytoscape가 쓰던 graph.nodes/edges 형식으로 반환

    완전한 거래 원장이 없어 조회하지 못한 거래는 missing_transaction_ids에 기록.
    """
    target = get_transaction(transaction_id)
    if target is None:
        raise RuntimeError("Results API에서 선택 거래의 상세 정보를 조회할 수 없습니다.")
    if target.get("is_alert") is not True:
        raise RuntimeError("XAI는 is_alert=True인 거래에 대해서만 실행합니다.")
    
    # Results API에서 받은 모델 버전
    results_version = target.get("model_version")

    if not isinstance(results_version, str) or not results_version:
        raise RuntimeError("Results API에서 모델 버전을 받지 못했습니다.")

    # Results 버전 → Explanation 버전 변환
    explanation_version = MODEL_VERSION_MAP.get(results_version)

    if explanation_version is None:
        raise RuntimeError(
            f"Explanation API에 매핑되지 않은 모델 버전입니다: {results_version}"
        )

    # 변환된 버전을 사용해 XAI 설명 요청 및 조회
    explanation = _wait_for_explanation(
        transaction_id,
        explanation_version
    )

    
    snapshot = explanation.get("graph_snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("edge_transaction_ids"), list):
        raise RuntimeError("Explanation의 graph_snapshot.edge_transaction_ids를 찾을 수 없습니다.")

    snapshot_ids = [str(value) for value in snapshot["edge_transaction_ids"]]
    by_id = {transaction_id: target}
    to_fetch = []
    with _cache_lock:
        for tx_id in snapshot_ids:
            if tx_id in by_id:
                continue
            known = _known_transactions.get(tx_id)
            if known is not None:
                by_id[tx_id] = known
            else:
                to_fetch.append(tx_id)

    # 스냅샷이 매우 클 경우 무한정 HTTP 요청하지 않도록 제한한다.
    # 조회하지 않은 거래도 결측으로 기록되므로 가짜 간선이 만들어지지 않는다.
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(get_transaction, tx_id): tx_id for tx_id in to_fetch[:MAX_GRAPH_LOOKUPS]}
        for future in as_completed(futures):
            record = future.result()
            if record is not None:
                by_id[futures[future]] = record

    graph = build_subgraph(explanation, by_id, transaction_id)
    if not graph["graph"]["edges"]:
        raise RuntimeError("관계도를 그릴 거래 상세 정보를 확보하지 못했습니다.")
    return graph
