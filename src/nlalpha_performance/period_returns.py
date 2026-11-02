"""Track observed endpoint returns by an explicit local Gregorian analysis calendar."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from nlalpha.core.contracts.time import get_timezone, period_boundary_ns, timezone_metadata
from nlalpha.core.telemetry import get_meter, start_span

from .basic_statistics import _error, _finite_number


PERIOD_RETURN_COLUMNS = [
    "period", "calendar_start_timestamp_ns", "calendar_end_timestamp_ns", "reference_equity",
    "reference_timestamp_ns", "reference_is_initial", "first_observation_timestamp_ns",
    "last_observation_timestamp_ns", "first_observation_index", "last_observation_index",
    "observation_count", "end_equity", "net_pnl", "net_return", "unavailable", "is_first_period",
    "is_last_period", "closed_by_next_period", "cross_period_interval_count", "skipped_periods_before",
    "non_positive_equity_observation_count",
]
_FREQUENCIES = ("monthly", "yearly")
_COUNT_FIELDS = (
    "observed_period_count", "valid_period_count", "unavailable_period_count", "positive_period_count",
    "negative_period_count", "flat_period_count", "skipped_period_count", "cross_period_interval_count",
    "intervals_spanning_skipped_periods",
)
_SUMMARY_FIELDS = {*_COUNT_FIELDS, "positive_period_fraction", "best", "worst", "current"}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_NS_PER_SECOND = 1_000_000_000
_I64_MIN = -(2 ** 63)
_I64_MAX = 2 ** 63 - 1
_evidence_counter = get_meter().create_counter(
    "nlalpha.empyrical.period_return_evidence", description="Observed period return evidence envelopes built"
)


def _integer(value: object, field: str, *, nullable: bool = False) -> int | None:
    """Validate exact integer counts or metadata timestamps."""
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(field, f"{field} must be an integer")
    return value


def _observation_timestamp(value: object, field: str = "timestamp_ns", *, nullable: bool = False) -> int | None:
    """Validate a signed int64 nanosecond observation timestamp."""
    timestamp = _integer(value, field, nullable=nullable)
    if timestamp is not None and not _I64_MIN <= timestamp <= _I64_MAX:
        raise _error(field, f"{field} must fit signed int64 nanoseconds")
    return timestamp


def _calendar_bucket(frequency: str, year: int, month: int, analysis_timezone: str) -> tuple[str, int, int, int]:
    """Return a local period label, ordinal and exact UTC nanosecond boundaries."""
    if frequency == "monthly":
        start = period_boundary_ns(year, month, analysis_timezone)
        end = period_boundary_ns(year + int(month == 12), 1 if month == 12 else month + 1, analysis_timezone)
        return f"{year:04d}-{month:02d}", year * 12 + month - 1, start, end
    start = period_boundary_ns(year, 1, analysis_timezone)
    end = period_boundary_ns(year + 1, 1, analysis_timezone)
    return f"{year:04d}", year, start, end


def _copy_row(row: dict | None) -> dict | None:
    """Copy one bounded candidate row and its unavailable reasons."""
    return None if row is None else {**row, "unavailable": dict(row["unavailable"])}


@dataclass
class _Bucket:
    """Maintain one observed calendar bucket's equity endpoints and sample coverage."""

    period: str
    ordinal: int
    calendar_start_timestamp_ns: int
    calendar_end_timestamp_ns: int
    reference_equity: float
    reference_timestamp_ns: int | None
    first_observation_timestamp_ns: int
    first_observation_index: int
    last_observation_timestamp_ns: int
    last_observation_index: int
    end_equity: float
    skipped_periods_before: int
    non_positive_equity_observation_count: int

    def row(self, *, is_last: bool) -> dict:
        """Calculate endpoint statistics when closing or reading this bucket."""
        initial = self.reference_timestamp_ns is None
        unavailable = {}
        net_pnl = self.end_equity - self.reference_equity
        if not math.isfinite(net_pnl):
            net_pnl = None
            unavailable["net_pnl"] = "numerical_overflow"
        net_return = None
        if self.reference_equity <= 0:
            unavailable["net_return"] = "non_positive_reference_equity"
        else:
            net_return = self.end_equity / self.reference_equity - 1.0
            if not math.isfinite(net_return):
                net_return = None
                unavailable["net_return"] = "numerical_overflow"
        return {
            "period": self.period,
            "calendar_start_timestamp_ns": self.calendar_start_timestamp_ns,
            "calendar_end_timestamp_ns": self.calendar_end_timestamp_ns,
            "reference_equity": self.reference_equity,
            "reference_timestamp_ns": self.reference_timestamp_ns,
            "reference_is_initial": initial,
            "first_observation_timestamp_ns": self.first_observation_timestamp_ns,
            "last_observation_timestamp_ns": self.last_observation_timestamp_ns,
            "first_observation_index": self.first_observation_index,
            "last_observation_index": self.last_observation_index,
            "observation_count": self.last_observation_index - self.first_observation_index + 1,
            "end_equity": self.end_equity,
            "net_pnl": net_pnl,
            "net_return": net_return,
            "unavailable": unavailable,
            "is_first_period": initial,
            "is_last_period": is_last,
            "closed_by_next_period": not is_last,
            "cross_period_interval_count": int(not initial),
            "skipped_periods_before": self.skipped_periods_before,
            "non_positive_equity_observation_count": self.non_positive_equity_observation_count,
        }


