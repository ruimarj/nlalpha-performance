"""Track drawdown episodes from completed-close net account equity observations."""

from __future__ import annotations

import math
from dataclasses import dataclass

from nlalpha.core.telemetry import get_meter, start_span

from .basic_statistics import _error, _finite_number


DRAWDOWN_COLUMNS = [
    "episode_id", "status", "peak_equity", "peak_timestamp_ns", "peak_observation_index",
    "peak_is_initial", "start_timestamp_ns", "start_observation_index", "trough_equity",
    "trough_timestamp_ns", "trough_observation_index", "recovery_equity", "recovery_timestamp_ns",
    "recovery_observation_index", "end_timestamp_ns", "end_observation_index", "depth",
    "decline_duration_ns", "recovery_duration_ns", "duration_ns", "observed_duration_ns",
    "duration_is_lower_bound", "decline_observations", "recovery_observations", "duration_observations",
]
_SUMMARY_FIELDS = {
    "episode_count", "recovered_episode_count", "unrecovered_episode_count", "deepest", "longest", "current",
}
_evidence_counter = get_meter().create_counter(
    "nlalpha.empyrical.drawdown_evidence", description="Drawdown evidence envelopes built"
)


def _integer(value: object, field: str, *, nullable: bool = False) -> int | None:
    """Validate exact integer timestamps and counts."""
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(field, f"{field} must be an integer")
    return value


@dataclass
class _Episode:
    """Maintain one peak-to-recovery or peak-to-sample-end drawdown."""

    episode_id: int
    peak_equity: float
    peak_timestamp_ns: int | None
    peak_observation_index: int
    start_timestamp_ns: int
    start_observation_index: int
    trough_equity: float
    trough_timestamp_ns: int
    trough_observation_index: int
    end_timestamp_ns: int
    end_observation_index: int
    depth: float
    recovery_equity: float | None = None
    recovery_timestamp_ns: int | None = None
    recovery_observation_index: int | None = None

    @property
    def observed_duration_ns(self) -> int:
        """Return the known elapsed duration for this episode."""
        start = self.start_timestamp_ns if self.peak_timestamp_ns is None else self.peak_timestamp_ns
        return self.end_timestamp_ns - start

    @property
    def duration_observations(self) -> int:
        """Return the number of observation intervals since its reference peak."""
        return self.end_observation_index - self.peak_observation_index

    def row(self) -> dict:
        """Materialize one episode using exact observation timestamps and indices."""
        initial = self.peak_timestamp_ns is None
        recovered = self.recovery_timestamp_ns is not None
        return {
            "episode_id": self.episode_id,
            "status": "recovered" if recovered else "unrecovered",
            "peak_equity": self.peak_equity,
            "peak_timestamp_ns": self.peak_timestamp_ns,
            "peak_observation_index": self.peak_observation_index,
            "peak_is_initial": initial,
            "start_timestamp_ns": self.start_timestamp_ns,
            "start_observation_index": self.start_observation_index,
            "trough_equity": self.trough_equity,
            "trough_timestamp_ns": self.trough_timestamp_ns,
            "trough_observation_index": self.trough_observation_index,
            "recovery_equity": self.recovery_equity,
            "recovery_timestamp_ns": self.recovery_timestamp_ns,
            "recovery_observation_index": self.recovery_observation_index,
            "end_timestamp_ns": self.end_timestamp_ns,
            "end_observation_index": self.end_observation_index,
            "depth": self.depth,
            "decline_duration_ns": None if initial else self.trough_timestamp_ns - self.peak_timestamp_ns,
            "recovery_duration_ns": self.recovery_timestamp_ns - self.trough_timestamp_ns if recovered else None,
            "duration_ns": None if initial else self.end_timestamp_ns - self.peak_timestamp_ns,
            "observed_duration_ns": self.observed_duration_ns,
            "duration_is_lower_bound": initial,
            "decline_observations": self.trough_observation_index - self.peak_observation_index,
            "recovery_observations": self.recovery_observation_index - self.trough_observation_index if recovered else None,
            "duration_observations": self.duration_observations,
        }


def _longer(candidate: _Episode, previous: _Episode | None) -> bool:
    """Select the longest observed duration with deterministic tie rules."""
    return previous is None or (
        candidate.observed_duration_ns, candidate.duration_observations, -candidate.episode_id
    ) > (
        previous.observed_duration_ns, previous.duration_observations, -previous.episode_id
    )


