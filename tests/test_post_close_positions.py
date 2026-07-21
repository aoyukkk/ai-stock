from __future__ import annotations

import base64

from sqlalchemy import create_engine

from database.session import get_session, init_db
from post_close.positions import PositionImportService


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return get_session(engine)


def test_position_csv_preview_confirm_and_new_session_readback():
    session = make_session()
    content = "股票代码,股票名称,持仓数量,可卖数量,成本价,买入日期\n600000.SH,浦发银行,1000,800,10.50,2026-07-14\n"
    service = PositionImportService(session)
    preview = service.preview(filename="positions.csv", content_base64=base64.b64encode(content.encode()).decode(), account_scope="HUMAN_REFERENCE")
    assert preview["status"] == "VALID"
    confirmed = service.confirm(preview["preview_id"])
    assert confirmed["status"] == "CONFIRMED"
    assert service.confirm(preview["preview_id"])["row_count"] == 1
    rows = service.current("HUMAN_REFERENCE")
    assert rows[0].stock_code == "600000.SH"
    assert rows[0].quantity == 1000
    assert rows[0].available_quantity == 800
    session.close()


def test_position_import_rejects_invalid_quantity_and_cost():
    session = make_session()
    content = "stock_code,quantity,available_quantity,cost_price\n000001.SZ,100,200,0\n"
    preview = PositionImportService(session).preview(filename="positions.csv", content_base64=base64.b64encode(content.encode()).decode(), account_scope="HUMAN_REFERENCE")
    assert preview["status"] == "INVALID"
    assert preview["errors"]
    session.close()


def test_empty_position_file_can_confirm_audited_empty_human_snapshot():
    session = make_session()
    content = "stock_code,quantity,available_quantity,cost_price\n"
    service = PositionImportService(session)
    preview = service.preview(filename="positions.csv", content_base64=base64.b64encode(content.encode()).decode(), account_scope="HUMAN_REFERENCE")
    assert preview["status"] == "VALID"
    assert preview["row_count"] == 0
    assert service.confirm(preview["preview_id"])["status"] == "CONFIRMED"
    assert service.current("HUMAN_REFERENCE") == []
    session.close()
