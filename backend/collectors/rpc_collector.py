"""
RPC-based Vesting Collector
공개 Ethereum RPC를 이용해 OpenZeppelin-style 베스팅 컨트랙트 데이터 수집
"""

import requests
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 무료 공개 RPC 엔드포인트
RPC_ENDPOINTS = {
    "ethereum": "https://eth.llamarpc.com",
    "arbitrum": "https://arbitrum.llamarpc.com",
    "optimism":  "https://optimism.llamarpc.com",
    "base":      "https://base.llamarpc.com",
    "polygon":   "https://polygon.llamarpc.com",
}

# 알려진 프로젝트 베스팅 컨트랙트 (공개 정보 기반)
KNOWN_VESTING_CONTRACTS = {
    "uniswap": [
        {"address": "0xe3953d9d317b834592ab58ab2c7a6ad22b54075d", "chain": "ethereum", "label": "Team Vesting"},
        {"address": "0xB460DAa847c45f1C4a41cb05BFB3b51c92e41B36", "chain": "ethereum", "label": "Investor Vesting"},
    ],
    "optimism": [
        {"address": "0x2501c477D0A35545a387Aa4A3EEe4292A9a8B3F0", "chain": "optimism", "label": "Core Contributors"},
    ],
    "lido": [
        {"address": "0x834A54A8C8A6A9E9bF418FA7Fb0C0b3fdD26Ff14", "chain": "ethereum", "label": "Team Vesting"},
    ],
}

# OZ TokenVesting ABI (핵심 함수만)
OZ_VESTING_ABI_FUNCS = {
    "beneficiary": "0x01bfd82e",  # beneficiary()
    "start":       "0xbe9a6555",  # start()
    "duration":    "0x0fb5a6b4",  # duration()
    "released":    "0x1a7a98e2",  # released(address)
}


def eth_call(chain: str, to: str, data: str) -> Optional[str]:
    """단순 eth_call (view 함수 호출)"""
    endpoint = RPC_ENDPOINTS.get(chain)
    if not endpoint:
        return None
    try:
        resp = requests.post(
            endpoint,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                  "params": [{"to": to, "data": data}, "latest"]},
            timeout=10,
        )
        result = resp.json().get("result")
        return result
    except Exception as e:
        logger.error(f"RPC call failed ({chain}, {to}): {e}")
        return None


def get_token_balance(chain: str, token_address: str, wallet_address: str) -> Optional[float]:
    """ERC20 balanceOf 조회"""
    # balanceOf(address) selector: 0x70a08231
    padded = wallet_address.lower().replace("0x", "").zfill(64)
    data = "0x70a08231" + padded
    result = eth_call(chain, token_address, data)
    if result and result != "0x":
        try:
            return int(result, 16) / 1e18
        except Exception:
            return None
    return None


def get_latest_block(chain: str) -> Optional[int]:
    """최신 블록 번호"""
    endpoint = RPC_ENDPOINTS.get(chain)
    if not endpoint:
        return None
    try:
        resp = requests.post(
            endpoint,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
            timeout=10,
        )
        return int(resp.json().get("result", "0x0"), 16)
    except Exception:
        return None


def get_erc20_transfers(chain: str, token_address: str, from_address: str, from_block: int = 0) -> list:
    """
    eth_getLogs로 ERC20 Transfer 이벤트 가져오기
    Transfer(address indexed from, address indexed to, uint256 value)
    topic0: 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
    """
    endpoint = RPC_ENDPOINTS.get(chain)
    if not endpoint:
        return []

    TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    padded_from = "0x" + from_address.lower().replace("0x", "").zfill(64)

    try:
        resp = requests.post(
            endpoint,
            json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
                "params": [{
                    "fromBlock": hex(from_block),
                    "toBlock": "latest",
                    "address": token_address,
                    "topics": [TRANSFER_SIG, padded_from],
                }]
            },
            timeout=20,
        )
        logs = resp.json().get("result", [])
        return logs if isinstance(logs, list) else []
    except Exception as e:
        logger.error(f"eth_getLogs failed: {e}")
        return []


def parse_transfer_log(log: dict, decimals: int = 18) -> dict:
    """Transfer 로그 파싱"""
    topics = log.get("topics", [])
    from_addr = "0x" + topics[1][26:] if len(topics) > 1 else None
    to_addr   = "0x" + topics[2][26:] if len(topics) > 2 else None
    value_raw = int(log.get("data", "0x0"), 16)

    return {
        "tx_hash": log.get("transactionHash"),
        "block_number": int(log.get("blockNumber", "0x0"), 16),
        "from_address": from_addr,
        "to_address": to_addr,
        "value": value_raw / (10 ** decimals),
        "value_raw": str(value_raw),
    }


if __name__ == "__main__":
    # 테스트: 이더리움 최신 블록
    block = get_latest_block("ethereum")
    print(f"Latest ETH block: {block:,}")

    # 테스트: ARB 토큰 알려진 컨트랙트 잔액
    arb_token = "0xB50721BCf8d664c30412Cfbc6cf7a15145234ad1"
    # Arbitrum 재단 주소 (공개됨)
    foundation_addr = "0x0C4B4C5de4bef8A88B8d68bC9C9F34f14Be7f6b5"
    balance = get_token_balance("ethereum", arb_token, foundation_addr)
    print(f"ARB Foundation balance: {balance}")
