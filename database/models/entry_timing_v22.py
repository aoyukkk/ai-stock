from __future__ import annotations

from datetime import date
from decimal import Decimal
from sqlalchemy import Boolean, Date, Index, JSON, Numeric, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column
from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin

SCORE=Numeric(14,6)

class MarketRegimeV2Snapshot(IDMixin,TimestampMixin,ReprMixin,Base):
    __tablename__="market_regime_v2_snapshot"
    __table_args__=(UniqueConstraint("trade_date","input_hash","version",name="uq_regime_v2_trade_hash_version"),Index("ix_regime_v2_trade_state","trade_date","current_state"))
    trade_date:Mapped[date]=mapped_column(Date,nullable=False);input_hash:Mapped[str]=mapped_column(String(64),nullable=False);version:Mapped[str]=mapped_column(String(64),nullable=False)
    previous_state:Mapped[str|None]=mapped_column(String(32));current_state:Mapped[str]=mapped_column(String(32),nullable=False);raw_state:Mapped[str]=mapped_column(String(32),nullable=False)
    state_reasons_json:Mapped[list]=mapped_column(JSON,nullable=False);cooldown_remaining:Mapped[int]=mapped_column(nullable=False);confirmation_count:Mapped[int]=mapped_column(nullable=False);input_coverage:Mapped[Decimal]=mapped_column(SCORE,nullable=False);source_snapshot_hash:Mapped[str]=mapped_column(String(64),nullable=False)

class DeploymentV22Run(IDMixin,TimestampMixin,ReprMixin,Base):
    __tablename__="deployment_v22_run"
    __table_args__=(Index("ix_deployment_v22_trade","trade_date"),Index("ix_deployment_v22_input","input_hash",unique=True))
    run_id:Mapped[str]=mapped_column(String(64),nullable=False,unique=True);trade_date:Mapped[date]=mapped_column(Date,nullable=False);v2_run_id:Mapped[str]=mapped_column(String(64),nullable=False);input_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    regime_state:Mapped[str]=mapped_column(String(32),nullable=False);candidate_before:Mapped[int]=mapped_column(nullable=False);after_regime:Mapped[int]=mapped_column(nullable=False);after_concentration:Mapped[int]=mapped_column(nullable=False);triggered_count:Mapped[int]=mapped_column(nullable=False)
    config_snapshot:Mapped[dict]=mapped_column(JSON,nullable=False);status:Mapped[str]=mapped_column(String(32),nullable=False);shadow_only:Mapped[bool]=mapped_column(Boolean,nullable=False,default=True);llm_calls:Mapped[int]=mapped_column(nullable=False,default=0);external_calls:Mapped[int]=mapped_column(nullable=False,default=0);orders_created:Mapped[int]=mapped_column(nullable=False,default=0);source_hashes_json:Mapped[dict]=mapped_column(JSON,nullable=False)

class DeploymentV22Result(IDMixin,TimestampMixin,ReprMixin,Base):
    __tablename__="deployment_v22_result"
    __table_args__=(UniqueConstraint("run_id","stock_code","pool_type",name="uq_deploy_v22_run_stock_pool"),Index("ix_deploy_v22_status","trade_date","deployment_status"))
    run_id:Mapped[str]=mapped_column(String(64),nullable=False);trade_date:Mapped[date]=mapped_column(Date,nullable=False);stock_code:Mapped[str]=mapped_column(String(32),nullable=False);stock_name:Mapped[str|None]=mapped_column(String(128));pool_type:Mapped[str]=mapped_column(String(32),nullable=False)
    industry:Mapped[str|None]=mapped_column(String(128));cluster_id:Mapped[str|None]=mapped_column(String(128));strategy_id:Mapped[str]=mapped_column(String(32),nullable=False);admission_score:Mapped[Decimal|None]=mapped_column(SCORE)
    regime_state:Mapped[str]=mapped_column(String(32),nullable=False);deployment_status:Mapped[str]=mapped_column(String(32),nullable=False);deployment_reason:Mapped[str]=mapped_column(String(128),nullable=False);position_multiplier:Mapped[Decimal]=mapped_column(SCORE,nullable=False)
    crowding_status:Mapped[str]=mapped_column(String(32),nullable=False);crowding_reason:Mapped[str]=mapped_column(String(128),nullable=False);retained_rank_in_sector:Mapped[int|None]=mapped_column();industry_candidate_count_before:Mapped[int]=mapped_column(nullable=False);industry_candidate_count_after:Mapped[int]=mapped_column(nullable=False);industry_pool_ratio:Mapped[Decimal|None]=mapped_column(SCORE)
    trigger_status:Mapped[str]=mapped_column(String(32),nullable=False);trigger_reasons_json:Mapped[list]=mapped_column(JSON,nullable=False);trigger_scores_json:Mapped[dict]=mapped_column(JSON,nullable=False);version:Mapped[str]=mapped_column(String(64),nullable=False)

class MarketAdjustedEvaluation(IDMixin,TimestampMixin,ReprMixin,Base):
    __tablename__="market_adjusted_evaluation"
    __table_args__=(UniqueConstraint("run_id","stock_code","pool_type",name="uq_market_adjusted_run_stock_pool"),)
    run_id:Mapped[str]=mapped_column(String(64),nullable=False);trade_date:Mapped[date]=mapped_column(Date,nullable=False);stock_code:Mapped[str]=mapped_column(String(32),nullable=False);pool_type:Mapped[str]=mapped_column(String(32),nullable=False)
    benchmark_type:Mapped[str|None]=mapped_column(String(32));benchmark_code:Mapped[str|None]=mapped_column(String(128));benchmark_quality:Mapped[str]=mapped_column(String(32),nullable=False);benchmark_missing_reason:Mapped[str|None]=mapped_column(String(128));metrics_json:Mapped[dict]=mapped_column(JSON,nullable=False);version:Mapped[str]=mapped_column(String(64),nullable=False)

def _immutable(*_args,**_kwargs):raise ValueError("IMMUTABLE_ENTRY_TIMING_V22_SNAPSHOT")
for _model in (MarketRegimeV2Snapshot,DeploymentV22Run,DeploymentV22Result,MarketAdjustedEvaluation):
    event.listen(_model,"before_update",_immutable);event.listen(_model,"before_delete",_immutable)