class DrawdownTracker:
    """Track peaks, troughs and recoveries while retaining bounded summary state."""

    def __init__(self, initial_equity: float, *, record_history: bool = True):
        self._peak_equity = _finite_number(initial_equity, "initial_equity")
        if self._peak_equity <= 0:
            raise _error("initial_equity", "initial_equity must be greater than zero")
        if not isinstance(record_history, bool):
            raise _error("record_history", "record_history must be a boolean")
        self._peak_timestamp_ns = None
        self._peak_observation_index = 0
        self._observation_count = 0
        self._last_timestamp_ns = None
        self._episode_count = 0
        self._recovered_count = 0
        self._current = None
        self._deepest = None
        self._longest_recovered = None
        self._history = [] if record_history else None

    def observe(self, timestamp_ns: int, equity: float) -> None:
        """Observe one strictly ordered completed-close equity value."""
        timestamp = _integer(timestamp_ns, "timestamp_ns")
        if self._last_timestamp_ns is not None and timestamp <= self._last_timestamp_ns:
            raise _error("timestamp_ns", "observation timestamps must be strictly increasing")
        equity = _finite_number(equity, "equity")
        underwater = equity < self._peak_equity
        depth = 0.0
        if underwater:
            depth = 1.0 - equity / self._peak_equity if equity < 0 else (self._peak_equity - equity) / self._peak_equity
        if not math.isfinite(depth):
            raise _error("depth", "drawdown depth must be finite")
        index = self._observation_count + 1
        if underwater:
            if self._current is None:
                self._episode_count += 1
                self._current = _Episode(
                    self._episode_count, self._peak_equity, self._peak_timestamp_ns,
                    self._peak_observation_index, timestamp, index, equity, timestamp, index,
                    timestamp, index, depth,
                )
            else:
                self._current.end_timestamp_ns = timestamp
                self._current.end_observation_index = index
                if equity < self._current.trough_equity:
                    self._current.trough_equity = equity
                    self._current.trough_timestamp_ns = timestamp
                    self._current.trough_observation_index = index
                    self._current.depth = depth
            if self._deepest is None or self._current.depth > self._deepest.depth:
                self._deepest = self._current
        else:
            if self._current is not None:
                self._current.end_timestamp_ns = timestamp
                self._current.end_observation_index = index
                self._current.recovery_equity = equity
                self._current.recovery_timestamp_ns = timestamp
                self._current.recovery_observation_index = index
                self._recovered_count += 1
                if _longer(self._current, self._longest_recovered):
                    self._longest_recovered = self._current
                if self._history is not None:
                    self._history.append(self._current)
                self._current = None
            self._peak_equity = equity
            self._peak_timestamp_ns = timestamp
            self._peak_observation_index = index
        self._observation_count = index
        self._last_timestamp_ns = timestamp

    def finish(self) -> tuple[dict, list[dict]]:
        """Return an independent summary and recorded rows without mutating tracker state."""
        longest = self._longest_recovered
        if self._current is not None and _longer(self._current, longest):
            longest = self._current
        summary = {
            "episode_count": self._episode_count,
            "recovered_episode_count": self._recovered_count,
            "unrecovered_episode_count": int(self._current is not None),
            "deepest": self._deepest.row() if self._deepest is not None else None,
            "longest": longest.row() if longest is not None else None,
            "current": self._current.row() if self._current is not None else None,
        }
        rows = []
        if self._history is not None:
            rows = [episode.row() for episode in self._history]
            if self._current is not None:
                rows.append(self._current.row())
        return summary, rows


