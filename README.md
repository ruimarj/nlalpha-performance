# nlalpha-performance

`nlalpha-performance` 0.6.0 provides shared performance analytics and annualized statistics from account equity observations for backtest engines.

## Capability discovery

The `nlalpha.capabilities` entry point is `performance = nlalpha_performance:PerformancePlugin`. Its stable provider ID is `performance`, classified under `performance`; the plugin caches one provider instance per discovery record. If `empyrical-reloaded` is missing from a degraded environment, the installed plugin reports provider `performance` as unavailable with a reason. The capability catalog omits it from available helpers and `diagnose_providers()` exposes the installed-but-unavailable state. The package does not start its own MCP server; query it through `nlalpha-capability`.

Install the community package and its declared dependencies with:

```bash
python -m pip install nlalpha-performance
```

Set `annualization` to a positive integer observations-per-year assumption. Without it, annual return, annual volatility, and Sharpe are returned as `null` with an `annualization_not_provided` diagnostic; the provider does not infer a conventional frequency. The finite annual `risk_free_rate` must be greater than -1 and defaults to 0 with source `engine_default`. When annualization is configured, it is converted to a per-observation compound rate as `expm1(log1p(rate) / annualization)`.

Statistics use simple returns of net account equity, sample variance (`ddof=1`), and explicit observation counts. Missing samples, insufficient observations, zero variance for Sharpe, non-positive equity, and numerical overflow are reported as unavailable with reasons. Annualization counts observations rather than calendar time; irregular spacing is not filled or resampled. Consumers record sampling timestamps and spacing, setting provenance, return basis, and fee treatment in diagnostics.

## Drawdown episodes

The shared drawdown summary is `metrics.risk.drawdowns` with `episode_count`, `recovered_episode_count`, `unrecovered_episode_count`, and `deepest`, `longest`, `current` episode rows (each nullable when absent). `metrics.risk.max_drawdown` remains the scalar maximum and agrees with `deepest.depth`, or is zero when observations exist without an episode. `reports.drawdowns` uses the standard report envelope and columns: `episode_id`, `status`, `peak_equity`, `peak_timestamp_ns`, `peak_observation_index`, `peak_is_initial`, `start_timestamp_ns`, `start_observation_index`, `trough_equity`, `trough_timestamp_ns`, `trough_observation_index`, `recovery_equity`, `recovery_timestamp_ns`, `recovery_observation_index`, `end_timestamp_ns`, `end_observation_index`, `depth`, `decline_duration_ns`, `recovery_duration_ns`, `duration_ns`, `observed_duration_ns`, `duration_is_lower_bound`, `decline_observations`, `recovery_observations`, `duration_observations`. The Vector and Nautilus schema IDs and sources remain engine-specific.

Episodes use completed-bar-close net equity, with fees already reflected. The initial-capital reference is index 0 but has no economic timestamp; its actual reference timestamp, when known, is informational metadata only. The high-water mark resets on equal or greater equity (latest equal peak); a drawdown starts strictly below it, recovery is the first observation at or above the episode peak, and equal troughs keep the earliest trough. Depth is `(peak_equity - trough_equity) / peak_equity`, a proportion. `deepest` chooses greatest depth (ties: earlier episode); `longest` means longest observed drawdown, ordered by observed elapsed duration, then observation intervals, then earlier episode.

If the peak is initial capital, peak timestamp, decline duration and total duration are null because the economic start is unknown. Observed duration from the first underwater point is only a lower bound (`duration_is_lower_bound=true`). Recovery duration is measured from trough to recovery only for recovered episodes; unrecovered recovery fields remain null. Elapsed nanoseconds and observation interval counts are separate, exact quantities: the initial reference to the first actual observation counts as an interval, never as a fabricated 1 ns. An episode still open at sample end uses the last observed point as its observational end without assuming recovery. No observations yield `unavailable/no_equity_observations`; available with zero rows means observations existed and no episode occurred.

The report describes sampled closes, not intrabar peaks/troughs. It does not resample calendars, infer annualization, or synthesize timestamps. Nautilus always records its available observed path, while Vector summary keeps bounded O(1) tracker state and aggregate rows but marks episode history `not_recorded/record_mode_summary` with the known episode count; Vector full records all episodes. Continue using `get_backtest_report` for paged drawdown rows (maximum 200 rows per page); the existing 32 MiB artifact cap remains.

## Historical 0.3.0 focused verification

