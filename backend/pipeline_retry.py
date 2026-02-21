"""
타임아웃 난 프로젝트들을 large 클러스터로 재시도
"""
import logging, time
from models.db import get_db
from collectors.contract_verifier import find_and_verify_vesting_contracts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 타임아웃 또는 후보 없이 끝난 프로젝트들
RETRY_PROJECTS = [
    {"slug": "lido",       "token": "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32", "chain": "ethereum"},
    {"slug": "rocketpool", "token": "0xD33526068D116cE69F19A9ee46F0bd304F21A51f", "chain": "ethereum"},
]

col = get_db()["verified_vesting_contracts"]

for p in RETRY_PROJECTS:
    print(f"\n=== {p['slug'].upper()} (large cluster) ===")
    results = find_and_verify_vesting_contracts(
        project_slug=p["slug"],
        token_address=p["token"],
        chain=p["chain"],
        min_amount=5000,
    )
    for doc in results:
        col.update_one(
            {"address": doc["address"], "project_slug": doc["project_slug"]},
            {"$set": doc}, upsert=True,
        )
    vesting = [r for r in results if r["is_vesting"]]
    print(f"  → {len(vesting)} vesting / {len(results)} 전체")
    for v in vesting:
        print(f"  🟢 {v['address'][:14]}... {v['total_distributed']:>14,.0f} | "
              f"recips={v['unique_recipients']} | {v.get('contract_name','')[:25]}")
    time.sleep(5)