def build_drawdown_evidence(
    *,
    summary: dict,
    rows: list[dict],
    record_mode: str,
    source: str,
    schema_id: str,
    observation_count: int,
    first_observation_timestamp_ns: int | None,
    last_observation_timestamp_ns: int | None,
    initial_reference_timestamp_ns: int | None,
) -> tuple[dict, dict]:
    """Build a drawdown report envelope and explain its sampling and unavailable values."""
    if not isinstance(summary, dict) or set(summary) != _SUMMARY_FIELDS:
        raise _error("summary", "drawdown summary must contain the declared summary fields")
    if not isinstance(rows, list):
        raise _error("rows", "drawdown rows must be a list")
    if not isinstance(record_mode, str) or record_mode not in {"full", "summary"}:
        raise _error("record_mode", "record_mode must be full or summary")
    for field, value in (("source", source), ("schema_id", schema_id)):
        if not isinstance(value, str) or not value or value != value.strip():
            raise _error(field, f"{field} must be a non-empty trimmed string")
    count = _integer(observation_count, "observation_count")
    first = _integer(first_observation_timestamp_ns, "first_observation_timestamp_ns", nullable=True)
    last = _integer(last_observation_timestamp_ns, "last_observation_timestamp_ns", nullable=True)
    reference = _integer(initial_reference_timestamp_ns, "initial_reference_timestamp_ns", nullable=True)
    if count < 0:
        raise _error("observation_count", "observation_count must be non-negative")
    if count == 0 and (first is not None or last is not None):
        raise _error("observation_count", "empty observations require null observation timestamps")
    if count > 0 and (first is None or last is None or first > last or (count == 1 and first != last) or (count > 1 and first == last)):
        raise _error("observation_timestamps", "observation count must agree with ordered timestamp bounds")
    for field in ("episode_count", "recovered_episode_count", "unrecovered_episode_count"):
        if _integer(summary[field], field) < 0:
            raise _error(field, f"{field} must be non-negative")
    episodes = summary["episode_count"]
    unrecovered = summary["unrecovered_episode_count"]
    if episodes != summary["recovered_episode_count"] + unrecovered or unrecovered > 1 or episodes > count:
        raise _error("summary", "drawdown episode counts are inconsistent")
    for field in ("deepest", "longest", "current"):
        row = summary[field]
        expected = bool(unrecovered) if field == "current" else bool(episodes)
        if expected != (row is not None) or (row is not None and (not isinstance(row, dict) or set(row) != set(DRAWDOWN_COLUMNS))):
            raise _error(field, f"{field} must agree with episode counts and declared row fields")
    if record_mode == "summary" and rows:
        raise _error("rows", "summary mode must not contain recorded drawdown rows")
    if record_mode == "full" and len(rows) != episodes:
        raise _error("rows", "recorded drawdown row count must match episode_count")
    unavailable = {key: "no_equity_observations" for key in ("max_drawdown", "drawdowns")} if count == 0 else {}
    with start_span("empyrical.build_drawdown_evidence", attributes={"observation_count": count, "episode_count": episodes}):
        diagnostics = {
            "sampling": "completed_bar_close_net_equity",
            "fee_treatment": "already_reflected_in_net_equity",
            "timestamp_unit": "ns",
            "timestamp_timezone": "UTC",
            "timestamp_columns": [field for field in DRAWDOWN_COLUMNS if field.endswith("timestamp_ns")],
            "depth_unit": "proportion",
            "depth_definition": "(peak_equity-trough_equity)/peak_equity",
            "peak_rule": "most_recent_greater_or_equal_equity",
            "trough_rule": "earliest_equal_trough_only_strictly_lower_equity_updates",
            "episode_start_rule": "first_equity_strictly_below_high_water_mark",
            "recovery_rule": "first_observation_greater_or_equal_episode_peak_equity",
            "deepest_selection": "largest_depth_then_earlier_episode_id",
            "longest_selection": "largest_observed_duration_ns_then_duration_observations_then_earlier_episode_id",
            "longest_duration_basis": "observed_duration_not_unknown_total_duration",
            "initial_peak_duration_rule": "unknown_total_duration_observed_duration_is_lower_bound_from_first_underwater_observation",
            "observed_peak_duration_rule": "duration_from_observed_peak_to_recovery_or_sample_end",
            "observation_interval_rule": "index_difference_initial_reference_to_first_observation_counts_as_one_interval",
            "initial_reference_timestamp_role": "informational_only_not_a_duration_endpoint",
            "nullable_field_reasons": {
                "peak_timestamp_ns": "initial_capital_has_no_economic_timestamp",
                "decline_duration_ns": "initial_peak_has_unknown_economic_start",
                "duration_ns": "initial_peak_has_unknown_economic_start",
                "recovery_equity": "episode_unrecovered_at_sample_end",
                "recovery_timestamp_ns": "episode_unrecovered_at_sample_end",
                "recovery_observation_index": "episode_unrecovered_at_sample_end",
                "recovery_duration_ns": "episode_unrecovered_at_sample_end",
                "recovery_observations": "episode_unrecovered_at_sample_end",
                "initial_reference_timestamp_ns": "reference_timestamp_not_provided",
                "first_observation_timestamp_ns": "no_equity_observations",
                "last_observation_timestamp_ns": "no_equity_observations",
                "deepest": "no_drawdown_episodes_or_no_equity_observations",
                "longest": "no_drawdown_episodes_or_no_equity_observations",
                "current": "no_active_drawdown_episode",
            },
            "observation_count": count,
            "first_observation_timestamp_ns": first,
            "last_observation_timestamp_ns": last,
            "initial_reference_timestamp_ns": reference,
            "record_mode": record_mode,
            "unavailable": unavailable,
        }
        status = "unavailable" if count == 0 else ("available" if record_mode == "full" else "not_recorded")
        reason = "no_equity_observations" if count == 0 else (None if record_mode == "full" else "record_mode_summary")
        report = {
            "schema_id": schema_id,
            "source": source,
            "status": status,
            "reason": reason,
            "row_count": None if count == 0 else episodes,
            "columns": list(DRAWDOWN_COLUMNS),
            "rows": rows,
            "metadata": dict(diagnostics),
        }
        _evidence_counter.add(1, {"source": source, "record_mode": record_mode})
        return report, diagnostics
