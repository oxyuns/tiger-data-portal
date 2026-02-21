"""
LlamaPay Vesting/Stream Collector
LlamaPay는 스트리밍 결제 프로토콜 - 팀 급여/베스팅으로 많이 사용됨
API: https://llamapay.io/api
"""

import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

LLAMAPAY_API = "https://api.llamapay.io"


def fetch_streams_by_token(token_address: str, chain_id: int = 1) -> list:
    """특정 토큰의 LlamaPay 스트림 가져오기"""
    try:
        resp = requests.get(
            f"{LLAMAPAY_API}/stream/token/{token_address}",
            params={"chainId": chain_id},
            timeout=15,
        )
        if resp.ok:
            return resp.json() if isinstance(resp.json(), list) else []
        logger.warning(f"LlamaPay API {resp.status_code}: {resp.text[:200]}")
        return []
    except Exception as e:
        logger.error(f"LlamaPay fetch failed: {e}")
        return []


def fetch_streams_by_address(wallet_address: str) -> list:
    """특정 지갑 관련 스트림"""
    try:
        resp = requests.get(
            f"{LLAMAPAY_API}/stream/{wallet_address}",
            timeout=15,
        )
        return resp.json() if resp.ok else []
    except Exception as e:
        logger.error(f"LlamaPay fetch failed: {e}")
        return []


def normalize_stream(stream: dict, project_slug: str = "") -> dict:
    return {
        "source": "llamapay",
        "stream_id": stream.get("id") or stream.get("streamId"),
        "chain": stream.get("chainId"),
        "contract_address": stream.get("contract"),
        "token_symbol": stream.get("token", {}).get("symbol") if isinstance(stream.get("token"), dict) else None,
        "token_address": stream.get("token", {}).get("address") if isinstance(stream.get("token"), dict) else stream.get("tokenAddress"),
        "sender": stream.get("payer") or stream.get("from"),
        "recipient": stream.get("payee") or stream.get("to"),
        "amount_per_sec": float(stream.get("amountPerSec", 0)),
        "total_streamed": float(stream.get("totalStreamed", 0)),
        "active": stream.get("active", True),
        "project_slug": project_slug,
        "fetched_at": datetime.utcnow(),
    }


if __name__ == "__main__":
    print("Testing LlamaPay collector...")
    # LDO 스트림 테스트
    streams = fetch_streams_by_token("0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32", chain_id=1)
    print(f"LDO streams via LlamaPay: {len(streams)}")
