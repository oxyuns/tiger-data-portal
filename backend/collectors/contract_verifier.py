"""
Contract Verifier
베스팅 후보 컨트랙트를 패턴 분석 + Blockscout 이름 조회로 분류/검증
"""

import requests
import logging
import time
from datetime import datetime
from typing import Optional

from collectors.dune_collector import run_sql

logger = logging.getLogger(__name__)

BLOCKSCOUT_BASES = {
    "ethereum": "https://eth.blockscout.com/api/v2",
    "optimism":  "https://optimism.blockscout.com/api/v2",
    "arbitrum":  "https://arbitrum.blockscout.com/api/v2",
    "base":      "https://base.blockscout.com/api/v2",
    "polygon":   "https://polygon.blockscout.com/api/v2",
}

NON_VESTING_TAGS = [
    "uniswap", "1inch", "coinbase", "binance", "kyber", "curve",
    "balancer", "dodo", "paraswap", "mev bot", "kraken", "okx",
    "bybit", "huobi", "kucoin", "gate.io", "sushiswap",
]

VESTING_KEYWORDS = [
    "vest", "treasury", "vester", "lock", "cliff",
    "grant", "allocation", "tokenlock", "tokenvest",
]

STREAMING_CONTRACTS = [
    "llamapay", "sablier", "superfluid", "drip",
    "streamr", "ricochet",
]


def classify_contract(
    ca: str,
    recips: int,
    txs: int,
    avg_gap: Optional[float],
    name: str,
    tags: list[str],
) -> tuple[str, int]:
    """
    컨트랙트 분류 및 베스팅 신뢰도 스코어 반환
    스코어: 10=확실, 9=가능성 높음, 5=가능, 3=불명, 2=스트리밍, 1=에어드랍, 0=비-베스팅
    """
    name_l = (name or "").lower()
    tags_l = " ".join(t.lower() for t in tags)

    # 알려진 DEX/CEX/MEV → 즉시 제외
    if any(t in tags_l for t in NON_VESTING_TAGS):
        return "EXCHANGE_DEX", 0

    # 스트리밍 프로토콜 이름
    if any(k in name_l for k in STREAMING_CONTRACTS):
        return "STREAMING_PROTOCOL", 1

    # 이름에 베스팅 키워드
    if any(k in name_l for k in VESTING_KEYWORDS):
        return "VESTING_CONFIRMED", 10

    # GnosisSafe → 팀 멀티시그 (별도 추적 가치 있음)
    if "gnosis" in name_l or ("proxy" in name_l and "safe" in name_l):
        return "TEAM_MULTISIG", 6

    # 스트리밍 패턴: 매우 짧은 간격 + 많은 tx
    if avg_gap is not None and avg_gap < 2 and txs > 100:
        return "STREAMING", 2

    # 에어드랍: 수혜자 많고 tx 적음
    if recips > 200:
        return "AIRDROP_DIST", 1

    # 핵심 베스팅 패턴
    if recips <= 30 and (avg_gap is None or 3 <= avg_gap <= 180):
        return "VESTING_LIKELY", 9

    if recips <= 100 and txs < 500:
        return "VESTING_POSSIBLE", 5

    return "UNKNOWN", 3


def get_blockscout_info(chain: str, address: str) -> dict:
    """Blockscout에서 컨트랙트 이름/태그 가져오기"""
    base = BLOCKSCOUT_BASES.get(chain)
    if not base:
        return {}
    try:
        r = requests.get(f"{base}/addresses/{address.lower()}", timeout=8)
        if not r.ok:
            return {}
        d = r.json()
        tags = [t.get("name", "") for t in (d.get("metadata") or {}).get("tags", [])]
        return {
            "name": d.get("name") or "",
            "is_verified": d.get("is_verified", False),
            "is_contract": d.get("is_contract", True),
            "tags": tags,
        }
    except Exception as e:
        logger.debug(f"Blockscout {chain}/{address}: {e}")
        return {}


