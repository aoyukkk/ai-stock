from __future__ import annotations

from datetime import date, datetime, timezone

from backend.workbench.historical import HistoricalPipelineRunResolver
from database.base import Base
from database.models.temporal import RunDataManifestRecord
from database.session import create_engine_from_url, get_session
from reporting import web_result_publish


def test_workbook_publish_creates_web_compatible_complete_chain(
    monkeypatch, tmp_path
) -> None:
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'web.db').as_posix()}")
    Base.metadata.create_all(engine)
    manifest_id = "manifest-web-publish-test"
    session = get_session(engine)
    session.add(
        RunDataManifestRecord(
            manifest_id=manifest_id,
            run_id="source-run",
            run_mode="SHADOW",
            decision_time=datetime(2030, 1, 2, 7, tzinfo=timezone.utc),
            base_market_trade_date=date(2030, 1, 2),
            target_trade_date=date(2030, 1, 3),
            fundamental_cutoff_time=datetime(
                2030, 1, 2, 7, tzinfo=timezone.utc
            ),
            required_dataset_watermarks=[],
            optional_dataset_watermarks=[],
            temporal_status="PASS",
            actionable=False,
            block_reasons=[],
            warnings=[],
            request_hash="manifest-web-publish-test-hash",
        )
    )
    session.commit()
    session.close()

    monkeypatch.setattr(
        web_result_publish, "get_session", lambda: get_session(engine)
    )
    monkeypatch.setattr(
        web_result_publish,
        "publish_internal_web_snapshot",
        lambda: {"status": "SUCCESS", "integrity_check": "ok"},
    )
    payload = {
        "candidates": [
            {
                "股票代码": "000001",
                "股票名称": "平安银行",
                "入选来源": "模型",
                "深度复核排名": 1,
                "深度复核分": 72,
                "复核优先级": "中",
                "核心逻辑": "测试逻辑",
                "主要风险": "测试风险",
                "建议仓位": 0,
            }
        ],
        "fundamentals": [
            {
                "股票代码": "000001",
                "产业链": "银行",
                "链条位置": "服务平台",
                "主营业务": "银行业务",
                "核心产品": "公司金融；零售金融",
                "概念标签": "银行",
                "结构性方向": "零售转型",
                "潜在优势": "客户基础",
                "行业趋势": "稳健",
                "核心逻辑": "估值与经营观察",
                "失效条件": "资产质量恶化",
                "财务状态": "稳健",
                "财务说明": "公开财务数据可读",
                "人工复核": "LLM补全",
            }
        ],
        "orders": [{"股票代码": "000001", "参考价": 10.0}],
    }
    final_audit = {
        "base_run_id": "source-run",
        "data_manifest_id": manifest_id,
        "market_regime": {"regime": "RISK_OFF"},
        "flash": {
            "top20": [
                {
                    "stock_code": "000001",
                    "llm_score": 68,
                    "screening_decision": "WATCH",
                }
            ]
        },
        "pro": {
            "results": [
                {
                    "stock_code": "000001",
                    "pro_score": 72,
                    "priority": "MEDIUM",
                    "final_summary": "测试逻辑",
                    "key_strengths": ["客户基础"],
                    "key_risks": ["资产质量"],
                }
            ]
        },
        "active_shadow": [],
        "order_plans": [],
    }
    fundamental_rows = [
        {
            "stock_code": "000001",
            "profile": {
                "stock_name": "平安银行",
                "profile_version": "test-v1",
                "financial_summary": {},
            },
            "inference": {},
        }
    ]

    publish_kwargs = dict(
        trade_date=date(2030, 1, 2),
        target_trade_date=date(2030, 1, 3),
        payload=payload,
        full_quant_rows=[
            {
                "stock_code": "000001",
                "rank": 1,
                "total_score": 70,
                "technical_score": 70,
                "capital_score": 70,
                "emotion_score": 65,
                "momentum_score": 72,
                "risk_score": 68,
            }
        ],
        final_audit=final_audit,
        fundamental_rows=fundamental_rows,
        workbook_path=tmp_path / "result.xlsx",
        workbook_sha256="a" * 64,
        workbook_content_hash="b" * 64,
        factor_version="V2_TEST",
    )
    result = web_result_publish.publish_workbook_payload(**publish_kwargs)
    reused = web_result_publish.publish_workbook_payload(**publish_kwargs)

    verify = get_session(engine)
    try:
        bundle = HistoricalPipelineRunResolver(verify).resolve(date(2030, 1, 2))
        assert result["status"] == "SUCCESS"
        assert result["reused"] is False
        assert reused["reused"] is True
        assert reused["counts"] == result["counts"]
        assert result["counts"] == {
            "quant": 1,
            "flash": 1,
            "pro": 1,
            "order": 1,
            "position": 1,
        }
        assert bundle["pipeline_run_id"] == result["pipeline_run_id"]
        assert bundle["consistency"] == {"status": "PASS", "differences": []}
        assert bundle["counts"]["fundamental"] == 1
    finally:
        verify.close()
        engine.dispose()
