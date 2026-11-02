"""Calculate basic annualized statistics from net account equity moments."""

import math
from numbers import Real

from nlalpha.core.errors import StrategyRuntimeError


def _error(field: str, message: str) -> StrategyRuntimeError:
    """Create a statistics input error."""
    return StrategyRuntimeError(
        message, details={"reason": "invalid_statistics_input", "field": field}
    )


def _finite_number(value: object, field: str) -> float:
    """Validate a finite real number."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _error(field, f"{field} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise _error(field, f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise _error(field, f"{field} must be a finite number")
    return number


def _positive_annualization(value: object) -> int:
    """Validate an explicit observations-per-year count."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _error("annualization", "annualization must be a positive integer")
    return value


def _risk_free_rate(value: object) -> float:
    """Validate an annual risk-free rate."""
    rate = _finite_number(value, "risk_free_rate")
    if rate <= -1.0:
        raise _error("risk_free_rate", "risk_free_rate must be greater than -1")
    return rate


def validate_statistics_settings(execution: dict) -> dict:
    """Validate explicit annualization and annual risk-free execution settings."""
    if not isinstance(execution, dict):
        raise _error("execution", "execution must be a dictionary")
    annualization = None
    if "annualization" in execution:
        annualization = _positive_annualization(execution["annualization"])
    configured_rate = "risk_free_rate" in execution
    return {
        "annualization": annualization,
        "annualization_source": "configured" if annualization is not None else "not_provided",
        "risk_free_rate": _risk_free_rate(execution["risk_free_rate"]) if configured_rate else 0.0,
        "risk_free_rate_source": "configured" if configured_rate else "engine_default",
    }


def _validated_settings(settings: dict) -> dict:
    """Validate normalized settings and their source labels."""
    if not isinstance(settings, dict):
        raise _error("settings", "settings must be a dictionary")
    for field in ("annualization", "annualization_source", "risk_free_rate", "risk_free_rate_source"):
        if field not in settings:
            raise _error(field, f"normalized settings must include {field}")
    annualization = settings["annualization"]
    if annualization is not None:
        annualization = _positive_annualization(annualization)
    annualization_source = settings["annualization_source"]
    expected_source = "configured" if annualization is not None else "not_provided"
    if annualization_source != expected_source:
        raise _error("annualization_source", "annualization_source must match annualization")
    rate = _risk_free_rate(settings["risk_free_rate"])
    rate_source = settings["risk_free_rate_source"]
    if rate_source not in ("configured", "engine_default"):
        raise _error("risk_free_rate_source", "risk_free_rate_source is invalid")
    if rate_source == "engine_default" and rate != 0.0:
        raise _error("risk_free_rate_source", "the engine default risk-free rate must be zero")
    return {
        "annualization": annualization,
        "annualization_source": annualization_source,
        "risk_free_rate": rate,
        "risk_free_rate_source": rate_source,
    }


class ReturnMoments:
    """Accumulate simple return moments from successive net equity observations."""

    def __init__(self, initial_equity: float):
        self._previous_equity = _finite_number(initial_equity, "initial_equity")
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.invalid_reason = "non_positive_equity" if self._previous_equity <= 0 else None

    def observe(self, equity: float) -> None:
        """Observe one account equity and retain only finite return moments."""
        current_equity = _finite_number(equity, "equity")
        if self.invalid_reason is not None:
            return
        if current_equity <= 0:
            self.invalid_reason = "non_positive_equity"
            return
        period_return = current_equity / self._previous_equity - 1.0
        count = self.count + 1
        delta = period_return - self.mean
        mean = self.mean + delta / count
        m2 = self.m2 + delta * (period_return - mean)
        if not all(math.isfinite(value) for value in (period_return, mean, m2)):
            self.invalid_reason = "numerical_overflow"
            return
        if m2 < 0:
            raise _error("return_m2", "return_m2 must be non-negative")
        self.count = count
        self.mean = mean
        self.m2 = m2
        self._previous_equity = current_equity