def find_and_verify_vesting_contracts(
    project_slug: str,
    token_address: str,
    chain: str = "ethereum",
    min_amount: float = 5000,
) -> list[dict]:
    """
    1) Dune으로 후보 추출
    2) Blockscout로 이름/태그 조회
    3) 분류기로 베스팅 여부 판정
    4) 검증된 컨트랙트 리스트 반환
    """
    logger.info(f"[{project_slug}] Scanning {chain} for vesting contracts...")

    rows = run_sql(f"""
WITH raw AS (
  SELECT "from" AS ca, "to" AS recip, value, evt_block_time
  FROM erc20_{chain}.evt_Transfer
  WHERE contract_address = {token_address}
    AND evt_block_time >= TIMESTAMP '2020-01-01'
),
ivs AS (
  SELECT ca,
    DATE_DIFF('day',
      LAG(evt_block_time) OVER (PARTITION BY ca ORDER BY evt_block_time),
      evt_block_time) AS gap
  FROM raw
),
gap_stats AS (
  SELECT ca,
    ROUND(AVG(gap), 1)    AS avg_gap,
    ROUND(STDDEV(gap), 1) AS std_gap
  FROM ivs WHERE gap IS NOT NULL GROUP BY 1
),
agg AS (
  SELECT ca,
    COUNT(DISTINCT recip)                                  AS recips,
    ROUND(SUM(TRY_CAST(value AS DOUBLE)) / 1e18, 0)       AS total,
    COUNT(*)                                               AS txs,
    CAST(MIN(evt_block_time) AS DATE)                      AS start_dt,
    CAST(MAX(evt_block_time) AS DATE)                      AS last_dt
  FROM raw
  GROUP BY 1
  HAVING COUNT(DISTINCT recip) BETWEEN 2 AND 300
    AND SUM(TRY_CAST(value AS DOUBLE)) / 1e18 > {min_amount}
    AND COUNT(*) < 100000
)
SELECT a.ca, a.recips, a.total, a.txs, a.start_dt, a.last_dt,
       g.avg_gap, g.std_gap
FROM agg a
LEFT JOIN gap_stats g ON a.ca = g.ca
ORDER BY a.total DESC
LIMIT 30
""", f"{project_slug} scan", performance="large")

    if not rows:
        logger.info(f"[{project_slug}] No candidates found")
        return []

    # uint256 오버플로우 필터 (max uint256 / 1e18 ≈ 1.16e59)
    rows = [r for r in rows if r.get("total", 0) < 1e18]

    verified = []
    for row in rows:
        ca = row["ca"]
        bs = get_blockscout_info(chain, ca)
        time.sleep(0.2)

        cls, score = classify_contract(
            ca=ca,
            recips=row["recips"],
            txs=row["txs"],
            avg_gap=row.get("avg_gap"),
            name=bs.get("name", ""),
            tags=bs.get("tags", []),
        )

        doc = {
            "address": ca,
            "project_slug": project_slug,
            "chain": chain,
            "token_address": token_address,
            "classification": cls,
            "vesting_score": score,
            # 온체인 통계
            "unique_recipients": row["recips"],
            "total_distributed": row["total"],
            "transfer_count": row["txs"],
            "start_date": str(row.get("start_dt", "")),
            "latest_date": str(row.get("last_dt", "")),
            "avg_days_between_tx": row.get("avg_gap"),
            "interval_stddev": row.get("std_gap"),
            # Blockscout 메타
            "contract_name": bs.get("name", ""),
            "is_verified": bs.get("is_verified", False),
            "tags": bs.get("tags", []),
            # 메타
            "is_vesting": score >= 9,
            "fetched_at": datetime.utcnow(),
        }
        verified.append(doc)

    # 스코어 내림차순 정렬
    verified.sort(key=lambda x: (-x["vesting_score"], -x["total_distributed"]))

    vesting_count = sum(1 for v in verified if v["is_vesting"])
    logger.info(
        f"[{project_slug}] {len(verified)} candidates → "
        f"{vesting_count} vesting confirmed"
    )
    return verified
