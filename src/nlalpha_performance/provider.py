"""Provide empyrical metrics and basic statistics from net equity moments."""

import logging
import math
import time
from typing import Sequence

import empyrical
import numpy as np
import pandas as pd

from nlalpha.core.capabilities.manifest import ProviderManifest
from nlalpha.core.telemetry import get_meter, start_span

from .basic_statistics import (
    _error,
    _finite_number,
    _positive_annualization,
    calculate_basic_statistics,
)

logger = logging.getLogger(__name__)

_meter = get_meter()
_call_counter = _meter.create_counter("nlalpha.empyrical.calls")
_latency_histogram = _meter.create_histogram("nlalpha.empyrical.latency_ms")


def _validated_returns(values: Sequence[float]) -> list[float]:
    """Validate every periodic return as a finite real number."""
    try:
        return [_finite_number(value, "returns") for value in values]
    except TypeError as exc:
        raise _error("returns", "returns must be a sequence of finite numbers") from exc


def _finite_metrics(metrics: dict, *, empty: bool = False) -> dict:
    """Expose unavailable metric reasons instead of non-finite numeric results."""
    result = {}
    unavailable = {}
    for metric, value in metrics.items():
        if value is None or not math.isfinite(value):
            result[metric] = None
            unavailable[metric] = "insufficient_observations" if empty else "non_finite_result"
        else:
            result[metric] = value
    result["unavailable"] = unavailable
    return result


