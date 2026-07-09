from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.core.config import AppConfig, get_app_config
from backend.core.security import is_sensitive_key
from database.models.mixins import utc_now
from database.models.system import ConfigHistory, SystemConfig
from database.session import get_session, init_db


class ConfigManagerError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.data = data
        super().__init__(message)


@dataclass(frozen=True)
class ConfigSpec:
    config_key: str
    yaml_file: str
    yaml_path: tuple[str, ...]
    default: Any
    value_type: str
    category: str
    description: str
    constraints: dict[str, Any]
    editable: bool = True
    is_sensitive: bool = False


CONFIG_PRIORITY = ["Web UI", "Database", "Config File", "Default"]
LOCAL_ADMIN = "local_admin"
DEFAULT_REASON = "frontend update"

FORBIDDEN_KEY_PARTS = (
    "enable_real_trading",
    "real_trading_enabled",
    "broker",
    "credential",
    "provider_credential",
)


def _spec(
    config_key: str,
    yaml_file: str,
    yaml_path: tuple[str, ...],
    default: Any,
    value_type: str,
    category: str,
    description: str,
    constraints: dict[str, Any] | None = None,
) -> ConfigSpec:
    return ConfigSpec(
        config_key=config_key,
        yaml_file=yaml_file,
        yaml_path=yaml_path,
        default=default,
        value_type=value_type,
        category=category,
        description=description,
        constraints=constraints or {},
    )


