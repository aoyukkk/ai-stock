from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_db_contract_alignment_doc_exists_and_contains_key_mappings() -> None:
    path = ROOT / "docs" / "db_contract_alignment.md"

    assert path.is_file()
    text = path.read_text(encoding="utf-8")

    assert "`news` | `news_raw` / `news_event`" in text
    assert "`news_stock_relation` | `event_stock_link`" in text
    assert "`trading_account` | `virtual_account`" in text
    assert "`trade_order` | `virtual_order`" in text
    assert "`position` | `virtual_position`" in text
    assert "`trade_record` | `virtual_trade_record`" in text
