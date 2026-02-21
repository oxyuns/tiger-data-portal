"""
Tiger Data Portal - 베스팅 컨트랙트 검증 파이프라인
전체 EVM 프로젝트에 대해 Dune + Blockscout로 검증 실행
"""

import json
import logging
import time
from pathlib import Path
from datetime import datetime

from models.db import get_db
from collectors.contract_verifier import find_and_verify_vesting_contracts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SEEDS_PATH = Path(__file__).parent.parent / "data" / "seeds" / "projects.json"

# Dune 지원 체인 (erc20_{chain}.evt_Transfer 테이블 있는 것만)
DUNE_SUPPORTED_CHAINS = {
    "ethereum":  "ethereum",
    "arbitrum":  "arbitrum",
    "optimism":  "optimism",
    "base":      "base",
    "polygon":   "polygon",
    "bnb":       "bnb",
    "avalanche": "avalanche_c",
}

ICON_MAP = {
    "VESTING_CONFIRMED":  "🟢✓",
    "VESTING_LIKELY":     "🟢",
    "VESTING_POSSIBLE":   "🟡",
    "TEAM_MULTISIG":      "🔵",
    "STREAMING":          "🔴",
    "STREAMING_PROTOCOL": "🔴",
    "EXCHANGE_DEX":       "🔴",
    "AIRDROP_DIST":       "🟠",
    "UNKNOWN":            "⚪",
}


def run_verification_pipeline():
    with open(SEEDS_PATH) as f:
        projects = json.load(f)

    col = get_db()["verified_vesting_contracts"]

    total_found   = 0
    total_vesting = 0
    skipped       = 0

    print(f"\n{'='*70}")
    print(f"  Tiger Data Portal — 베스팅 컨트랙트 검증 파이프라인")
    print(f"  프로젝트: {len(projects)}개 | 시작: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*70}\n")

    for proj in projects:
        slug         = proj["slug"]
        token_addr   = proj.get("token_address")
        chain        = proj.get("chain", "ethereum")
        dune_chain   = DUNE_SUPPORTED_CHAINS.get(chain)

        if not token_addr or not dune_chain:
            logger.debug(f"[{slug}] 스킵 (토큰 주소 없음 or 미지원 체인: {chain})")
            skipped += 1
            continue

        try:
            results = find_and_verify_vesting_contracts(
                project_slug=slug,
                token_address=token_addr,
                chain=dune_chain,
                min_amount=5000,
            )
        except Exception as e:
            logger.error(f"[{slug}] 오류: {e}")
            time.sleep(5)
            continue

        if not results:
            print(f"  ⚫ {slug:<20} — 후보 없음")
            time.sleep(2)
            continue

        # MongoDB 저장
        for doc in results:
            col.update_one(
                {"address": doc["address"], "project_slug": doc["project_slug"]},
                {"$set": doc},
                upsert=True,
            )

        vesting   = [r for r in results if r["is_vesting"]]
        multisig  = [r for r in results if r["classification"] == "TEAM_MULTISIG"]
        streaming = [r for r in results if r["classification"] in ("STREAMING", "STREAMING_PROTOCOL")]
        other     = len(results) - len(vesting) - len(multisig) - len(streaming)

        total_found   += len(results)
        total_vesting += len(vesting)

        print(f"\n  📦 {slug.upper():<22} | 총 {len(results)}개 후보")
        print(f"     🟢 베스팅: {len(vesting):>2}  🔵 멀티시그: {len(multisig):>2}"
              f"  🔴 스트리밍: {len(streaming):>2}  ⚪ 기타: {other:>2}")

        for r in results:
            if r["vesting_score"] >= 5:   # 주목할 만한 것만 출력
                icon  = ICON_MAP.get(r["classification"], "⚪")
                name  = r["contract_name"] or r["address"][:16] + "..."
                total = r['total_distributed']
                # uint256 오버플로우 값 필터
                total_str = f"{total:>14,.0f}" if total < 1e18 else "  (overflow)"
                print(f"     {icon} {r['address'][:14]}... "
                      f"{total_str} {proj['token_symbol']:<6} "
                      f"| recips={r['unique_recipients']:>3} "
                      f"| {str(r.get('avg_days_between_tx') or '?'):>5}d "
                      f"| {name[:30]}")

        # Dune rate limit 방지
        time.sleep(3)

    # 최종 요약
    print(f"\n{'='*70}")
    print(f"  ✅ 완료 | 총 후보: {total_found} | 베스팅 확인: {total_vesting} | 스킵: {skipped}")

    # DB 요약
    db = get_db()
    by_cls = list(db["verified_vesting_contracts"].aggregate([
        {"$group": {"_id": "$classification", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]))
    print(f"\n  📊 분류별 현황:")
    for x in by_cls:
        icon = ICON_MAP.get(x["_id"], "⚪")
        print(f"     {icon} {x['_id']:<25} {x['count']:>4}개")

    top_vesting = list(db["verified_vesting_contracts"].find(
        {"is_vesting": True},
        {"project_slug": 1, "address": 1, "total_distributed": 1,
         "token_address": 1, "unique_recipients": 1, "contract_name": 1, "_id": 0},
    ).sort("total_distributed", -1).limit(15))

    print(f"\n  🏆 베스팅 확인 상위 15개:")
    for v in top_vesting:
        name = v.get("contract_name") or v["address"][:14] + "..."
        print(f"     [{v['project_slug']:<12}] {v['address'][:14]}... "
              f"| {v['total_distributed']:>15,.0f} tokens "
              f"| recips={v['unique_recipients']:>3} | {name[:25]}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    run_verification_pipeline()
