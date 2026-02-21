"""
Dune Analytics Collector (Plus plan)
Runs arbitrary SQL on Dune's full blockchain dataset.
"""

import os
import time
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

API_KEY = os.getenv("DUNE_API_KEY", "")
BASE_URL = "https://api.dune.com/api/v1"
HEADERS = {"X-Dune-API-Key": API_KEY, "Content-Type": "application/json"}

POLL_INTERVAL = 5   # seconds between status checks
MAX_POLLS = 60      # max 5 minutes


def run_sql(sql: str, label: str = "", performance: str = "medium") -> list[dict]:
    """Dune Plus: 임의 SQL 실행 후 결과 반환"""
    logger.info(f"Dune SQL: {label or sql[:60]}...")

    # 실행 시작
    r = requests.post(
        f"{BASE_URL}/sql/execute",
        headers=HEADERS,
        json={"sql": sql, "performance": performance},
        timeout=30,
    )
    if not r.ok:
        logger.error(f"Dune execute failed {r.status_code}: {r.text[:200]}")
        return []

    exec_id = r.json()["execution_id"]

    # 완료 대기
    for i in range(MAX_POLLS):
        time.sleep(POLL_INTERVAL)
        s = requests.get(f"{BASE_URL}/execution/{exec_id}/status", headers=HEADERS, timeout=15)
        if not s.ok:
            time.sleep(10)
            continue
        state = s.json().get("state", "")
        if "COMPLETED" in state:
            break
        elif "FAILED" in state:
            logger.error(f"Dune query failed: {s.json().get('error', '')}")
            return []

    # 결과 수집
    res = requests.get(
        f"{BASE_URL}/execution/{exec_id}/results",
        headers=HEADERS,
        params={"limit": 500},
        timeout=30,
    )
    return res.json().get("result", {}).get("rows", [])


# ─── 쿼리 라이브러리 ───────────────────────────────────────────────────────────

def find_vesting_contracts(token_address: str, chain: str = "ethereum",
                           min_recipients: int = 2, min_amount: float = 10000) -> list:
    """
    토큰 전송 패턴으로 베스팅 컨트랙트 후보 탐색
    패턴: 동일 컨트랙트가 여러 수혜자에게 반복적으로 토큰 전송
    """
    table = f"erc20_{chain}.evt_Transfer"
    sql = f"""
SELECT
  "from"                                        AS vesting_contract,
  COUNT(DISTINCT "to")                          AS unique_recipients,
  SUM(TRY_CAST(value AS DOUBLE)) / 1e18         AS total_distributed,
  COUNT(*)                                      AS transfer_count,
  MIN(evt_block_time)                           AS start_date,
  MAX(evt_block_time)                           AS latest_date,
  -- 베스팅 패턴 스코어 (낮은 recipient, 높은 금액 = 더 확실한 베스팅)
  (SUM(TRY_CAST(value AS DOUBLE)) / 1e18)
    / NULLIF(COUNT(DISTINCT "to"), 0)           AS avg_per_recipient
FROM {table}
WHERE contract_address = {token_address}
  AND evt_block_time >= TIMESTAMP '2020-01-01'
GROUP BY 1
HAVING COUNT(DISTINCT "to") >= {min_recipients}
   AND SUM(TRY_CAST(value AS DOUBLE)) / 1e18 > {min_amount}
   AND COUNT(*) < 100000  -- 너무 많은 tx = DEX/스테이킹 (필터)
ORDER BY avg_per_recipient DESC
LIMIT 20
"""
    return run_sql(sql, f"vesting_contracts({token_address[:10]}...)")


def get_vesting_recipients(vesting_contract: str, token_address: str,
                           chain: str = "ethereum") -> list:
    """베스팅 컨트랙트의 수혜자별 수령액"""
    table = f"erc20_{chain}.evt_Transfer"
    sql = f"""
SELECT
  "to"                                          AS recipient,
  SUM(TRY_CAST(value AS DOUBLE)) / 1e18         AS total_received,
  COUNT(*)                                      AS claim_count,
  MIN(evt_block_time)                           AS first_claim,
  MAX(evt_block_time)                           AS last_claim,
  -- 평균 클레임 간격 (일)
  DATE_DIFF('day', MIN(evt_block_time), MAX(evt_block_time))
    / NULLIF(COUNT(*) - 1, 0)                   AS avg_days_between_claims
FROM {table}
WHERE "from" = {vesting_contract}
  AND contract_address = {token_address}
GROUP BY 1
ORDER BY 2 DESC
LIMIT 100
"""
    return run_sql(sql, f"recipients({vesting_contract[:10]}...)")


