from __future__ import annotations

import sqlite3

from sqlalchemy.pool import NullPool

from database.session import create_engine_from_url
from scripts.publish_business_results_to_internal_web import REQUIRED_TABLES, publish_snapshot
from scripts.run_postclose_official_once import _publish_internal_web_snapshot


def test_business_snapshot_publish_is_verified_and_replaceable(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "published" / "business.db"
    connection = sqlite3.connect(source)
    try:
        for table in REQUIRED_TABLES:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        connection.execute("INSERT INTO midday_recommendation_run DEFAULT VALUES")
        connection.commit()
    finally:
        connection.close()

    first = publish_snapshot(source, destination)
    assert first["published"] is True
    assert first["table_counts"]["midday_recommendation_run"] == 1

    connection = sqlite3.connect(source)
    try:
        connection.execute("INSERT INTO midday_recommendation_run DEFAULT VALUES")
        connection.commit()
    finally:
        connection.close()
    second = publish_snapshot(source, destination)
    assert second["table_counts"]["midday_recommendation_run"] == 2
    assert second["destination_hash"] != first["destination_hash"]


def test_readonly_sqlite_engine_releases_file_handles(tmp_path):
    database = tmp_path / "readonly.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    url = f"sqlite+pysqlite:///file:{database.as_posix()}?mode=ro&uri=true"
    engine = create_engine_from_url(url)
    try:
        assert isinstance(engine.pool, NullPool)
    finally:
        engine.dispose()


def test_postclose_snapshot_sync_uses_configured_paths(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    destination = tmp_path / "published" / "business.db"
    destination.parent.mkdir()
    connection = sqlite3.connect(source)
    try:
        for table in REQUIRED_TABLES:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setenv("AI_TRADER_DB_PATH", str(source))
    monkeypatch.setenv("INTERNAL_WEB_BUSINESS_DATABASE_PATH", str(destination))

    result = _publish_internal_web_snapshot()

    assert result["status"] == "SUCCESS"
    assert result["integrity_check"] == "ok"
    assert destination.is_file()