EDITABLE_CONFIG_SPECS: tuple[ConfigSpec, ...] = (
    _spec("stock_scan.quant_top_n", "stock_scan", ("stock_scan", "quant_top_n"), 500, "integer", "stock_scan", "Quant scan candidate count", {"min": 1, "max": 5000}),
    _spec("stock_scan.light_analysis_top_n", "stock_scan", ("stock_scan", "light_analysis_top_n"), 500, "integer", "stock_scan", "Light LLM analysis count", {"min": 1, "max": 5000}),
    _spec("stock_scan.committee_analysis_top_n", "stock_scan", ("stock_scan", "committee_analysis_top_n"), 50, "integer", "stock_scan", "AI committee input count", {"min": 1, "max": 1000}),
    _spec("stock_scan.final_recommend_top_n", "stock_scan", ("stock_scan", "final_recommend_top_n"), 50, "integer", "stock_scan", "Final recommendation count", {"min": 1, "max": 1000}),
    _spec("stock_scan.manual_watchlist_limit", "stock_scan", ("stock_scan", "manual_watchlist", "soft_warning_count"), 100, "integer", "stock_scan", "Manual watchlist soft limit", {"min": 1, "max": 1000}),
    _spec("stock_scan.allow_intraday_dynamic_pool", "stock_scan", ("stock_scan", "intraday_dynamic_entry", "enabled"), False, "boolean", "stock_scan", "Allow intraday dynamic pool", {}),
    _spec("market_data.full_market_refresh.interval_minutes", "system", ("refresh_frequency", "full_market_light_quant_minutes"), 60, "integer", "refresh_frequency", "Full market refresh interval", {"min": 1, "max": 240}),
    _spec("market_data.watchlist_refresh.interval_minutes", "system", ("refresh_frequency", "manual_watchlist_minutes"), 15, "integer", "refresh_frequency", "Watchlist refresh interval", {"min": 1, "max": 240}),
    _spec("market_data.holding_refresh.interval_minutes", "system", ("refresh_frequency", "holding_minutes"), 15, "integer", "refresh_frequency", "Holding refresh interval", {"min": 1, "max": 240}),
    _spec("market_data.pending_order_refresh.interval_minutes", "system", ("refresh_frequency", "pending_order_minutes"), 5, "integer", "refresh_frequency", "Pending order refresh interval", {"min": 1, "max": 240}),
    _spec("market_data.news_refresh.interval_minutes", "system", ("refresh_frequency", "news_minutes"), 15, "integer", "refresh_frequency", "News refresh interval", {"min": 1, "max": 240}),
    _spec("llm.mock_only", "models", ("llm", "mock_only"), True, "boolean", "llm", "LLM mock-only mode", {"fixed": True}),
    _spec("llm.default_provider", "models", ("llm", "default_provider"), "mock", "string", "llm", "Default LLM provider", {"allowed_values": ["mock"]}),
    _spec("llm.default_model", "models", ("llm", "default_model"), "mock-chat", "string", "llm", "Default LLM model", {"allowed_values": ["mock-chat"]}),
    _spec("llm.enable_cache", "models", ("llm", "enable_cache"), True, "boolean", "llm", "LLM response cache", {}),
    _spec("llm.cache_ttl_minutes", "models", ("llm", "cache_ttl_minutes"), 30, "integer", "llm", "LLM cache TTL", {"min": 1, "max": 1440}),
    _spec("llm.budgets.daily_token_budget", "models", ("llm", "budget", "daily_token_budget"), 1800000, "integer", "llm", "Daily token budget", {"min": 0, "max": 10000000}),
    _spec("llm.budgets.daily_cost_budget_usd", "models", ("llm", "budget", "daily_cost_budget_usd"), 8, "number", "llm", "Daily cost budget", {"min": 0, "max": 1000}),
    _spec("quant_factor.weights.technical", "quant_factor", ("quant_factor", "weights", "technical"), 0.25, "number", "quant_weights", "Technical factor weight", {"min": 0, "max": 1}),
    _spec("quant_factor.weights.capital", "quant_factor", ("quant_factor", "weights", "capital"), 0.25, "number", "quant_weights", "Capital factor weight", {"min": 0, "max": 1}),
    _spec("quant_factor.weights.emotion", "quant_factor", ("quant_factor", "weights", "emotion"), 0.20, "number", "quant_weights", "Emotion factor weight", {"min": 0, "max": 1}),
    _spec("quant_factor.weights.momentum", "quant_factor", ("quant_factor", "weights", "momentum"), 0.15, "number", "quant_weights", "Momentum factor weight", {"min": 0, "max": 1}),
    _spec("quant_factor.weights.risk", "quant_factor", ("quant_factor", "weights", "risk"), 0.15, "number", "quant_weights", "Risk factor weight", {"min": 0, "max": 1}),
    _spec("order_price.atr_window", "order_price", ("order_price", "atr_window"), 14, "integer", "order_price", "ATR window", {"min": 1, "max": 120}),
    _spec("order_price.tick_size", "order_price", ("order_price", "tick_size"), 0.01, "number", "order_price", "Price tick size", {"min": 0.001, "max": 1}),
    _spec("order_price.max_chase_percent", "order_price", ("order_price", "max_chase_percent"), 0.05, "number", "order_price", "Max chase percent", {"min": 0, "max": 1}),
    _spec("order_price.min_risk_reward", "order_price", ("order_price", "min_risk_reward"), 1.5, "number", "order_price", "Minimum risk-reward", {"min": 0, "max": 20}),
    _spec("order_price.ideal_risk_reward", "order_price", ("order_price", "ideal_risk_reward"), 2.0, "number", "order_price", "Ideal risk-reward", {"min": 0, "max": 20}),
    _spec("order_price.max_stop_loss_percent", "order_price", ("order_price", "max_stop_loss_percent"), 0.08, "number", "order_price", "Max stop-loss percent", {"min": 0, "max": 1}),
    _spec("order_price.default_position_percent", "order_price", ("order_price", "default_position_percent"), 0.10, "number", "order_price", "Default position percent", {"min": 0, "max": 1}),
    _spec("event_trigger.price.rapid_rise_percent", "risk_rules", ("event_trigger", "price", "rapid_rise_percent"), 8, "number", "risk_alert", "Rapid rise alert threshold", {"min": 0, "max": 20}),
    _spec("event_trigger.price.rapid_drop_percent", "risk_rules", ("event_trigger", "price", "rapid_drop_percent"), -5, "number", "risk_alert", "Rapid drop alert threshold", {"min": -20, "max": 0}),
    _spec("event_trigger.volume.abnormal_ratio", "risk_rules", ("event_trigger", "volume", "abnormal_ratio"), 3, "number", "risk_alert", "Abnormal volume ratio", {"min": 1, "max": 20}),
    _spec("event_trigger.turnover.abnormal_ratio", "risk_rules", ("event_trigger", "turnover", "abnormal_ratio"), 2, "number", "risk_alert", "Abnormal turnover ratio", {"min": 1, "max": 20}),
    _spec("event_trigger.news.importance_threshold", "risk_rules", ("event_trigger", "news", "importance_threshold"), 80, "number", "risk_alert", "News importance threshold", {"min": 0, "max": 100}),
    _spec("event_trigger.pre_market.high_open_cancel_threshold_percent", "risk_rules", ("event_trigger", "pre_market", "high_open_cancel_threshold_percent"), 6, "number", "risk_alert", "High-open cancel threshold", {"min": 0, "max": 20}),
    _spec("event_trigger.order.near_fill_threshold_percent", "risk_rules", ("event_trigger", "order", "near_fill_threshold_percent"), 0.3, "number", "risk_alert", "Near-fill threshold", {"min": 0, "max": 5}),
    _spec("event_trigger.order.reprice_threshold_percent", "risk_rules", ("event_trigger", "order", "reprice_threshold_percent"), 1.5, "number", "risk_alert", "Reprice threshold", {"min": 0, "max": 10}),
    _spec("paper_trading.initial_cash", "virtual_trading", ("virtual_trading", "initial_cash"), 1000000, "number", "virtual_trading", "Initial simulation cash", {"min": 0, "max": 100000000}),
    _spec("paper_trading.rules.allow_partial_fill", "virtual_trading", ("virtual_trading", "rules", "allow_partial_fill"), True, "boolean", "virtual_trading", "Allow partial virtual fill", {}),
    _spec("paper_trading.cost.commission_rate", "virtual_trading", ("virtual_trading", "cost", "commission_rate"), 0.0003, "number", "virtual_trading", "Virtual commission rate", {"min": 0, "max": 0.1}),
    _spec("paper_trading.cost.min_commission", "virtual_trading", ("virtual_trading", "cost", "min_commission"), 5, "number", "virtual_trading", "Virtual minimum commission", {"min": 0, "max": 1000}),
    _spec("paper_trading.cost.stamp_tax_rate", "virtual_trading", ("virtual_trading", "cost", "stamp_tax_rate"), 0.001, "number", "virtual_trading", "Virtual stamp tax rate", {"min": 0, "max": 0.1}),
    _spec("paper_trading.cost.slippage_rate", "virtual_trading", ("virtual_trading", "cost", "slippage_rate"), 0.0005, "number", "virtual_trading", "Virtual slippage rate", {"min": 0, "max": 0.1}),
    _spec("memory.retrieval.top_k", "memory", ("memory", "retrieval", "top_k"), 5, "integer", "memory", "Memory retrieval top K", {"min": 1, "max": 50}),
    _spec("memory.retrieval.min_quality_score", "memory", ("memory", "retrieval", "min_quality_score"), 50, "number", "memory", "Minimum memory quality score", {"min": 0, "max": 100}),
    _spec("memory.short_term.ttl_hours", "memory", ("memory", "short_term", "ttl_hours"), 24, "integer", "memory", "Short-term memory TTL hours", {"min": 1, "max": 720}),
    _spec("memory.mid_term.ttl_days", "memory", ("memory", "mid_term", "ttl_days"), 20, "integer", "memory", "Mid-term memory TTL days", {"min": 1, "max": 365}),
    _spec("memory.vector.enabled", "memory", ("memory", "vector", "enabled"), False, "boolean", "memory", "Vector memory storage", {"fixed": False}),
    _spec("memory.graph.enabled", "memory", ("memory", "graph", "enabled"), False, "boolean", "memory", "Graph memory storage", {"fixed": False}),
)