Historical Empyrical 0.3.0 tracker verification passed for four hand-calculated episodes, initial-peak lower-bound durations, tie rules, separate deepest/longest/current selection, negative equity, extreme `2^64-1` nanosecond span, invalid input, flat and empty observations, and input nonmutation. This is targeted private verification, not a full suite or benchmark rerun.

## Monthly and yearly net returns

`metrics.performance.period_returns` summarizes observed `monthly` and `yearly` buckets; detailed rows are exposed by the existing `reports.monthly_returns` and `reports.yearly_returns` tables and retrieved with `get_backtest_report`. No tool ID or input schema is added. Each report has an engine-specific schema and the shared report envelope. Rows identify the Gregorian period in `execution.analysis_timezone` and its exact calendar bounds represented as UTC nanoseconds, reference/end equity and timestamps, endpoint net PnL/return, observation indexes/count, sample-boundary flags, cross-period attribution, skipped periods, non-positive-equity observation count, and per-field unavailable reasons.

Buckets use local Gregorian month/year boundaries from raw nanosecond timestamps in `execution.analysis_timezone` (default `UTC`); boundaries are represented as UTC nanoseconds. A bucket return is the net-equity change assigned to the period of its ending observation; the initial bucket uses starting capital as reference and has no economic reference timestamp. Later buckets use the preceding real observation as reference. Fees and slippage are already reflected in net equity. Non-positive reference equity makes return unavailable; valid negative/zero end equity is preserved. Missing calendar periods are omitted, never filled with zero or carried equity. Skipped-period and cross-period interval diagnostics disclose when a bucket endpoint return spans a gap. `diagnostics.period_returns` records the analysis timezone, pinned timezone database version and TZIF hash, and strict local-midnight boundary policy; ambiguous or nonexistent month boundaries fail explicitly.

Summary counts, positive fraction, best/worst/current include valid observed buckets, including partial first and last sample periods; denominators are valid observed buckets, not all elapsed or only complete calendar periods. `yearly_returns` are observed yearly endpoint changes, not `annual_return` or CAGR and are not compounded from monthly rows. A `summary` run retains these bounded aggregates but marks report rows `not_recorded`; `full` stores rows. Vector uses O(1) aggregation without per-bar date conversion within a bucket. Nautilus independently derives its period rows from its own raw-nanosecond net-equity path, excluding its artificial baseline from buckets while preserving native execution and reporting. Engine schemas and sources remain distinct.

Each frequency summary reports `observed_period_count`, `valid_period_count`, `unavailable_period_count`, positive/negative/flat counts and fraction, skipped-period and cross-period interval counts, skipped-gap bucket count, and best/worst/current rows. Row fields include `period`, local calendar start/end represented as UTC nanoseconds, reference and ending equity/timestamp/index, `net_pnl`, `net_return`, `observation_count`, first/last flags, `closed_by_next_period`, `cross_period_interval_count`, `skipped_periods_before`, `non_positive_equity_observation_count`, and `unavailable` reasons. `yearly` and `monthly` use the same conceptual fields under their own table schema.

## Historical Empyrical 0.4.0 verification

Empyrical 0.4.0 shared tracker hand cases passed for independent monthly/yearly endpoints, UTC boundary ±1 ns, leap years/gaps, valid-period denominator, non-positive references, overflow-to-null behavior, stable finish, and exact i128 calendar bounds/JSON conversion for signed-i64 extremes. Offline lock and local package rebuild checks passed. This was focused verification, not the full suite or a benchmark rerun.

`PeriodReturnsTracker` accepts `analysis_timezone` (default `UTC`) and groups the ordered UTC-nanosecond observations by that timezone’s local Gregorian boundaries. Python and Rust share Core’s exact pinned TZIF bytes. Report timestamps and bounds remain UTC ns; diagnostics include timezone database identity/hash, and ambiguous or nonexistent local month boundaries fail explicitly. This changes the analysis calendar only; source interpretation and event execution remain unchanged.

## Public dependency verification

An isolated CPython 3.12.13 environment using public `empyrical-reloaded` 0.5.12, NumPy 2.5.4, and pandas 2.3.3 passed 6 tests and 13 subtests in 1.33 seconds. The parent checked the six metric outputs against independent mathematical expectations. The workspace lock update is still in progress; this focused result does not establish that the official lock or every supported environment has been verified.


## License

Community distribution under AGPL-3.0-or-later. This applies to the NLAlpha adapter package; empyrical-reloaded and other third-party dependencies retain their own licenses.
