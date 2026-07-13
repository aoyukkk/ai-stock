from pathlib import Path

from review.performance_cache import PerformanceCacheManager, stable_hash


def test_cache_atomic_write_checksum_and_tamper_rejection(tmp_path: Path) -> None:
    manager = PerformanceCacheManager(tmp_path)
    checksum, count, path = manager.write("run-1", {"status": "SUCCESS"}, {"cohorts": [{}], "daily": [{}], "stocks": [{}, {}]})
    assert count == 4
    assert manager.validate(path, checksum, count)
    assert not list(tmp_path.glob("*.tmp"))
    path.write_text(path.read_text(encoding="utf-8").replace("SUCCESS", "FAILED"), encoding="utf-8")
    assert not manager.validate(path, checksum, count)


def test_input_hash_changes_with_contract_fields() -> None:
    base = {"return_basis": "NEXT_OPEN", "weighting_mode": "EQUAL_WEIGHT", "selection_scope": "FINAL_CANDIDATES"}
    assert stable_hash(base) != stable_hash({**base, "return_basis": "SIGNAL_CLOSE"})
    assert stable_hash(base) != stable_hash({**base, "weighting_mode": "SUGGESTED_POSITION_WEIGHT"})
    assert stable_hash(base) != stable_hash({**base, "selection_scope": "LLM_ONLY"})
