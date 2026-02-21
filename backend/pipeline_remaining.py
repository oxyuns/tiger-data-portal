"""
미처리 프로젝트들 재실행 (medium 클러스터, Lido/RPL은 쿼리 최적화)
"""
import json, logging, time
from pathlib import Path
from datetime import datetime
from models.db import get_db
from collectors.contract_verifier import find_and_verify_vesting_contracts
from collectors.dune_collector import run_sql
from collectors.blockscout_collector import get_address_info as bs_info

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SEEDS_PATH = Path(__file__).parent.parent / "data" / "seeds" / "projects.json"
DUNE_CHAINS = {
    "ethereum": "ethereum", "arbitrum": "arbitrum",
    "optimism": "optimism", "base": "base", "polygon": "polygon",
}
ICON = {"VESTING_CONFIRMED":"🟢✓","VESTING_LIKELY":"🟢","VESTING_POSSIBLE":"🟡",
        "TEAM_MULTISIG":"🔵","STREAMING":"🔴","STREAMING_PROTOCOL":"🔴",
        "EXCHANGE_DEX":"🔴","AIRDROP_DIST":"🟠","UNKNOWN":"⚪"}

def find_vesting_lite(slug, token, chain, min_amount=5000):
    """Lido/RPL처럼 tx 많은 토큰용 — 최근 2년 + tx<50000으로 범위 축소"""
    rows = run_sql(f"""
WITH raw AS (
  SELECT "from" AS ca, "to" AS recip, value, evt_block_time
  FROM erc20_{chain}.evt_Transfer
  WHERE contract_address = {token}
    AND evt_block_time >= NOW() - INTERVAL '730' DAY
),
ivs AS (
  SELECT ca,
    DATE_DIFF('day',
      LAG(evt_block_time) OVER (PARTITION BY ca ORDER BY evt_block_time),
      evt_block_time) AS gap
  FROM raw
),
gap_stats AS (
  SELECT ca, ROUND(AVG(gap),1) AS avg_gap, ROUND(STDDEV(gap),1) AS std_gap
  FROM ivs WHERE gap IS NOT NULL GROUP BY 1
),
agg AS (
  SELECT ca,
    COUNT(DISTINCT recip)                          AS recips,
    ROUND(SUM(TRY_CAST(value AS DOUBLE))/1e18, 0) AS total,
    COUNT(*)                                       AS txs,
    CAST(MIN(evt_block_time) AS DATE)              AS start_dt,
    CAST(MAX(evt_block_time) AS DATE)              AS last_dt
  FROM raw GROUP BY 1
  HAVING COUNT(DISTINCT recip) BETWEEN 2 AND 200
    AND SUM(TRY_CAST(value AS DOUBLE))/1e18 > {min_amount}
    AND COUNT(*) < 50000
)
SELECT a.ca, a.recips, a.total, a.txs, a.start_dt, a.last_dt, g.avg_gap, g.std_gap
FROM agg a LEFT JOIN gap_stats g ON a.ca = g.ca
ORDER BY a.total DESC LIMIT 25
""", f"{slug} lite scan")
    return [r for r in rows if r.get("total", 0) < 1e18]

with open(SEEDS_PATH) as f:
    all_projects = json.load(f)

col = get_db()["verified_vesting_contracts"]
done = {x["project_slug"] for x in col.find({}, {"project_slug": 1})}

REMAINING = [
    p for p in all_projects
    if p["slug"] not in done
    and p.get("token_address")
    and DUNE_CHAINS.get(p.get("chain",""))
]

# Lido와 RPL은 lite 모드로
LITE_SLUGS = {"lido", "rocketpool"}

print(f"\n{'='*65}")
print(f"  미처리 프로젝트 재실행: {len(REMAINING)}개")
print(f"{'='*65}\n")

total_vesting = 0
for proj in REMAINING:
    slug  = proj["slug"]
    token = proj["token_address"]
    chain = DUNE_CHAINS[proj["chain"]]

    print(f"\n📦 {slug.upper()}")
    try:
        if slug in LITE_SLUGS:
            rows = find_vesting_lite(slug, token, chain)
            # 분류기 수동 적용
            from collectors.contract_verifier import classify_contract, get_blockscout_info
            results = []
            for row in rows:
                bs = get_blockscout_info(chain, row["ca"]); time.sleep(0.2)
                cls, score = classify_contract(row["ca"], row["recips"], row["txs"],
                    row.get("avg_gap"), bs.get("name",""), bs.get("tags",[]))
                results.append({
                    "address": row["ca"], "project_slug": slug, "chain": chain,
                    "token_address": token, "classification": cls, "vesting_score": score,
                    "unique_recipients": row["recips"], "total_distributed": row["total"],
                    "transfer_count": row["txs"],
                    "start_date": str(row.get("start_dt","")),
                    "latest_date": str(row.get("last_dt","")),
                    "avg_days_between_tx": row.get("avg_gap"),
                    "interval_stddev": row.get("std_gap"),
                    "contract_name": bs.get("name",""),
                    "is_verified": bs.get("is_verified", False),
                    "tags": bs.get("tags",[]),
                    "is_vesting": score >= 9,
                    "fetched_at": datetime.utcnow(),
                })
            results.sort(key=lambda x: (-x["vesting_score"], -x["total_distributed"]))
        else:
            results = find_and_verify_vesting_contracts(slug, token, chain)
    except Exception as e:
        logger.error(f"[{slug}] 오류: {e}")
        time.sleep(5); continue

    for doc in results:
        col.update_one({"address": doc["address"], "project_slug": slug},
                       {"$set": doc}, upsert=True)

    vesting  = [r for r in results if r["is_vesting"]]
    multisig = [r for r in results if r["classification"] == "TEAM_MULTISIG"]
    streaming= [r for r in results if "STREAMING" in r["classification"]]
    total_vesting += len(vesting)

    print(f"  🟢 베스팅={len(vesting)} 🔵 멀티시그={len(multisig)} 🔴 스트리밍={len(streaming)}")
    for r in results:
        if r["vesting_score"] >= 5:
            icon = ICON.get(r["classification"],"⚪")
            name = r["contract_name"] or r["address"][:14]+"..."
            total_str = f"{r['total_distributed']:>14,.0f}" if r['total_distributed'] < 1e18 else "  (overflow)"
            print(f"  {icon} {r['address'][:14]}... {total_str} {proj['token_symbol']:<6}"
                  f" | recips={r['unique_recipients']:>3} | {str(r.get('avg_days_between_tx') or '?'):>5}d | {name[:28]}")
    time.sleep(3)

# 최종 요약
db = get_db()
total_col = db["verified_vesting_contracts"]
print(f"\n{'='*65}")
print(f"  ✅ 재실행 완료 | 이번 베스팅 확인: {total_vesting}개")
print(f"  📊 전체 DB: {total_col.count_documents({})} contracts / "
      f"{total_col.count_documents({'is_vesting': True})} vesting")
by_cls = list(total_col.aggregate([
    {"$group": {"_id": "$classification", "count": {"$sum": 1}}},
    {"$sort": {"count": -1}}
]))
for x in by_cls:
    print(f"     {ICON.get(x['_id'],'⚪')} {x['_id']:<22} {x['count']:>4}개")
print(f"{'='*65}\n")
