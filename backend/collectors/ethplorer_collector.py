"""
Ethplorer Collector
Free with 'freekey'. Provides token info and top holders.
Key use: Identify large token holders → insider wallet candidates
"""

import requests
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)
ETHPLORER_BASE = "https://api.ethplorer.io"
API_KEY = "freekey"  # free tier


def get_token_info(token_address: str) -> Optional[dict]:
    """토큰 기본 정보 (이름, 공급량, 홀더 수 등)"""
    try:
        r = requests.get(
            f"{ETHPLORER_BASE}/getTokenInfo/{token_address}",
            params={"apiKey": API_KEY},
            timeout=10,
        )
        return r.json() if r.ok else None
    except Exception as e:
        logger.error(f"Ethplorer getTokenInfo failed: {e}")
        return None


def get_top_holders(token_address: str, limit: int = 50) -> list:
    """
    상위 토큰 보유자 목록
    → 대규모 보유자 = 팀/투자자/베스팅 컨트랙트 후보
    """
    try:
        r = requests.get(
            f"{ETHPLORER_BASE}/getTopTokenHolders/{token_address}",
            params={"apiKey": API_KEY, "limit": min(limit, 1000)},
            timeout=15,
        )
        if r.ok:
            return r.json().get("holders", [])
        return []
    except Exception as e:
        logger.error(f"Ethplorer getTopHolders failed: {e}")
        return []


def get_address_transactions(address: str, limit: int = 50) -> list:
    """특정 주소의 토큰 이전 내역"""
    try:
        r = requests.get(
            f"{ETHPLORER_BASE}/getAddressHistory/{address}",
            params={"apiKey": API_KEY, "limit": limit, "type": "transfer"},
            timeout=15,
        )
        if r.ok:
            return r.json().get("operations", [])
        return []
    except Exception as e:
        logger.error(f"Ethplorer getAddressHistory failed: {e}")
        return []


def get_address_info(address: str) -> Optional[dict]:
    """주소 정보 (ETH 잔액 + 토큰 목록)"""
    try:
        r = requests.get(
            f"{ETHPLORER_BASE}/getAddressInfo/{address}",
            params={"apiKey": API_KEY},
            timeout=10,
        )
        return r.json() if r.ok else None
    except Exception as e:
        logger.error(f"Ethplorer getAddressInfo failed: {e}")
        return None


def normalize_holder(holder: dict, token_address: str, token_symbol: str, project_slug: str = "") -> dict:
    """상위 홀더 데이터 정규화"""
    return {
        "source": "ethplorer_top_holders",
        "address": holder.get("address", "").lower(),
        "token_address": token_address.lower(),
        "token_symbol": token_symbol,
        "balance": float(holder.get("balance", 0)),
        "share_percent": float(holder.get("share", 0)),
        "project_slug": project_slug,
        "is_insider_candidate": holder.get("share", 0) >= 0.5,  # 0.5% 이상이면 내부자 후보
        "fetched_at": datetime.utcnow(),
    }


if __name__ == "__main__":
    print("Testing Ethplorer collector...")

    # UNI 토큰 테스트
    token = "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"
    info = get_token_info(token)
    print(f"UNI: {info.get('name')}, holders: {info.get('holdersCount')}")

    print("\nTop UNI holders (insider candidates):")
    holders = get_top_holders(token, limit=10)
    for h in holders:
        print(f"  {h.get('address', '')[:12]}... | {h.get('share', 0):.2f}% | balance: {h.get('balance', 0)/1e18:.0f}")