def _include_row(summary: dict, row: dict) -> None:
    """Include one observed bucket in the bounded counts and valid-return candidates."""
    summary["observed_period_count"] += 1
    summary["skipped_period_count"] += row["skipped_periods_before"]
    summary["cross_period_interval_count"] += row["cross_period_interval_count"]
    summary["intervals_spanning_skipped_periods"] += int(row["skipped_periods_before"] > 0)
    value = row["net_return"]
    if value is None:
        summary["unavailable_period_count"] += 1
        return
    summary["valid_period_count"] += 1
    sign = "positive" if value > 0 else ("negative" if value < 0 else "flat")
    summary[f"{sign}_period_count"] += 1
    if summary["best"] is None or value > summary["best"]["net_return"]:
        summary["best"] = row
    if summary["worst"] is None or value < summary["worst"]["net_return"]:
        summary["worst"] = row


class _FrequencyTracker:
    """Keep closed-period aggregates and the current period for one frequency."""

    def __init__(self, frequency: str, record_history: bool, analysis_timezone: str):
        self._frequency = frequency
        self._analysis_timezone = analysis_timezone
        self._current = None
        self._closed = {field: 0 for field in _COUNT_FIELDS}
        self._closed.update({"best": None, "worst": None})
        self._history = [] if record_history else None

    def observe(self, year: int, month: int, timestamp: int, equity: float, index: int, reference_equity: float, reference_timestamp: int | None) -> None:
        """Update endpoints or close an observed bucket when its local period changes."""
        ordinal = year * 12 + month - 1 if self._frequency == "monthly" else year
        if self._current is not None and ordinal == self._current.ordinal:
            self._current.last_observation_timestamp_ns = timestamp
            self._current.last_observation_index = index
            self._current.end_equity = equity
            self._current.non_positive_equity_observation_count += int(equity <= 0)
            return
        period, ordinal, start, end = _calendar_bucket(self._frequency, year, month, self._analysis_timezone)
        skipped = 0
        if self._current is not None:
            skipped = ordinal - self._current.ordinal - 1
            row = self._current.row(is_last=False)
            _include_row(self._closed, row)
            if self._history is not None:
                self._history.append(row)
        self._current = _Bucket(
            period, ordinal, start, end, reference_equity, reference_timestamp, timestamp, index,
            timestamp, index, equity, skipped, int(equity <= 0),
        )

    def finish(self) -> tuple[dict, list[dict]]:
        """Return independent aggregates and history including the current bucket."""
        summary = dict(self._closed)
        current = self._current.row(is_last=True) if self._current is not None else None
        if current is not None:
            _include_row(summary, current)
        valid_count = summary["valid_period_count"]
        summary["positive_period_fraction"] = summary["positive_period_count"] / valid_count if valid_count else None
        summary["best"] = _copy_row(summary["best"])
        summary["worst"] = _copy_row(summary["worst"])
        summary["current"] = _copy_row(current)
        rows = []
        if self._history is not None:
            rows = [_copy_row(row) for row in self._history]
            if current is not None:
                rows.append(current)
        return summary, rows


