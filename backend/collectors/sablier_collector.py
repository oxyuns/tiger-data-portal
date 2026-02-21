"""
Sablier Lockup (Vesting) Collector
Fetches vesting stream data from Sablier's Hyperindex GraphQL API
"""

import requests
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

SABLIER_LOCKUP_URL = "https://indexer.hyperindex.xyz/53b7e25/v1/graphql"

CHAIN_ID_MAP = {
    "1": "ethereum",
    "42161": "arbitrum",
    "10": "optimism",
    "8453": "base",
    "137": "polygon",
    "43114": "avalanche",
    "56": "bsc",
}


def fetch_streams_by_sender(sender_address: str, limit: int = 100) -> list:
    """특정 sender(팀/프로젝트)의 베스팅 스트림 가져오기"""
    query = """
    query StreamsBySender($sender: String!, $limit: Int!) {
      Stream(
        where: {sender: {_eq: $sender}}
        limit: $limit
        order_by: {startTime: desc}
      ) {
        id
        chainId
        contract
        asset { symbol address decimals }
        sender
        recipient
        depositAmount
        startTime
        endTime
        cancelable
        canceled
        transferable
      }
    }
    """
    variables = {"sender": sender_address.lower(), "limit": limit}
    return _execute_query(query, variables)


def fetch_streams_by_token(token_address: str, chain_id: Optional[str] = None, limit: int = 200) -> list:
    """특정 토큰의 모든 베스팅 스트림 가져오기 (프로젝트 토큰 기준)"""
    # asset_id 포맷: "asset-{chainId}-{address}"
    if chain_id:
        asset_id = f"asset-{chain_id}-{token_address.lower()}"
        where_clause = {"asset_id": {"_eq": asset_id}}
    else:
        # chainId 모름 → 주소만으로 필터 (모든 체인)
        where_clause = {"asset": {"address": {"_eq": token_address.lower()}}}

    query = """
    query StreamsByToken($where: Stream_bool_exp!, $limit: Int!) {
      Stream(
        where: $where
        limit: $limit
        order_by: {depositAmount: desc}
      ) {
        id
        chainId
        contract
        asset_id
        asset { symbol address decimals }
        sender
        recipient
        depositAmount
        startTime
        endTime
        cancelable
        canceled
        transferable
      }
    }
    """
    variables = {"where": where_clause, "limit": limit}
    return _execute_query(query, variables)


def _execute_query(query: str, variables: dict) -> list:
    try:
        resp = requests.post(
            SABLIER_LOCKUP_URL,
            json={"query": query, "variables": variables},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            logger.error(f"GraphQL errors: {data['errors']}")
            return []
        return data.get("data", {}).get("Stream", [])
    except Exception as e:
        logger.error(f"Sablier query failed: {e}")
        return []


def normalize_stream(stream: dict) -> dict:
    """스트림 데이터를 MongoDB 저장 형식으로 변환"""
    chain = CHAIN_ID_MAP.get(str(stream.get("chainId", "")), f"chain_{stream.get('chainId')}")
    decimals = int(stream.get("asset", {}).get("decimals", 18))
    deposit_raw = int(stream.get("depositAmount", 0))

    return {
        "source": "sablier",
        "stream_id": stream["id"],
        "chain": chain,
        "contract_address": stream.get("contract"),
        "token_symbol": stream.get("asset", {}).get("symbol"),
        "token_address": stream.get("asset", {}).get("address"),
        "sender": stream.get("sender"),
        "recipient": stream.get("recipient"),
        "deposit_amount": deposit_raw / (10 ** decimals),
        "deposit_amount_raw": str(deposit_raw),
        "start_time": datetime.fromtimestamp(int(stream.get("startTime", 0))),
        "end_time": datetime.fromtimestamp(int(stream.get("endTime", 0))),
        "cancelable": stream.get("cancelable", False),
        "canceled": stream.get("canceled", False),
        "transferable": stream.get("transferable", False),
        "fetched_at": datetime.utcnow(),
    }


if __name__ == "__main__":
    # 테스트: Lido 팀 토큰 (LDO) 베스팅 스트림
    print("Testing Sablier collector...")
    streams = fetch_streams_by_token(
        "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32",  # LDO
        chain_id="1",
        limit=5,
    )
    print(f"Found {len(streams)} streams for LDO")
    for s in streams:
        norm = normalize_stream(s)
        print(f"  {norm['token_symbol']} | {norm['deposit_amount']:.2f} | {norm['sender'][:10]}... -> {norm['recipient'][:10]}... | {norm['start_time'].date()} ~ {norm['end_time'].date()}")
