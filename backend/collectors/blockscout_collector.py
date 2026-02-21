"""
Blockscout Collector
Free, no API key required. Multi-chain support.
Provides: contract names, token transfers, address labels, protocol tags
"""

import requests
import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

BLOCKSCOUT_BASES = {
    "ethereum": "https://eth.blockscout.com/api/v2",
    "optimism":  "https://optimism.blockscout.com/api/v2",
    "arbitrum":  "https://arbitrum.blockscout.com/api/v2",
    "base":      "https://base.blockscout.com/api/v2",
    "polygon":   "https://polygon.blockscout.com/api/v2",
}


def _get(chain: str, path: str, params: dict = None) -> Optional[dict]:
    base = BLOCKSCOUT_BASES.get(chain)
    if not base:
        return None
    try:
        r = requests.get(f"{base}{path}", params=params or {}, timeout=15)
        if r.ok:
            return r.json()
        logger.warning(f"Blockscout {chain} {path}: {r.status_code}")
        return None
    except Exception as e:
        logger.error(f"Blockscout error: {e}")
        return None


def get_address_info(chain: str, address: str) -> Optional[dict]:
    """주소 상세 정보 (이름, 태그, 검증 여부)"""
    return _get(chain, f"/addresses/{address.lower()}")


def get_token_transfers(chain: str, address: str, token_address: str = None) -> list:
    """주소의 ERC20 토큰 전송 내역"""
    params = {"type": "ERC-20"}
    if token_address:
        params["token"] = token_address
    data = _get(chain, f"/addresses/{address.lower()}/token-transfers", params)
    if not data:
        return []
    return data.get("items", [])


def get_contract_info(chain: str, address: str) -> Optional[dict]:
    """스마트 컨트랙트 정보 (ABI, 이름, 검증 여부)"""
    return _get(chain, f"/smart-contracts/{address.lower()}")


def find_vesting_contracts_from_token(chain: str, token_address: str, limit: int = 50) -> list:
    """
    토큰의 최근 대규모 전송에서 송신 컨트랙트 탐색
    → 베스팅 컨트랙트 후보 식별
    """
    data = _get(chain, f"/tokens/{token_address.lower()}/transfers")
    if not data:
        return []

    items = data.get("items", [])
    contract_senders = {}

    for tx in items:
        frm = tx.get("from") or {}
        if not isinstance(frm, dict):
            continue
        if frm.get("is_contract") and frm.get("hash"):
            addr = frm["hash"].lower()
            if addr not in contract_senders:
                contract_senders[addr] = {
                    "address": addr,
                    "name": frm.get("name"),
                    "is_verified": frm.get("is_verified", False),
                    "tags": [
                        t.get("name") for t in
                        (frm.get("metadata") or {}).get("tags", [])
                    ],
                    "transfer_count": 0,
                }
            contract_senders[addr]["transfer_count"] += 1

    return sorted(contract_senders.values(), key=lambda x: -x["transfer_count"])


def normalize_transfer(tx: dict, project_slug: str = "") -> dict:
    """토큰 전송 이벤트 정규화"""
    frm = tx.get("from") or {}
    to = tx.get("to") or {}
    token = tx.get("token") or {}
    total = tx.get("total") or {}

    decimals = int(token.get("decimals") or 18)
    raw_value = int(total.get("value") or 0)

    return {
        "source": "blockscout",
        "tx_hash": tx.get("transaction_hash"),
        "block_number": tx.get("block_number"),
        "timestamp": tx.get("timestamp"),
        "from_address": (frm.get("hash") or "").lower(),
        "from_name": frm.get("name"),
        "to_address": (to.get("hash") or "").lower(),
        "to_name": to.get("name"),
        "token_address": (token.get("address_hash") or "").lower(),
        "token_symbol": token.get("symbol"),
        "value": raw_value / (10 ** decimals),
        "value_raw": str(raw_value),
        "method": tx.get("method"),
        "project_slug": project_slug,
        "fetched_at": datetime.utcnow(),
    }


if __name__ == "__main__":
    print("Testing Blockscout collector...")

    # 1. Uniswap TreasuryVester 정보
    info = get_address_info("ethereum", "0xe3953d9d317b834592ab58ab2c7a6ad22b54075d")
    print(f"Uniswap TreasuryVester: {info.get('name')} (verified={info.get('is_verified')})")

    # 2. UNI 토큰에서 베스팅 컨트랙트 탐색
    print("\nSearching vesting contracts from UNI token transfers...")
    contracts = find_vesting_contracts_from_token(
        "ethereum",
        "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"
    )
    print(f"Found {len(contracts)} contract senders:")
    for c in contracts[:5]:
        print(f"  {c['name'] or '(unnamed)'} | {c['address'][:12]}... | verified={c['is_verified']} | txs={c['transfer_count']}")
        if c['tags']:
            print(f"    tags: {c['tags']}")