class PerformanceProvider:
    """Provide performance analytics through empyrical and equity return moments."""

    def __init__(self):
        self._version = getattr(empyrical, "__version__", None)

    def manifest(self) -> ProviderManifest:
        """Return the provider methods and calculation conventions."""
        return ProviderManifest(
            provider_id="performance",
            provider_name="PerformanceProvider",
            provider_version=self._version,

            available=True,
            domains=["performance"],
            methods=[
                "calculate_performance", "calculate_risk", "calculate_benchmark_relative",
                "max_drawdown", "calmar_ratio", "calculate_basic_statistics",
            ],
            conventions={
                "annualization": "periods_per_year",
                "risk_free_rate": 0.0,
                "returns_input": "simple_returns",
                "basic_statistics_annualization": "explicit_observations_per_year",
                "basic_statistics_risk_free_rate": "annual_rate_converted_to_period_compound_rate",
                "basic_statistics_returns_basis": "net_account_equity_simple_returns",
                "basic_statistics_variance_ddof": 1,
                "unavailable_metrics": "null_with_reason",
                "note": "max_drawdown and calmar use empyrical native functions on returns series",
            },
        )

    def _to_series(self, values: Sequence[float]):
        """Convert periodic returns into an empyrical-compatible series."""
        return pd.Series(values)

    def calculate_performance(self, returns: Sequence[float], *, annualization: int | None = None) -> dict:
        """Calculate return metrics and disclose unavailable values."""
        t0 = time.time()
        with start_span("empyrical.calculate_performance") as span:
            returns = _validated_returns(returns)
            if annualization is not None:
                _positive_annualization(annualization)
            if len(returns) == 0:
                result = _finite_metrics({"total_return": None, "annual_return": None}, empty=True)
            else:
                s = self._to_series(returns)
                total_return = float(empyrical.cum_returns_final(s))
                annual_return = float(empyrical.annual_return(s, annualization=annualization))
                result = _finite_metrics({"total_return": total_return, "annual_return": annual_return})
            latency_ms = (time.time() - t0) * 1000
            _call_counter.add(1)
            _latency_histogram.record(latency_ms)
            logger.info("empyrical.calculate_performance | latency_ms=%.1f", latency_ms)
            return result

    def calculate_risk(self, returns: Sequence[float], *, annualization: int | None = None) -> dict:
        """Calculate risk metrics and disclose unavailable values."""
        t0 = time.time()
        with start_span("empyrical.calculate_risk") as span:
            returns = _validated_returns(returns)
            if annualization is not None:
                _positive_annualization(annualization)
            if len(returns) == 0:
                result = _finite_metrics({
                    "annual_volatility": None,
                    "downside_volatility": None,
                    "sharpe_ratio": None,
                    "sortino_ratio": None,
                    "omega_ratio": None,
                    "max_drawdown": None,
                    "calmar_ratio": None,
                }, empty=True)
            else:
                s = self._to_series(returns)
                annual_vol = float(empyrical.annual_volatility(s, annualization=annualization))
                sharpe = float(empyrical.sharpe_ratio(s, annualization=annualization))
                sortino = float(empyrical.sortino_ratio(s, annualization=annualization))
                downside_vol = float(
                    empyrical.downside_risk(s, required_return=0, annualization=annualization)
                )
                omega_kwargs = {"annualization": annualization} if annualization is not None else {}
                omega = float(empyrical.omega_ratio(s, required_return=0, **omega_kwargs))
                mdd = float(empyrical.max_drawdown(s))
                calmar = float(empyrical.calmar_ratio(s, annualization=annualization))

                result = _finite_metrics({
                    "annual_volatility": annual_vol,
                    "downside_volatility": downside_vol,
                    "sharpe_ratio": sharpe,
                    "sortino_ratio": sortino,
                    "omega_ratio": omega,
                    "max_drawdown": mdd,
                    "calmar_ratio": calmar,
                })
            latency_ms = (time.time() - t0) * 1000
            _call_counter.add(1)
            _latency_histogram.record(latency_ms)
            logger.info("empyrical.calculate_risk | latency_ms=%.1f", latency_ms)
            return result

    def calculate_basic_statistics(
        self,
        *,
        initial_equity: float,
        final_equity: float,
        return_count: int,
        return_mean: float,
        return_m2: float,
        settings: dict,
        invalid_reason: str | None = None,
    ) -> dict:
        """Calculate annual return, volatility, and Sharpe from equity return moments."""
        t0 = time.time()
        with start_span("empyrical.calculate_basic_statistics"):
            result = calculate_basic_statistics(
                initial_equity=initial_equity,
                final_equity=final_equity,
                return_count=return_count,
                return_mean=return_mean,
                return_m2=return_m2,
                settings=settings,
                invalid_reason=invalid_reason,
            )
            latency_ms = (time.time() - t0) * 1000
            _call_counter.add(1)
            _latency_histogram.record(latency_ms)
            logger.info("empyrical.calculate_basic_statistics | latency_ms=%.1f", latency_ms)
            return result

    def max_drawdown(self, returns: Sequence[float]) -> float:
        """Calculate maximum drawdown through empyrical."""
        t0 = time.time()
        with start_span("empyrical.max_drawdown") as span:
            if not returns:
                result = 0.0
            else:
                result = float(empyrical.max_drawdown(self._to_series(returns)))
            latency_ms = (time.time() - t0) * 1000
            _call_counter.add(1)
            _latency_histogram.record(latency_ms)
            logger.info("empyrical.max_drawdown | latency_ms=%.1f", latency_ms)
            return result

    def calmar_ratio(self, returns: Sequence[float], *, annualization: int | None = None) -> float:
        """Calculate the Calmar ratio through empyrical."""
        t0 = time.time()
        with start_span("empyrical.calmar_ratio") as span:
            if not returns:
                result = 0.0
            else:
                result = float(empyrical.calmar_ratio(self._to_series(returns), annualization=annualization))
            latency_ms = (time.time() - t0) * 1000
            _call_counter.add(1)
            _latency_histogram.record(latency_ms)
            logger.info("empyrical.calmar_ratio | latency_ms=%.1f", latency_ms)
            return result

    def calculate_benchmark_relative(
        self,
        returns: Sequence[float],
        benchmark_returns: Sequence[float],
        *,
        annualization: int | None = None,
    ) -> dict:
        """Calculate benchmark-relative metrics through empyrical."""
        t0 = time.time()
        with start_span("empyrical.calculate_benchmark_relative") as span:
            if not returns or not benchmark_returns:
                result = {
                    "benchmark_return": None,
                    "excess_return": None,
                    "tracking_error": None,
                    "information_ratio": None,
                    "alpha": None,
                    "beta": None,
                }
            else:
                s = self._to_series(returns)
                b = self._to_series(benchmark_returns)
                alpha_val = float(empyrical.alpha(s, b, annualization=annualization))
                beta_val = float(empyrical.beta(s, b))

                # tracking_error 使用 empyrical 风格：超额收益的标准差年化
                min_len = min(len(returns), len(benchmark_returns))
                r_arr = np.array(returns[:min_len], dtype=float)
                b_arr = np.array(benchmark_returns[:min_len], dtype=float)
                excess = r_arr - b_arr
                tracking_err = float(np.std(excess, ddof=1) * np.sqrt(annualization)) if annualization else 0.0
                avg_excess = float(np.mean(excess) * annualization) if annualization else 0.0
                info_ratio = avg_excess / tracking_err if tracking_err > 0 else 0.0
                benchmark_ret = float(np.mean(b_arr) * annualization) if annualization else 0.0

                result = {
                    "benchmark_return": benchmark_ret,
                    "excess_return": avg_excess,
                    "tracking_error": tracking_err,
                    "information_ratio": info_ratio,
                    "alpha": alpha_val,
                    "beta": beta_val,
                }
            latency_ms = (time.time() - t0) * 1000
            _call_counter.add(1)
            _latency_histogram.record(latency_ms)
            logger.info("empyrical.calculate_benchmark_relative | latency_ms=%.1f", latency_ms)
            return result