def calculate_basic_statistics(
    *,
    initial_equity: float,
    final_equity: float,
    return_count: int,
    return_mean: float,
    return_m2: float,
    settings: dict,
    invalid_reason: str | None = None,
) -> dict:
    """Calculate observation-based annual return, sample annual volatility, and Sharpe."""
    initial = _finite_number(initial_equity, "initial_equity")
    final = _finite_number(final_equity, "final_equity")
    mean = _finite_number(return_mean, "return_mean")
    m2 = _finite_number(return_m2, "return_m2")
    if isinstance(return_count, bool) or not isinstance(return_count, int) or return_count < 0:
        raise _error("return_count", "return_count must be a non-negative integer")
    if m2 < 0:
        raise _error("return_m2", "return_m2 must be non-negative")
    if invalid_reason is not None and (not isinstance(invalid_reason, str) or not invalid_reason):
        raise _error("invalid_reason", "invalid_reason must be a non-empty string or None")
    normalized = _validated_settings(settings)
    annualization = normalized["annualization"]
    rate = normalized["risk_free_rate"]
    period_rate = None
    if annualization is not None:
        try:
            period_rate = math.expm1(math.log1p(rate) / annualization)
        except OverflowError:
            pass
    values = {"annual_return": None, "annual_volatility": None, "sharpe_ratio": None}
    unavailable = {}
    diagnostics = {
        "return_count": return_count,
        "annualization": annualization,
        "annualization_source": normalized["annualization_source"],
        "annual_risk_free_rate": rate,
        "risk_free_rate_source": normalized["risk_free_rate_source"],
        "period_risk_free_rate": period_rate,
        "variance_ddof": 1,
        "returns_basis": "net_account_equity_simple_returns",
        "annualization_basis": "observations_per_year_no_calendar_resampling",
        "unavailable": unavailable,
    }
    reason = invalid_reason
    if reason is None and (initial <= 0 or final <= 0):
        reason = "non_positive_equity"
    if reason is None and annualization is None:
        reason = "annualization_not_provided"
    if reason is None and return_count == 0:
        reason = "insufficient_observations"
    if reason is not None:
        unavailable.update({metric: reason for metric in values})
    else:
        try:
            annual_return = math.expm1(
                (math.log(final) - math.log(initial)) * annualization / return_count
            )
            if math.isfinite(annual_return):
                values["annual_return"] = annual_return
            else:
                unavailable["annual_return"] = "numerical_overflow"
        except OverflowError:
            unavailable["annual_return"] = "numerical_overflow"
        if return_count < 2:
            unavailable.update({
                metric: "insufficient_observations"
                for metric in ("annual_volatility", "sharpe_ratio")
            })
        elif m2 == 0:
            values["annual_volatility"] = 0.0
            unavailable["sharpe_ratio"] = "zero_volatility"
        else:
            try:
                deviation = math.sqrt(m2 / (return_count - 1))
                annual_factor = math.sqrt(annualization)
                volatility = deviation * annual_factor
                if math.isfinite(volatility):
                    values["annual_volatility"] = volatility
                else:
                    unavailable["annual_volatility"] = "numerical_overflow"
                if deviation == 0:
                    unavailable["sharpe_ratio"] = "zero_volatility"
                elif period_rate is None:
                    unavailable["sharpe_ratio"] = "numerical_overflow"
                else:
                    sharpe = (mean - period_rate) / deviation * annual_factor
                    if math.isfinite(sharpe):
                        values["sharpe_ratio"] = sharpe
                    else:
                        unavailable["sharpe_ratio"] = "numerical_overflow"
            except OverflowError:
                unavailable.update({
                    metric: "numerical_overflow"
                    for metric in ("annual_volatility", "sharpe_ratio")
                })
    return {
        "performance": {"annual_return": values["annual_return"]},
        "risk": {metric: values[metric] for metric in ("annual_volatility", "sharpe_ratio")},
        "diagnostics": diagnostics,
    }
