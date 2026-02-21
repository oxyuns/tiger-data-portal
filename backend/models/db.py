"""
MongoDB connection and collection helpers
"""

import os
from pymongo import MongoClient, ASCENDING, DESCENDING
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/tiger_data_portal")
_client = None


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URI)
    return _client.get_default_database()


def get_collection(name: str):
    return get_db()[name]


# Shorthand accessors
def projects():       return get_collection("projects")
def vesting_contracts(): return get_collection("vesting_contracts")
def wallets():        return get_collection("wallets")
def wallet_labels():  return get_collection("wallet_labels")
def transactions():   return get_collection("transactions")
def vesting_streams(): return get_collection("vesting_streams")
