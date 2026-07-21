from __future__ import annotations
from datetime import date,datetime
from sqlalchemy import Boolean,Date,DateTime,Index,JSON,String,Text,UniqueConstraint,event
from sqlalchemy.orm import Mapped,mapped_column
from database.base import Base
from database.models.mixins import IDMixin,ReprMixin,TimestampMixin

class MiddayV22Run(IDMixin,TimestampMixin,ReprMixin,Base):
    __tablename__="midday_v22_run"
    __table_args__=(Index("ix_midday_v22_trade_status","trade_date","status"),Index("ix_midday_v22_input_hash","input_hash"))
    run_id:Mapped[str]=mapped_column(String(64),unique=True,nullable=False);trade_date:Mapped[date]=mapped_column(Date,nullable=False);cutoff_time:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    run_mode:Mapped[str]=mapped_column(String(40),nullable=False);status:Mapped[str]=mapped_column(String(40),nullable=False);current_stage:Mapped[str]=mapped_column(String(40),nullable=False)
    previous_regime:Mapped[str|None]=mapped_column(String(32));midday_regime:Mapped[str|None]=mapped_column(String(32));input_hash:Mapped[str]=mapped_column(String(64),nullable=False);config_hash:Mapped[str]=mapped_column(String(64),nullable=False)
    quant_hash:Mapped[str|None]=mapped_column(String(64));flash_hash:Mapped[str|None]=mapped_column(String(64));pro_hash:Mapped[str|None]=mapped_column(String(64));output_hash:Mapped[str|None]=mapped_column(String(64))
    counts_json:Mapped[dict]=mapped_column(JSON,default=dict,nullable=False);provider_audit_json:Mapped[list]=mapped_column(JSON,default=list,nullable=False);checkpoint_json:Mapped[dict]=mapped_column(JSON,default=dict,nullable=False);warnings_json:Mapped[list]=mapped_column(JSON,default=list,nullable=False)
    failure_stage:Mapped[str|None]=mapped_column(String(40));error_code:Mapped[str|None]=mapped_column(String(80));error_message:Mapped[str|None]=mapped_column(Text);output_paths_json:Mapped[dict]=mapped_column(JSON,default=dict,nullable=False)
    real_orders:Mapped[int]=mapped_column(nullable=False,default=0);virtual_orders:Mapped[int]=mapped_column(nullable=False,default=0);scheduler_enabled:Mapped[bool]=mapped_column(Boolean,nullable=False,default=False);completed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))

class MiddayV22Result(IDMixin,TimestampMixin,ReprMixin,Base):
    __tablename__="midday_v22_result";__table_args__=(UniqueConstraint("run_id","stock_code","pool_type",name="uq_midday_v22_result"),)
    run_id:Mapped[str]=mapped_column(String(64),nullable=False);stock_code:Mapped[str]=mapped_column(String(32),nullable=False);stock_name:Mapped[str|None]=mapped_column(String(128));pool_type:Mapped[str]=mapped_column(String(32),nullable=False);result_layer:Mapped[str]=mapped_column(String(32),nullable=False);payload_json:Mapped[dict]=mapped_column(JSON,nullable=False)

class MiddayV22AfternoonRun(IDMixin,TimestampMixin,ReprMixin,Base):
    __tablename__="midday_v22_afternoon_run"
    __table_args__=(Index("ix_midday_v22_afternoon_trade_status","trade_date","status"),)
    run_id:Mapped[str]=mapped_column(String(64),unique=True,nullable=False);midday_run_id:Mapped[str]=mapped_column(String(64),nullable=False);trade_date:Mapped[date]=mapped_column(Date,nullable=False);recheck_time:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    status:Mapped[str]=mapped_column(String(40),nullable=False);current_stage:Mapped[str]=mapped_column(String(40),nullable=False);previous_midday_regime:Mapped[str|None]=mapped_column(String(32));afternoon_regime:Mapped[str|None]=mapped_column(String(32))
    counts_json:Mapped[dict]=mapped_column(JSON,default=dict,nullable=False);provider_audit_json:Mapped[list]=mapped_column(JSON,default=list,nullable=False);output_paths_json:Mapped[dict]=mapped_column(JSON,default=dict,nullable=False)
    failure_stage:Mapped[str|None]=mapped_column(String(40));error_code:Mapped[str|None]=mapped_column(String(80));error_message:Mapped[str|None]=mapped_column(Text);completed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    real_orders:Mapped[int]=mapped_column(nullable=False,default=0);virtual_orders:Mapped[int]=mapped_column(nullable=False,default=0);scheduler_enabled:Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)

class MiddayV22AfternoonResult(IDMixin,TimestampMixin,ReprMixin,Base):
    __tablename__="midday_v22_afternoon_result"
    __table_args__=(UniqueConstraint("run_id","stock_code",name="uq_midday_v22_afternoon_result"),)
    run_id:Mapped[str]=mapped_column(String(64),nullable=False);stock_code:Mapped[str]=mapped_column(String(32),nullable=False);stock_name:Mapped[str|None]=mapped_column(String(128));prior_layer:Mapped[str]=mapped_column(String(40),nullable=False);result_layer:Mapped[str]=mapped_column(String(40),nullable=False);trigger_status:Mapped[str]=mapped_column(String(40),nullable=False);payload_json:Mapped[dict]=mapped_column(JSON,nullable=False)

def _immutable(*_args,**_kwargs):raise ValueError("IMMUTABLE_MIDDAY_V22_RESULT")
event.listen(MiddayV22Result,"before_update",_immutable);event.listen(MiddayV22Result,"before_delete",_immutable)
event.listen(MiddayV22AfternoonResult,"before_update",_immutable);event.listen(MiddayV22AfternoonResult,"before_delete",_immutable)
