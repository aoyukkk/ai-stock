from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.api.v1 import database as database_api
from database.base import Base
from database.models.stock import StockMaster
from database.session import create_engine_from_url, get_database_url, get_session
from database.stock_master_sync import (
    ManualStockResolutionError,
    ManualStockResolver,
    StockMasterSyncService,
)
from scripts import sync_stock_master as sync_cli


MANUAL_ROWS = [
    ("300145.SZ", "南方泵业", "机械基件", "20101209"),
    ("300821.SZ", "东岳硅材", "化工原料", "20200312"),
    ("301151.SZ", "冠龙节能", "机械基件", "20220411"),
    ("301356.SZ", "天振股份", "家居用品", "20221114"),
    ("603726.SH", "朗迪集团", "家用电器", "20160421"),
    ("603019.SH", "中科曙光", "IT设备", "20141106"),
    ("002409.SZ", "雅克科技", "半导体", "20100525"),
]


def _cache(tmp_path: Path, rows=MANUAL_ROWS) -> Path:
    root = tmp_path / "stock_basic"
    path = root / "20250331" / "cache.json"
    path.parent.mkdir(parents=True)
    records = [
        {
            "ts_code": code,
            "symbol": code.split(".")[0],
            "name": name,
            "market": "主板",
            "industry": industry,
            "list_date": list_date,
        }
        for code, name, industry, list_date in rows
    ]
    path.write_text(
        json.dumps(
            {
                "metadata": {
                    "interface": "stock_basic",
                    "status": "available",
                    "schema_version": "fundamental-cache-v1",
                },
                "records": records,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def _engine(tmp_path: Path):
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'sync.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def test_empty_stock_master_backfills_commits_and_new_session_reads(tmp_path) -> None:
    engine = _engine(tmp_path)
    cache = _cache(tmp_path)
    first = get_session(engine)
    try:
        result = StockMasterSyncService(first, cache).sync_from_cache(commit=True)
        assert result.rows_inserted == 7
        assert result.rows_read == 7
    finally:
        first.close()

    second = get_session(engine)
    try:
        assert second.scalar(select(func.count()).select_from(StockMaster)) == 7
        assert second.scalar(select(StockMaster).where(StockMaster.code == "300145.SZ")).name == "南方泵业"
    finally:
        second.close()
        engine.dispose()


def test_stock_master_upsert_is_idempotent_and_ts_code_unique(tmp_path) -> None:
    engine = _engine(tmp_path)
    cache = _cache(tmp_path)
    session = get_session(engine)
    try:
        first = StockMasterSyncService(session, cache).sync_from_cache(commit=True)
        second = StockMasterSyncService(session, cache).sync_from_cache(commit=True)
        codes = session.scalars(select(StockMaster.code)).all()
        assert first.rows_inserted == 7
        assert second.rows_inserted == 0
        assert second.rows_updated == 0
        assert second.rows_skipped == 7
        assert len(codes) == len(set(codes)) == 7
        assert all(len(code) == 9 and code[-3:] in {".SZ", ".SH", ".BJ"} for code in codes)
    finally:
        session.close()
        engine.dispose()


def test_seven_manual_stocks_resolve_by_exact_name(tmp_path) -> None:
    engine = _engine(tmp_path)
    cache = _cache(tmp_path)
    session = get_session(engine)
    try:
        StockMasterSyncService(session, cache).sync_from_cache(commit=True)
        names = [row[1] for row in MANUAL_ROWS]
        results = ManualStockResolver(session).resolve_all(names)
        assert [item.ts_code for item in results] == [row[0] for row in MANUAL_ROWS]
        assert all(item.match_method == "EXACT_NAME" for item in results)
        assert all(item.listing_status == "L" for item in results)
    finally:
        session.close()
        engine.dispose()


def test_invalid_and_ambiguous_manual_names_fail(tmp_path) -> None:
    engine = _engine(tmp_path)
    session = get_session(engine)
    try:
        session.add_all(
            [
                StockMaster(code="000001.SZ", name="同名证券", market="SZ", status="L"),
                StockMaster(code="600001.SH", name="同名证券", market="SH", status="L"),
            ]
        )
        session.commit()
        resolver = ManualStockResolver(session)
        with pytest.raises(ManualStockResolutionError, match="NOT_FOUND"):
            resolver.resolve("不存在证券")
        with pytest.raises(ManualStockResolutionError, match="AMBIGUOUS"):
            resolver.resolve("同名证券")
    finally:
        session.close()
        engine.dispose()


def test_sync_has_zero_llm_calls_and_does_not_leak_token(tmp_path, monkeypatch) -> None:
    engine = _engine(tmp_path)
    cache = _cache(tmp_path)
    monkeypatch.setenv("TUSHARE_TOKEN", "sensitive-test-token")
    session = get_session(engine)
    try:
        result = StockMasterSyncService(session, cache).sync_from_cache(commit=True)
        serialized = json.dumps(result.as_dict())
        assert result.llm_call_count == 0
        assert result.per_stock_api_call_count == 0
        assert "sensitive-test-token" not in serialized
        assert "TUSHARE_TOKEN" not in serialized
    finally:
        session.close()
        engine.dispose()


def test_cli_and_api_use_same_database_configuration() -> None:
    assert sync_cli.get_database_url() == database_api.get_database_url() == get_database_url()
    identity = sync_cli.active_database_identity()
    assert identity["dialect"] in {"sqlite", "postgresql"}
    assert "password" not in json.dumps(identity).lower()
    assert "@" not in identity["connection_fingerprint"]
