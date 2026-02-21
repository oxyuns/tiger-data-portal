"""
Tiger Data Portal - FastAPI Server
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from models.db import projects, vesting_streams

app = FastAPI(title="Tiger Data Portal", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def serialize(doc):
    doc["_id"] = str(doc["_id"])
    return doc


@app.get("/")
def root():
    return {"message": "Tiger Data Portal API", "version": "0.1.0"}


@app.get("/projects")
def list_projects(category: str = None, chain: str = None):
    """50개 트래킹 프로젝트 목록"""
    query = {}
    if category:
        query["category"] = category
    if chain:
        query["chain"] = chain
    docs = list(projects().find(query, {"_id": 0}))
    return {"total": len(docs), "projects": docs}


@app.get("/projects/{slug}")
def get_project(slug: str):
    """프로젝트 상세 + 베스팅 스트림 요약"""
    proj = projects().find_one({"slug": slug}, {"_id": 0})
    if not proj:
        return {"error": "not found"}

    streams = list(vesting_streams().find(
        {"project_slug": slug},
        {"_id": 0, "stream_id": 1, "chain": 1, "sender": 1, "recipient": 1,
         "deposit_amount": 1, "token_symbol": 1, "start_time": 1, "end_time": 1,
         "canceled": 1, "source": 1}
    ).sort("deposit_amount", -1).limit(50))

    total_value = sum(s.get("deposit_amount", 0) for s in streams)

    return {
        "project": proj,
        "vesting_summary": {
            "total_streams": len(streams),
            "total_value": round(total_value, 2),
            "token": streams[0]["token_symbol"] if streams else None,
        },
        "streams": streams,
    }


@app.get("/streams")
def list_streams(
    project: str = None,
    chain: str = None,
    source: str = None,
    limit: int = Query(default=50, le=200),
    min_amount: float = None,
):
    """베스팅 스트림 목록 (필터 지원)"""
    query = {}
    if project:
        query["project_slug"] = project
    if chain:
        query["chain"] = chain
    if source:
        query["source"] = source
    if min_amount:
        query["deposit_amount"] = {"$gte": min_amount}

    docs = list(
        vesting_streams()
        .find(query, {"_id": 0})
        .sort("deposit_amount", -1)
        .limit(limit)
    )
    return {"total": len(docs), "streams": docs}


@app.get("/streams/top")
def top_streams(limit: int = Query(default=20, le=100)):
    """금액 기준 상위 베스팅 스트림"""
    docs = list(
        vesting_streams()
        .find({}, {"_id": 0, "project_name": 1, "token_symbol": 1,
                   "deposit_amount": 1, "recipient": 1, "sender": 1,
                   "start_time": 1, "end_time": 1, "source": 1, "chain": 1})
        .sort("deposit_amount", -1)
        .limit(limit)
    )
    return {"streams": docs}


@app.get("/wallets/{address}")
def wallet_info(address: str):
    """특정 지갑 주소의 베스팅 스트림 조회"""
    streams = list(
        vesting_streams()
        .find({"recipient": address.lower()}, {"_id": 0})
        .sort("deposit_amount", -1)
    )
    return {
        "address": address,
        "streams_as_recipient": len(streams),
        "streams": streams,
    }


@app.get("/stats")
def stats():
    """전체 통계"""
    total_projects = projects().count_documents({})
    total_streams = vesting_streams().count_documents({})

    # 체인별 스트림 수
    by_chain = list(vesting_streams().aggregate([
        {"$group": {"_id": "$chain", "count": {"$sum": 1}, "total_value": {"$sum": "$deposit_amount"}}},
        {"$sort": {"count": -1}}
    ]))

    # 프로젝트별 스트림 수
    by_project = list(vesting_streams().aggregate([
        {"$group": {"_id": "$project_name", "count": {"$sum": 1}, "total_value": {"$sum": "$deposit_amount"}}},
        {"$sort": {"total_value": -1}},
        {"$limit": 10}
    ]))

    return {
        "total_projects": total_projects,
        "total_streams": total_streams,
        "by_chain": by_chain,
        "top_projects_by_value": by_project,
    }
