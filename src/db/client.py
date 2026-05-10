"""
Database clients (singletons).

Two paths into Supabase:

1. `get_supabase_client()` → supabase-py Client (service_role).
   For typical row CRUD via PostgREST. RLS is bypassed.

2. `get_db_connection()` → psycopg connection (autocommit=True by default).
   For migrations, complex SQL, and anything PostgREST can't express.

Both read credentials from .env via python-dotenv on first call.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import psycopg
from dotenv import load_dotenv
from supabase import Client, create_client


_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_LOADED = False
_lock = threading.Lock()

_supabase: Optional[Client] = None
_dsn: Optional[str] = None


def _ensure_env() -> None:
    global _ENV_LOADED
    if not _ENV_LOADED:
        load_dotenv(_ROOT / ".env")
        _ENV_LOADED = True


def _require(name: str) -> str:
    _ensure_env()
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"{name} not set in .env")
    return val


def get_supabase_client() -> Client:
    """Return a singleton Supabase Client with service_role permissions."""
    global _supabase
    if _supabase is None:
        with _lock:
            if _supabase is None:
                _supabase = create_client(
                    _require("SUPABASE_URL"),
                    _require("SUPABASE_SERVICE_ROLE_KEY"),
                )
    return _supabase


def get_db_connection(*, autocommit: bool = True) -> psycopg.Connection:
    """
    Return a fresh psycopg connection. Caller is responsible for closing
    (use as a context manager).

    We deliberately do NOT pool / cache the connection: the Supabase pooler
    already pools server-side, and reusing a single conn across the app
    causes problems with transactions and threading.
    """
    global _dsn
    if _dsn is None:
        _dsn = _require("SUPABASE_DB_URL")
    return psycopg.connect(_dsn, autocommit=autocommit)
