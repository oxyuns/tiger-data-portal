"""
Tiger Data Portal - Main Data Pipeline
Orchestrates data collection from all sources into MongoDB
"""

import json
import logging
import sys
from pathlib import Path
from datetime import datetime

from models.db import projects, vesting_streams
from collectors.sablier_collector import fetch_streams_by_token, normalize_stream

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SEEDS_PATH = Path(__file__).parent.parent / "data" / "seeds" / "projects.json"


def seed_projects():
    """프로젝트 시드 데이터를 MongoDB에 로드"""
    col = projects()
    with open(SEEDS_PATH) as f:
        project_list = json.load(f)

    inserted, updated = 0, 0
    for p in project_list:
        p["created_at"] = datetime.utcnow()
        result = col.update_one(
            {"slug": p["slug"]},
            {"$setOnInsert": p},
            upsert=True,
        )
        if result.upserted_id:
            inserted += 1
        else:
            updated += 1

    logger.info(f"Projects seeded: {inserted} inserted, {updated} already existed")
    return project_list


def collect_sablier_for_project(project: dict) -> int:
    """단일 프로젝트의 Sablier 베스팅 스트림 수집"""
    token_addr = project.get("token_address")
    chain = project.get("chain", "ethereum")

    if not token_addr or chain not in ("ethereum", "arbitrum", "optimism", "base", "polygon"):
        return 0

    # Sablier chain_id 매핑
    chain_id_map = {
        "ethereum": "1",
        "arbitrum": "42161",
        "optimism": "10",
        "base": "8453",
        "polygon": "137",
    }
    chain_id = chain_id_map.get(chain)

    logger.info(f"Fetching Sablier streams for {project['name']} ({project['token_symbol']}) on {chain}")
    streams = fetch_streams_by_token(token_addr, chain_id=chain_id, limit=200)

    if not streams:
        logger.info(f"  → No streams found")
        return 0

    col = vesting_streams()
    saved = 0
    for stream in streams:
        doc = normalize_stream(stream)
        doc["project_slug"] = project["slug"]
        doc["project_name"] = project["name"]

        col.update_one(
            {"stream_id": doc["stream_id"]},
            {"$set": doc},
            upsert=True,
        )
        saved += 1

    logger.info(f"  → Saved {saved} streams")
    return saved


def run_full_pipeline():
    logger.info("=== Tiger Data Portal Pipeline START ===")

    # 1. 시드 데이터 로드
    project_list = seed_projects()

    # 2. ETH 기반 프로젝트 Sablier 수집
    total_streams = 0
    ethereum_projects = [p for p in project_list if p.get("token_address") and p.get("chain") in ("ethereum", "arbitrum", "optimism", "base")]

    logger.info(f"Collecting Sablier data for {len(ethereum_projects)} EVM projects...")
    for project in ethereum_projects:
        count = collect_sablier_for_project(project)
        total_streams += count

    logger.info(f"=== Pipeline DONE | Total streams collected: {total_streams} ===")

    # 3. 요약 출력
    db_projects = projects()
    db_streams = vesting_streams()
    print(f"\n📊 현재 DB 상태:")
    print(f"  Projects: {db_projects.count_documents({})}")
    print(f"  Vesting Streams: {db_streams.count_documents({})}")

    if total_streams > 0:
        print(f"\n🔍 상위 스트림 (금액 기준):")
        top = db_streams.find(
            {}, {"project_name": 1, "token_symbol": 1, "deposit_amount": 1, "recipient": 1, "start_time": 1, "end_time": 1}
        ).sort("deposit_amount", -1).limit(10)
        for s in top:
            print(f"  [{s['project_name']}] {s.get('deposit_amount', 0):,.0f} {s.get('token_symbol')} → {s.get('recipient', '')[:12]}... ({s.get('start_time', '?')} ~ {s.get('end_time', '?')})")


if __name__ == "__main__":
    run_full_pipeline()
