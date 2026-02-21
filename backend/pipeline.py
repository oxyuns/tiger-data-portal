"""
Tiger Data Portal - Main Data Pipeline
Orchestrates data collection from all sources into MongoDB
"""

import json
import logging
import time
from pathlib import Path
from datetime import datetime

from models.db import get_db, projects, vesting_streams
from collectors.sablier_collector import fetch_streams_by_token, normalize_stream as norm_sablier
from collectors.blockscout_collector import (
    find_vesting_contracts_from_token,
    get_address_info as blockscout_addr_info,
)
from collectors.ethplorer_collector import get_top_holders, get_token_info, normalize_holder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SEEDS_PATH = Path(__file__).parent.parent / "data" / "seeds" / "projects.json"

CHAIN_ID_MAP = {
    "ethereum": "1",
    "arbitrum": "42161",
    "optimism": "10",
    "base":     "8453",
    "polygon":  "137",
}


def seed_projects():
    col = projects()
    with open(SEEDS_PATH) as f:
        project_list = json.load(f)

    inserted = 0
    for p in project_list:
        p["created_at"] = datetime.utcnow()
        result = col.update_one({"slug": p["slug"]}, {"$setOnInsert": p}, upsert=True)
        if result.upserted_id:
            inserted += 1

    logger.info(f"Projects: {inserted} inserted, {len(project_list)-inserted} already existed")
    return project_list


def collect_sablier(project: dict) -> int:
    token_addr = project.get("token_address")
    chain = project.get("chain", "ethereum")
    chain_id = CHAIN_ID_MAP.get(chain)
    if not token_addr or not chain_id:
        return 0

    streams = fetch_streams_by_token(token_addr, chain_id=chain_id, limit=200)
    if not streams:
        return 0

    col = vesting_streams()
    saved = 0
    for s in streams:
        doc = norm_sablier(s)
        doc["project_slug"] = project["slug"]
        doc["project_name"] = project["name"]
        col.update_one({"stream_id": doc["stream_id"]}, {"$set": doc}, upsert=True)
        saved += 1

    logger.info(f"  [Sablier] {project['name']}: {saved} streams")
    return saved


def collect_top_holders(project: dict) -> int:
    token_addr = project.get("token_address")
    if not token_addr or project.get("chain") not in ("ethereum",):
        return 0

    holders = get_top_holders(token_addr, limit=50)
    if not holders:
        return 0

    col = get_db()["top_holders"]
    saved = 0
    for h in holders:
        doc = normalize_holder(h, token_addr, project.get("token_symbol", ""), project["slug"])
        col.update_one(
            {"address": doc["address"], "token_address": doc["token_address"]},
            {"$set": doc},
            upsert=True,
        )
        saved += 1

    logger.info(f"  [Ethplorer] {project['name']}: {saved} top holders")
    time.sleep(0.3)  # rate limit 방지
    return saved


def collect_blockscout_contracts(project: dict) -> int:
    token_addr = project.get("token_address")
    chain = project.get("chain", "ethereum")
    if not token_addr or chain not in ("ethereum", "optimism", "arbitrum", "base"):
        return 0

    contracts = find_vesting_contracts_from_token(chain, token_addr)
    if not contracts:
        return 0

    col = get_db()["vesting_contract_candidates"]
    saved = 0
    for c in contracts:
        # 검증된 컨트랙트 또는 태그 있는 것만
        if c.get("is_verified") or c.get("tags"):
            doc = {
                **c,
                "project_slug": project["slug"],
                "project_name": project["name"],
                "token_address": token_addr,
                "chain": chain,
                "fetched_at": datetime.utcnow(),
            }
            col.update_one(
                {"address": c["address"], "project_slug": project["slug"]},
                {"$set": doc},
                upsert=True,
            )
            saved += 1

    if saved:
        logger.info(f"  [Blockscout] {project['name']}: {saved} contract candidates")
    return saved


def run_full_pipeline(sources=("sablier", "ethplorer", "blockscout")):
    logger.info("=== Tiger Data Portal Pipeline START ===")

    project_list = seed_projects()

    # EVM 프로젝트만 필터
    evm = [p for p in project_list if p.get("token_address") and
           p.get("chain") in ("ethereum", "arbitrum", "optimism", "base", "polygon")]
    eth_only = [p for p in evm if p.get("chain") == "ethereum"]

    totals = {"sablier": 0, "ethplorer": 0, "blockscout": 0}

    for project in evm:
        logger.info(f"Processing: {project['name']} ({project['chain']})")

        if "sablier" in sources:
            totals["sablier"] += collect_sablier(project)

        if "ethplorer" in sources and project in eth_only:
            totals["ethplorer"] += collect_top_holders(project)
            time.sleep(0.5)

        if "blockscout" in sources:
            totals["blockscout"] += collect_blockscout_contracts(project)
            time.sleep(0.3)

    logger.info(f"=== Pipeline DONE ===")

    # 요약
    db = get_db()
    print(f"\n📊 DB 현황:")
    print(f"  Projects:             {db['projects'].count_documents({})}")
    print(f"  Vesting Streams:      {db['vesting_streams'].count_documents({})}")
    print(f"  Top Holders:          {db['top_holders'].count_documents({})}")
    print(f"  Contract Candidates:  {db['vesting_contract_candidates'].count_documents({})}")
    print(f"\n✅ 이번 실행 수집:")
    print(f"  Sablier streams:     {totals['sablier']}")
    print(f"  Ethplorer holders:   {totals['ethplorer']}")
    print(f"  Blockscout contracts:{totals['blockscout']}")

    # Top 인사이더 후보
    print(f"\n🔍 주요 인사이더 후보 (보유량 상위):")
    top = db["top_holders"].find(
        {"is_insider_candidate": True},
        {"project_slug": 1, "address": 1, "share_percent": 1, "token_symbol": 1, "balance": 1}
    ).sort("share_percent", -1).limit(10)
    for h in top:
        print(f"  [{h.get('project_slug','?')}] {h.get('address','')[:14]}... "
              f"{h.get('share_percent',0):.1f}% ({h.get('balance',0)/1e18:.0f} {h.get('token_symbol','')})")


if __name__ == "__main__":
    run_full_pipeline()