def detect_cex_inflows(wallet_addresses: list[str], token_address: str,
                       chain: str = "ethereum", days: int = 180) -> list:
    """
    내부자 지갑 → CEX 입금 주소 이체 탐지
    = 매도 의향 신호 (SEC Form 4 equivalent)
    """
    if not wallet_addresses:
        return []

    wallet_list = ", ".join(wallet_addresses)
    table = f"erc20_{chain}.evt_Transfer"
    sql = f"""
WITH cex_deposits AS (
  SELECT address
  FROM labels.all
  WHERE label_type = 'deposit'
    AND blockchain = '{chain}'
),
insider_txs AS (
  SELECT
    t."from"                              AS insider,
    t."to"                               AS cex_deposit,
    SUM(TRY_CAST(t.value AS DOUBLE)) / 1e18  AS tokens_sent,
    COUNT(*)                              AS tx_count,
    MIN(t.evt_block_time)                AS first_deposit,
    MAX(t.evt_block_time)                AS last_deposit
  FROM {table} t
  INNER JOIN cex_deposits c ON t."to" = c.address
  WHERE t."from" IN ({wallet_list})
    AND t.contract_address = {token_address}
    AND t.evt_block_time >= NOW() - INTERVAL '{days}' DAY
  GROUP BY 1, 2
)
SELECT
  *,
  tokens_sent / tx_count AS avg_per_tx
FROM insider_txs
ORDER BY tokens_sent DESC
LIMIT 50
"""
    return run_sql(sql, f"cex_inflows({token_address[:10]}...)")


def get_token_unlock_schedule(vesting_contract: str, token_address: str,
                              chain: str = "ethereum") -> list:
    """월별 토큰 언락(클레임) 스케줄"""
    table = f"erc20_{chain}.evt_Transfer"
    sql = f"""
SELECT
  DATE_TRUNC('month', evt_block_time)           AS unlock_month,
  SUM(TRY_CAST(value AS DOUBLE)) / 1e18         AS tokens_unlocked,
  COUNT(DISTINCT "to")                          AS recipients,
  COUNT(*)                                      AS transactions
FROM {table}
WHERE "from" = {vesting_contract}
  AND contract_address = {token_address}
GROUP BY 1
ORDER BY 1
"""
    return run_sql(sql, f"unlock_schedule({vesting_contract[:10]}...)")


def find_large_insider_wallets(token_address: str, chain: str = "ethereum",
                               min_share_pct: float = 0.5) -> list:
    """
    토큰 대량 보유자 탐색 (현재 잔액 기준)
    Dune labels로 거래소/프로토콜 필터
    """
    sql = f"""
WITH balances AS (
  SELECT
    address,
    SUM(CASE WHEN "to" = address THEN TRY_CAST(value AS DOUBLE) ELSE 0 END)
    - SUM(CASE WHEN "from" = address THEN TRY_CAST(value AS DOUBLE) ELSE 0 END) AS net_balance
  FROM (
    SELECT "to" AS address, value, NULL AS "from" FROM erc20_{chain}.evt_Transfer
    WHERE contract_address = {token_address}
    UNION ALL
    SELECT NULL AS "to", value, "from" FROM erc20_{chain}.evt_Transfer
    WHERE contract_address = {token_address}
  )
  GROUP BY 1
),
total AS (
  SELECT SUM(net_balance) AS total_supply FROM balances WHERE net_balance > 0
),
labeled AS (
  SELECT DISTINCT address, name
  FROM labels.all
  WHERE blockchain = '{chain}'
    AND label_type IN ('cex', 'dex', 'defi', 'bridge')
)
SELECT
  b.address,
  b.net_balance / 1e18                         AS balance,
  b.net_balance / t.total_supply * 100          AS share_pct,
  l.name                                        AS known_entity
FROM balances b
CROSS JOIN total t
LEFT JOIN labeled l ON b.address = l.address
WHERE b.net_balance / t.total_supply * 100 >= {min_share_pct}
  AND b.net_balance > 0
  AND l.name IS NULL  -- 알려진 DEX/CEX 제외
ORDER BY 2 DESC
LIMIT 30
"""
    return run_sql(sql, f"insider_wallets({token_address[:10]}...)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    print("=== Dune Collector Test ===\n")

    # 1. Lido 베스팅 컨트랙트 탐색
    print("1. Lido (LDO) 베스팅 컨트랙트:")
    contracts = find_vesting_contracts(
        "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32",
        min_recipients=3, min_amount=100000
    )
    for c in contracts[:5]:
        print(f"   {c['vesting_contract'][:14]}... | {c['total_distributed']:>12,.0f} LDO | "
              f"recips={c['unique_recipients']} | avg/recip={c.get('avg_per_recipient',0):,.0f}")

    time.sleep(2)

    # 2. Lido 첫 번째 컨트랙트 수혜자 목록
    if contracts:
        top_contract = contracts[0]["vesting_contract"]
        print(f"\n2. {top_contract[:14]}... 수혜자 목록:")
        recipients = get_vesting_recipients(
            top_contract,
            "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32"
        )
        for r in recipients[:5]:
            print(f"   {r['recipient'][:14]}... | {r['total_received']:>12,.0f} LDO | "
                  f"claims={r['claim_count']} | last: {str(r['last_claim'])[:10]}")