class PeriodReturnsTracker:
    """Track month and year endpoint returns with bounded state and optional history."""

    def __init__(self, initial_equity: float, *, record_history: bool = True, analysis_timezone: str = "UTC"):
        self._analysis_zone = get_timezone(analysis_timezone)
        self._previous_equity = _finite_number(initial_equity, "initial_equity")
        if self._previous_equity <= 0:
            raise _error("initial_equity", "initial_equity must be greater than zero")
        if not isinstance(record_history, bool):
            raise _error("record_history", "record_history must be a boolean")
        self._previous_timestamp_ns = None
        self._observation_count = 0
        self._calendar_year = None
        self._calendar_month = None
        self._trackers = {frequency: _FrequencyTracker(frequency, record_history, analysis_timezone) for frequency in _FREQUENCIES}

    def observe(self, timestamp_ns: int, equity: float) -> None:
        """Assign an ordered UTC equity observation to its ending local calendar periods."""
        timestamp = _observation_timestamp(timestamp_ns)
        if self._previous_timestamp_ns is not None and timestamp <= self._previous_timestamp_ns:
            raise _error("timestamp_ns", "observation timestamps must be strictly increasing")
        equity = _finite_number(equity, "equity")
        current_month = self._trackers["monthly"]._current
        if current_month is None or timestamp >= current_month.calendar_end_timestamp_ns:
            seconds, _ = divmod(timestamp, _NS_PER_SECOND)
            date = (_EPOCH + timedelta(seconds=seconds)).astimezone(self._analysis_zone)
            self._calendar_year, self._calendar_month = date.year, date.month
        index = self._observation_count + 1
        for tracker in self._trackers.values():
            tracker.observe(self._calendar_year, self._calendar_month, timestamp, equity, index, self._previous_equity, self._previous_timestamp_ns)
        self._previous_equity = equity
        self._previous_timestamp_ns = timestamp
        self._observation_count = index

    def finish(self) -> tuple[dict, dict]:
        """Return independent monthly and yearly evidence without mutating tracker state."""
        summaries = {}
        rows = {}
        for frequency, tracker in self._trackers.items():
            summaries[frequency], rows[frequency] = tracker.finish()
        return summaries, rows


def _validate_summary(summary: object, frequency: str, observation_count: int) -> dict:
    """Validate one bounded summary and its candidate fields."""
    if not isinstance(summary, dict) or set(summary) != _SUMMARY_FIELDS:
        raise _error(frequency, "period return summary must contain its declared fields")
    for field in _COUNT_FIELDS:
        if _integer(summary[field], field) < 0:
            raise _error(field, f"{field} must be non-negative")
    observed = summary["observed_period_count"]
    valid = summary["valid_period_count"]
    if observed != valid + summary["unavailable_period_count"] or valid != sum(summary[f"{sign}_period_count"] for sign in ("positive", "negative", "flat")):
        raise _error(frequency, "period return counts are inconsistent")
    if observed > observation_count or bool(observed) != bool(observation_count):
        raise _error(frequency, "period counts must agree with the observed sample")
    if summary["cross_period_interval_count"] != max(0, observed - 1) or summary["intervals_spanning_skipped_periods"] > summary["cross_period_interval_count"]:
        raise _error(frequency, "period transition counts are inconsistent")
    skipped = summary["skipped_period_count"]
    spanning = summary["intervals_spanning_skipped_periods"]
    if spanning > skipped or bool(spanning) != bool(skipped):
        raise _error(frequency, "skipped period counts must agree with their spanning intervals")
    fraction = summary["positive_period_fraction"]
    if valid:
        if _finite_number(fraction, "positive_period_fraction") != summary["positive_period_count"] / valid:
            raise _error(frequency, "positive_period_fraction must use valid observed periods")
    elif fraction is not None:
        raise _error(frequency, "positive_period_fraction must be null without valid periods")
    for field in ("best", "worst", "current"):
        row = summary[field]
        expected = bool(observed) if field == "current" else bool(valid)
        if expected != (row is not None) or (row is not None and (not isinstance(row, dict) or set(row) != set(PERIOD_RETURN_COLUMNS))):
            raise _error(field, "period candidates must agree with counts and declared row fields")
        if field != "current" and row is not None:
            _finite_number(row["net_return"], f"{field}.net_return")
    return summary


