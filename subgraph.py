"""Explanation 스냅샷 + Results 거래 데이터를 기존 그래프 모양으로 변환.

중요: 스냅샷의 ID만으로 송금 방향은 알 수 없다. 상세 조회에 실패한
거래의 연결을 추측해서 만들지 않는다.
"""
from datetime import datetime, timezone


def build_subgraph(explanation: dict, transactions_by_id: dict, target_id: str) -> dict:
    if explanation.get("status") != "completed":
        raise ValueError("XAI 설명이 완료되지 않았습니다.")

    snapshot = explanation.get("graph_snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("graph_snapshot이 없는 XAI 응답입니다.")

    tx_ids = snapshot.get("edge_transaction_ids")
    account_ids = snapshot.get("node_account_ids")
    if not isinstance(tx_ids, list) or not isinstance(account_ids, list):
        raise ValueError("XAI 스냅샷의 거래/계좌 ID 목록 형식이 올바르지 않습니다.")

    importance = {
        str(item["transaction_id"]): item["importance"]
        for item in explanation.get("edge_importance", [])
        if isinstance(item, dict)
        and "transaction_id" in item and "importance" in item
    }

    edges, missing = [], []
    for raw_id in tx_ids:
        tx_id = str(raw_id)
        tx = transactions_by_id.get(tx_id)
        required = ("sender_account", "receiver_account", "amount", "currency", "timestamp")
        if not isinstance(tx, dict) or any(tx.get(field) is None for field in required):
            missing.append(tx_id)
            continue

        edges.append({
            "id": tx_id,
            "source": str(tx["sender_account"]),
            "target": str(tx["receiver_account"]),
            "amount": tx["amount"],
            "currency": tx["currency"],
            "timestamp": tx["timestamp"],
            "is_target": tx_id == target_id,
            "importance": importance.get(tx_id),
        })

    # 실제로 거래 방향이 확인된 노드만 표시한다. 일부 정보가 없는 계좌를
    # 임의로 고립 노드로 보여 주면 완전한 그래프로 오인할 수 있기 때문이다.
    visible_ids = {str(e["source"]) for e in edges} | {str(e["target"]) for e in edges}

    timestamps = [datetime.fromisoformat(str(e["timestamp"]).replace("Z", "+00:00")) for e in edges]
    start = min(timestamps).astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if timestamps else None
    end = max(timestamps).astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if timestamps else None

    return {
        "graph": {
            "nodes": [{"id": account} for account in sorted(visible_ids)],
            "edges": edges,
            # 이 구간은 확보한 거래의 최소/최대 시각. 원래 Graph API의 탐색 범위와 다를 수 있다.
            "window_start": start,
            "window_end": end,
        },
        "data_source": "results_explanation",
        "graph_snapshot": snapshot,
        "snapshot_edge_count": len(tx_ids),
        "snapshot_node_count": len(account_ids),
        "missing_transaction_ids": missing,
    }