EDITABLE_CONFIG_BY_KEY = {spec.config_key: spec for spec in EDITABLE_CONFIG_SPECS}
QUANT_WEIGHT_KEYS = tuple(
    spec.config_key
    for spec in EDITABLE_CONFIG_SPECS
    if spec.category == "quant_weights"
)


class ConfigManager:
    def __init__(
        self,
        app_config: AppConfig | None = None,
        session: Session | None = None,
    ) -> None:
        self.app_config = app_config or get_app_config()
        self.session = session

    def get_effective_config(self) -> dict[str, Any]:
        overrides = self._load_database_overrides()
        values, sources = self._resolve_values(overrides)
        return {
            "priority": CONFIG_PRIORITY,
            "real_trading_enabled": False,
            "llm_mock_only": values["llm.mock_only"],
            "data_source_mode": "mock_only",
            "values": values,
            "sources": sources,
            "groups": self._build_groups(values, sources),
        }

    def get_config_value(self, key: str) -> Any:
        self._require_editable_key(key)
        return self.get_effective_config()["values"][key]

    def list_editable_config(self) -> list[dict[str, Any]]:
        effective = self.get_effective_config()
        return [
            self._item_payload(
                spec,
                effective["values"][spec.config_key],
                effective["sources"][spec.config_key],
            )
            for spec in EDITABLE_CONFIG_SPECS
        ]

    def set_config_value(
        self,
        key: str,
        value: Any,
        user: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        spec = self._require_editable_key(key)
        normalized = self._normalize_and_validate(spec, value)
        current_values = self.get_effective_config()["values"]
        proposed_values = {**current_values, key: normalized}
        self._validate_cross_key_rules(proposed_values)

        session, should_close = self._get_write_session()
        try:
            old_value = current_values[key]
            record = self._get_config_record(session, key)
            self._upsert_config_record(
                session=session,
                record=record,
                spec=spec,
                value=normalized,
                user=user or LOCAL_ADMIN,
            )
            self.write_config_history(
                key=key,
                old_value=old_value,
                new_value=normalized,
                user=user or LOCAL_ADMIN,
                reason=reason or DEFAULT_REASON,
                session=session,
            )
            session.commit()
            return {
                "config_key": key,
                "old_value": old_value,
                "new_value": normalized,
                "effective_value": normalized,
            }
        except SQLAlchemyError as exc:
            session.rollback()
            raise ConfigManagerError(
                code="CONFIG_DATABASE_UNAVAILABLE",
                message="Database is unavailable for config persistence",
                status_code=503,
            ) from exc
        finally:
            if should_close:
                session.close()

    def set_config_values_bulk(
        self,
        items: list[dict[str, Any]],
        user: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if not items:
            return {"items": []}

        normalized_items: dict[str, Any] = {}
        for item in items:
            key = str(item.get("config_key", ""))
            spec = self._require_editable_key(key)
            normalized_items[key] = self._normalize_and_validate(spec, item.get("value"))

        current_values = self.get_effective_config()["values"]
        proposed_values = {**current_values, **normalized_items}
        self._validate_cross_key_rules(proposed_values)

        session, should_close = self._get_write_session()
        changed: list[dict[str, Any]] = []
        try:
            for key, normalized in normalized_items.items():
                spec = EDITABLE_CONFIG_BY_KEY[key]
                old_value = current_values[key]
                record = self._get_config_record(session, key)
                self._upsert_config_record(
                    session=session,
                    record=record,
                    spec=spec,
                    value=normalized,
                    user=user or LOCAL_ADMIN,
                )
                self.write_config_history(
                    key=key,
                    old_value=old_value,
                    new_value=normalized,
                    user=user or LOCAL_ADMIN,
                    reason=reason or DEFAULT_REASON,
                    session=session,
                )
                changed.append(
                    {
                        "config_key": key,
                        "old_value": old_value,
                        "new_value": normalized,
                        "effective_value": normalized,
                    }
                )
            session.commit()
            return {"items": changed}
        except SQLAlchemyError as exc:
            session.rollback()
            raise ConfigManagerError(
                code="CONFIG_DATABASE_UNAVAILABLE",
                message="Database is unavailable for config persistence",
                status_code=503,
            ) from exc
        finally:
            if should_close:
                session.close()

    def reset_config_value(
        self,
        key: str,
        user: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        self._require_editable_key(key)
        old_value = self.get_config_value(key)
        session, should_close = self._get_write_session()
        try:
            session.execute(delete(SystemConfig).where(SystemConfig.config_key == key))
            reset_value = self._yaml_or_default(EDITABLE_CONFIG_BY_KEY[key])
            self.write_config_history(
                key=key,
                old_value=old_value,
                new_value=reset_value,
                user=user or LOCAL_ADMIN,
                reason=reason or "frontend reset",
                session=session,
            )
            session.commit()
            return {
                "config_key": key,
                "old_value": old_value,
                "new_value": reset_value,
                "effective_value": reset_value,
            }
        except SQLAlchemyError as exc:
            session.rollback()
            raise ConfigManagerError(
                code="CONFIG_DATABASE_UNAVAILABLE",
                message="Database is unavailable for config persistence",
                status_code=503,
            ) from exc
        finally:
            if should_close:
                session.close()

    def list_config_history(
        self,
        config_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if config_key:
            self._require_editable_key(config_key)

        limit = max(1, min(int(limit), 500))
        session, should_close = self._get_read_session()
        if session is None:
            return []

        try:
            query = select(ConfigHistory).order_by(ConfigHistory.time.desc()).limit(limit)
            if config_key:
                query = query.where(ConfigHistory.config_key == config_key)
            rows = session.scalars(query).all()
        except SQLAlchemyError:
            return []
        finally:
            if should_close:
                session.close()

        history: list[dict[str, Any]] = []
        for row in rows:
            if row.config_key not in EDITABLE_CONFIG_BY_KEY:
                continue
            history.append(
                {
                    "id": row.id,
                    "config_key": row.config_key,
                    "old_value": _unwrap_history_value(row.old_value),
                    "new_value": _unwrap_history_value(row.new_value),
                    "user": row.user,
                    "reason": row.reason,
                    "time": row.time.isoformat() if row.time else None,
                }
            )
        return history

    def validate_config_key(self, key: str, value: Any) -> Any:
        spec = self._require_editable_key(key)
        normalized = self._normalize_and_validate(spec, value)
        proposed = {**self.get_effective_config()["values"], key: normalized}
        self._validate_cross_key_rules(proposed)
        return normalized

    def write_config_history(
        self,
        key: str,
        old_value: Any,
        new_value: Any,
        user: str,
        reason: str,
        session: Session | None = None,
    ) -> None:
        db_session = session or self._get_write_session()[0]
        db_session.add(
            ConfigHistory(
                user=user,
                config_key=key,
                old_value={"value": old_value},
                new_value={"value": new_value},
                reason=reason,
                time=utc_now(),
            )
        )

    def _build_groups(
        self,
        values: dict[str, Any],
        sources: dict[str, str],
    ) -> list[dict[str, Any]]:
        categories: dict[str, list[dict[str, Any]]] = {}
        for spec in EDITABLE_CONFIG_SPECS:
            categories.setdefault(spec.category, []).append(
                self._item_payload(spec, values[spec.config_key], sources[spec.config_key])
            )
        return [
            {"category": category, "items": items}
            for category, items in categories.items()
        ]

    def _item_payload(
        self,
        spec: ConfigSpec,
        value: Any,
        source: str,
    ) -> dict[str, Any]:
        return {
            "config_key": spec.config_key,
            "current_value": value,
            "value_type": spec.value_type,
            "category": spec.category,
            "description": spec.description,
            "editable": spec.editable,
            "constraints": spec.constraints,
            "source": source,
        }

    def _resolve_values(
        self,
        overrides: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        values: dict[str, Any] = {}
        sources: dict[str, str] = {}
        for spec in EDITABLE_CONFIG_SPECS:
            if spec.config_key in overrides:
                values[spec.config_key] = overrides[spec.config_key]
                sources[spec.config_key] = "database"
            else:
                yaml_value, found = self._yaml_value(spec)
                values[spec.config_key] = yaml_value if found else spec.default
                sources[spec.config_key] = "config_file" if found else "default"
        values["paper_trading.real_trading_enabled"] = False
        sources["paper_trading.real_trading_enabled"] = "safety_default"
        return values, sources

    def _load_database_overrides(self) -> dict[str, Any]:
        session, should_close = self._get_read_session()
        if session is None:
            return {}

        try:
            rows = session.scalars(select(SystemConfig).where(SystemConfig.editable.is_(True))).all()
        except SQLAlchemyError:
            return {}
        finally:
            if should_close:
                session.close()

        overrides: dict[str, Any] = {}
        for row in rows:
            if row.config_key in EDITABLE_CONFIG_BY_KEY and not row.is_sensitive:
                overrides[row.config_key] = row.config_value
        return overrides

    def _get_read_session(self) -> tuple[Session | None, bool]:
        try:
            if self.session is not None:
                return self.session, False
            init_db()
            return get_session(), True
        except Exception:
            return None, False

    def _get_write_session(self) -> tuple[Session, bool]:
        try:
            if self.session is not None:
                return self.session, False
            init_db()
            return get_session(), True
        except Exception as exc:
            raise ConfigManagerError(
                code="CONFIG_DATABASE_UNAVAILABLE",
                message="Database is unavailable for config persistence",
                status_code=503,
            ) from exc

    def _get_config_record(self, session: Session, key: str) -> SystemConfig | None:
        return session.scalar(select(SystemConfig).where(SystemConfig.config_key == key))

    def _upsert_config_record(
        self,
        session: Session,
        record: SystemConfig | None,
        spec: ConfigSpec,
        value: Any,
        user: str,
    ) -> None:
        if record is None:
            record = SystemConfig(config_key=spec.config_key)
            session.add(record)

        record.config_value = value
        record.value_type = spec.value_type
        record.category = spec.category
        record.description = spec.description
        record.editable = True
        record.is_sensitive = False
        record.updated_by = user

    def _require_editable_key(self, key: str) -> ConfigSpec:
        normalized = str(key or "")
        lower_key = normalized.lower()
        sensitive = is_sensitive_key(normalized)
        forbidden = sensitive or any(part in lower_key for part in FORBIDDEN_KEY_PARTS)
        if forbidden or normalized not in EDITABLE_CONFIG_BY_KEY:
            safe_key = "[REDACTED]" if sensitive else normalized
            raise ConfigManagerError(
                code="CONFIG_KEY_NOT_EDITABLE",
                message=f"Config key is not editable: {safe_key}",
                status_code=400,
                data={"config_key": safe_key},
            )
        return EDITABLE_CONFIG_BY_KEY[normalized]

    def _normalize_and_validate(self, spec: ConfigSpec, value: Any) -> Any:
        normalized = self._normalize_value(spec, value)

        fixed = spec.constraints.get("fixed")
        if "fixed" in spec.constraints and normalized != fixed:
            raise ConfigManagerError(
                code="CONFIG_VALUE_INVALID",
                message=f"{spec.config_key} is fixed to {fixed!r} in Phase 14",
                data={"config_key": spec.config_key, "allowed_value": fixed},
            )

        allowed_values = spec.constraints.get("allowed_values")
        if allowed_values is not None and normalized not in allowed_values:
            raise ConfigManagerError(
                code="CONFIG_VALUE_INVALID",
                message=f"{spec.config_key} only allows: {allowed_values}",
                data={"config_key": spec.config_key, "allowed_values": allowed_values},
            )

        minimum = spec.constraints.get("min")
        maximum = spec.constraints.get("max")
        if minimum is not None and normalized < minimum:
            raise ConfigManagerError(
                code="CONFIG_VALUE_INVALID",
                message=f"{spec.config_key} must be >= {minimum}",
                data={"config_key": spec.config_key, "min": minimum},
            )
        if maximum is not None and normalized > maximum:
            raise ConfigManagerError(
                code="CONFIG_VALUE_INVALID",
                message=f"{spec.config_key} must be <= {maximum}",
                data={"config_key": spec.config_key, "max": maximum},
            )
        return normalized

    def _normalize_value(self, spec: ConfigSpec, value: Any) -> Any:
        try:
            if spec.value_type == "boolean":
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    normalized = value.strip().lower()
                    if normalized in {"true", "1", "yes", "on"}:
                        return True
                    if normalized in {"false", "0", "no", "off"}:
                        return False
                raise ValueError

            if spec.value_type == "integer":
                if isinstance(value, bool):
                    raise ValueError
                if isinstance(value, int):
                    return value
                if isinstance(value, float) and value.is_integer():
                    return int(value)
                if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                    return int(value)
                raise ValueError

            if spec.value_type == "number":
                if isinstance(value, bool):
                    raise ValueError
                if isinstance(value, int | float):
                    return float(value)
                if isinstance(value, str):
                    return float(value.strip())
                raise ValueError

            if spec.value_type == "string":
                if isinstance(value, str):
                    return value
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ConfigManagerError(
                code="CONFIG_VALUE_INVALID",
                message=f"{spec.config_key} expects {spec.value_type}",
                data={"config_key": spec.config_key, "value_type": spec.value_type},
            ) from exc

        raise ConfigManagerError(
            code="CONFIG_VALUE_INVALID",
            message=f"Unsupported config value type: {spec.value_type}",
            data={"config_key": spec.config_key},
        )

    def _validate_cross_key_rules(self, proposed_values: dict[str, Any]) -> None:
        total = sum(float(proposed_values[key]) for key in QUANT_WEIGHT_KEYS)
        if abs(total - 1.0) > 0.0001:
            raise ConfigManagerError(
                code="CONFIG_VALUE_INVALID",
                message="Quant factor weights must sum to 1",
                data={"quant_weight_sum": total},
            )

        if proposed_values.get("paper_trading.real_trading_enabled") is not False:
            raise ConfigManagerError(
                code="CONFIG_VALUE_INVALID",
                message="real_trading_enabled must remain false",
                data={"real_trading_enabled": False},
            )

    def _yaml_or_default(self, spec: ConfigSpec) -> Any:
        value, found = self._yaml_value(spec)
        return value if found else spec.default

    def _yaml_value(self, spec: ConfigSpec) -> tuple[Any, bool]:
        current: Any = self.app_config.config_files.get(spec.yaml_file, {})
        for part in spec.yaml_path:
            if not isinstance(current, dict) or part not in current:
                return None, False
            current = current[part]
        return current, True


def _unwrap_history_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"value"}:
        return value["value"]
    return value