def build_period_return_evidence(
    *,
    summaries: dict,
    rows: dict,
    record_mode: str,
    source: str,
    schema_prefix: str,
    observation_count: int,
    first_observation_timestamp_ns: int | None,
    last_observation_timestamp_ns: int | None,
    initial_reference_timestamp_ns: int | None,
    analysis_timezone: str = "UTC",
) -> tuple[dict, dict]:
    """Build month and year report envelopes with explicit sample and attribution metadata."""
    timezone_database = timezone_metadata(analysis_timezone)
    if not isinstance(summaries, dict) or set(summaries) != set(_FREQUENCIES) or not isinstance(rows, dict) or set(rows) != set(_FREQUENCIES):
        raise _error("period_returns", "period summaries and rows must contain monthly and yearly")
    if not isinstance(record_mode, str) or record_mode not in {"full", "summary"}:
        raise _error("record_mode", "record_mode must be full or summary")
    for field, value in (("source", source), ("schema_prefix", schema_prefix)):
        if not isinstance(value, str) or not value or value != value.strip():
            raise _error(field, f"{field} must be a non-empty trimmed string")
    count = _integer(observation_count, "observation_count")
    first = _observation_timestamp(first_observation_timestamp_ns, "first_observation_timestamp_ns", nullable=True)
    last = _observation_timestamp(last_observation_timestamp_ns, "last_observation_timestamp_ns", nullable=True)
    reference = _integer(initial_reference_timestamp_ns, "initial_reference_timestamp_ns", nullable=True)
    if count < 0:
        raise _error("observation_count", "observation_count must be non-negative")
    if count == 0 and (first is not None or last is not None):
        raise _error("observation_count", "empty observations require null observation timestamps")
    if count > 0 and (first is None or last is None or first > last or (count == 1 and first != last) or (count > 1 and first == last)):
        raise _error("observation_timestamps", "observation count must agree with ordered timestamp bounds")
    unavailable = {}
    for frequency in _FREQUENCIES:
        summary = _validate_summary(summaries[frequency], frequency, count)
        if not isinstance(rows[frequency], list) or (record_mode == "summary" and rows[frequency]) or (record_mode == "full" and len(rows[frequency]) != summary["observed_period_count"]):
            raise _error(frequency, "stored period row count must match its recording mode and summary")
        if not summary["valid_period_count"]:
            reason = "no_equity_observations" if count == 0 else "no_valid_period_returns"
            unavailable[frequency] = {key: reason for key in ("positive_period_fraction", "best", "worst")}
            if count == 0:
                unavailable[frequency]["current"] = reason
    with start_span("empyrical.build_period_return_evidence", attributes={"observation_count": count}):
        diagnostics = {
            "calendar": "local_Gregorian",
            "analysis_timezone": analysis_timezone,
            "timezone_database": timezone_database,
            "calendar_midnight_policy": "reject_ambiguous_or_nonexistent_local_calendar_midnight",
            "timestamp_unit": "ns",
            "timestamp_timezone": "UTC",
            "timestamp_columns": [field for field in PERIOD_RETURN_COLUMNS if field.endswith("timestamp_ns")],
            "calendar_bounds": "inclusive_local_period_start_exclusive_next_local_period_start_as_exact_UTC_integer_ns",
            "sampling": "completed_bar_close_net_equity",
            "source": source,
            "return_unit": "proportion",
            "net_return_definition": "end_equity/reference_equity-1",
            "net_pnl_definition": "end_equity-reference_equity",
            "return_assignment": "calendar_period_of_ending_observation",
            "reference_equity_rule": "initial_capital_then_immediately_preceding_real_observation",
            "initial_reference_timestamp_role": "informational_only_not_used_for_bucketing_or_economic_interval_start",
            "fee_treatment": "already_reflected_in_net_equity",
            "yearly_return_basis": "independent_year_equity_endpoints_not_monthly_sums_or_annualized_return",
            "missing_period_policy": "omit_unobserved_periods_without_zero_rows_or_forward_fill",
            "boundary_interval_attribution": "assign_cross_period_interval_to_ending_observation_bucket",
            "gap_attribution": "intervals_spanning_skipped_periods_are_assigned_to_ending_bucket",
            "sample_coverage": "sample_flags_and_closed_by_next_period_do_not_prove_complete_calendar_or_trading_coverage",
            "positive_period_fraction_denominator": "valid_observed_buckets_including_first_and_current_partial_sample_buckets",
            "best_worst_population": "all_valid_observed_buckets_including_current",
            "best_worst_tie_rule": "earlier_local_calendar_period",
            "non_positive_equity_policy": "preserve_observations_and_endpoint_returns_with_positive_reference_without_compounding",
            "nullable_field_reasons": {
                "reference_timestamp_ns": "initial_capital_has_no_economic_timestamp",
                "net_return": "row_unavailable_reason_non_positive_reference_equity_or_numerical_overflow",
                "net_pnl": "row_unavailable_reason_numerical_overflow",
                "initial_reference_timestamp_ns": "reference_timestamp_not_provided",
                "first_observation_timestamp_ns": "no_equity_observations",
                "last_observation_timestamp_ns": "no_equity_observations",
            },
            "observation_count": count,
            "first_observation_timestamp_ns": first,
            "last_observation_timestamp_ns": last,
            "initial_reference_timestamp_ns": reference,
            "record_mode": record_mode,
            "unavailable": unavailable,
        }
        reports = {}
        for frequency in _FREQUENCIES:
            name = f"{frequency}_returns"
            status = "unavailable" if count == 0 else ("available" if record_mode == "full" else "not_recorded")
            reason = "no_equity_observations" if count == 0 else (None if record_mode == "full" else "record_mode_summary")
            frequency_counts = {field: summaries[frequency][field] for field in (*_COUNT_FIELDS, "positive_period_fraction")}
            reports[name] = {
                "schema_id": f"{schema_prefix}.{name}.v1",
                "source": source,
                "status": status,
                "reason": reason,
                "row_count": None if count == 0 else summaries[frequency]["observed_period_count"],
                "columns": list(PERIOD_RETURN_COLUMNS),
                "rows": rows[frequency],
                "metadata": {**diagnostics, "frequency": frequency, "summary_counts": frequency_counts},
            }
        _evidence_counter.add(1, {"source": source, "record_mode": record_mode})
        return reports, diagnostics
