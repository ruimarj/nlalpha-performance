# nlalpha-performance

`nlalpha-performance` 0.6.0 为回测引擎提供共享绩效分析，并基于账户权益观测计算年化统计。

## 能力发现

包通过 `nlalpha.capabilities` entry point `performance = nlalpha_performance:PerformancePlugin` 注册，不单独启动 MCP Server，应通过 `nlalpha-capability` 查询。稳定 provider ID 为 `performance`，分类为 `performance`；每个发现记录会缓存 provider 实例。若降级环境中缺少 `empyrical-reloaded`，已安装插件仍以同一 ID 报告 unavailable 和原因；可用目录不列出该 provider，`diagnose_providers()` 会显示已安装但不可用的状态。

安装包及其声明依赖：

```bash
python -m pip install nlalpha-performance
```

`annualization` 必须是表示每年观测数的正整数。未提供时，年化收益、年化波动率和 Sharpe 均返回 `null`，诊断原因为 `annualization_not_provided`；provider 不推断惯用频率。有限年化 `risk_free_rate` 必须大于 -1，默认为来源 `engine_default` 的 0，配置 annualization 后，再按 `expm1(log1p(rate) / annualization)` 换算为每期复合利率。

统计使用净账户权益的简单收益和样本方差（`ddof=1`），保留明确的观测次数。无样本、样本不足、Sharpe 零方差、非正权益和数值溢出均会附原因标记为不可用。年化代表每年观测次数，不按日历时间推断；不规则间距不插值或重采样。使用方会在诊断中记录采样时间戳与间距、参数来源、收益口径及费用处理。

## 回撤区间

共享摘要 `metrics.risk.drawdowns` 包含 `episode_count`、`recovered_episode_count`、`unrecovered_episode_count`，以及可能为空的 `deepest`、`longest`、`current` 区间行。`metrics.risk.max_drawdown` 保留标量最大回撤，与 `deepest.depth` 一致；若已有观测但没有区间则为 0。`reports.drawdowns` 使用通用报告外层结构，列为：`episode_id`、`status`、`peak_equity`、`peak_timestamp_ns`、`peak_observation_index`、`peak_is_initial`、`start_timestamp_ns`、`start_observation_index`、`trough_equity`、`trough_timestamp_ns`、`trough_observation_index`、`recovery_equity`、`recovery_timestamp_ns`、`recovery_observation_index`、`end_timestamp_ns`、`end_observation_index`、`depth`、`decline_duration_ns`、`recovery_duration_ns`、`duration_ns`、`observed_duration_ns`、`duration_is_lower_bound`、`decline_observations`、`recovery_observations`、`duration_observations`。Vector 与 Nautilus 的 schema ID 和 source 各自定义。

区间基于已完成 bar 收盘时的净权益，费用已计入。初始资金是 index 0 的参考值，但没有经济时间戳；如记录实际参考时间，只作为 metadata 信息。权益达到或超过高水位时更新峰值（相同峰值选最近一次）；低于峰值才开始回撤，首次回到区间峰值或以上即恢复，相同谷值保留最早一次。深度 `(peak_equity - trough_equity) / peak_equity` 是比例。`deepest` 选深度最大者（并列选 episode 较早者）；`longest` 明确指观测到的最长回撤，先比较已观测时长，再比较观测间隔数，最后选 episode 较早者。

若峰值为初始资金，峰值时间戳、下跌时长和总时长均为 null，因为经济起点未知；从首次低于水位至当前结束的观测时长仅是下限（`duration_is_lower_bound=true`）。仅已恢复区间计算谷底至恢复的 recovery duration；未恢复区间的恢复字段均为 null。纳秒 elapsed duration 与观测间隔数分别记录且精确：初始参考至首个真实观测算一个间隔，不能伪造成 1 纳秒。样本结束时仍未恢复的区间以最后观测作为观测结束点，不假设恢复。没有权益观测为 `unavailable/no_equity_observations`；有观测且无区间则是 available 空表。

报告描述的是收盘采样，不是 K 线内部峰谷；不做日历重采样、年化推断或时间戳合成。Nautilus 总是记录已有观测路径；Vector summary 仅保留 O(1) tracker 状态及摘要，并将区间历史标成 `not_recorded/record_mode_summary`，同时保留已知区间数；Vector full 才记录所有区间。继续通过 `get_backtest_report` 分页读取回撤行（每页最多 200 行）；既有 32 MiB artifact 上限不变。

## Empyrical 0.3.0 历史核验

Empyrical 0.3.0 tracker 历史核验通过：四个手算区间、初始峰值时长下限、并列规则、deepest/longest/current 分别选择、负权益、`2^64-1` 纳秒跨度、非法输入、平坦/空观测及输入不变性。这是私有针对性核验，不是完整 suite 或性能重测。

## 月度与年度净收益

`metrics.performance.period_returns` 汇总已观测的 `monthly`、`yearly` 桶；明细分别通过 `reports.monthly_returns`、`reports.yearly_returns` 表提供，并用现有 `get_backtest_report` 分页读取。不增加工具 ID 或输入 schema。报告沿用通用外层契约和引擎各自 schema。每行包含 analysis_timezone 下的公历周期及其 UTC 纳秒日历边界、参考/期末权益和时间戳、端点净盈亏/收益、观测索引/次数、样本边界标记、跨期归属、跳过周期数、非正权益观测数及字段不可用原因。

按原始纳秒时间戳和 `execution.analysis_timezone`（默认 `UTC`）映射当地公历月/年边界；边界以 UTC 纳秒表示。桶收益是净权益变化归属于结束观测所在周期的端点收益；首桶以初始资金为参考，但初始资金没有经济参考时间戳。后续桶以前一个真实观测为参考。费用和滑点已反映在净权益中。参考权益非正时收益不可用；非正的期末权益仍保留。缺失日历周期不生成行、不补零、不前向填充；skipped-period 与 cross-period interval 诊断会指出端点收益跨越的缺口。`diagnostics.period_returns` 记录分析时区、固定 tzdata 版本和 TZIF 哈希，以及严格处理当地月初边界的策略；不存在或歧义的当地午夜会明确失败。

summary 的计数、正收益比例、best/worst/current 以有效已观测桶为分母，包含有效的首尾不完整样本桶；分母不是样本覆盖的全部日历周期，也不只计算完整周期。`yearly_returns` 是按年度端点观测的收益，不等同于 `annual_return` 或 CAGR，也不由月收益复利得出。`summary` 只保留有界聚合，明细行标记 `not_recorded`；`full` 才保存行。Vector 以 O(1) 聚合，同一桶内不逐 bar 做日期转换。Nautilus 从自身原始纳秒净权益路径独立生成周期行，排除人工 baseline，不改变原生执行和报告。两引擎的 schema 和 source 保持独立。

每个频率的摘要提供 `observed_period_count`、`valid_period_count`、`unavailable_period_count`、正/负/零收益桶计数及比例、跳过周期数、跨期区间数、跨缺口桶数，以及 best/worst/current 行。明细字段包括 `period`、当地日历起止（以 UTC 纳秒表示）、参考与期末权益/时间戳/索引、`net_pnl`、`net_return`、`observation_count`、首末标记、`closed_by_next_period`、`cross_period_interval_count`、`skipped_periods_before`、`non_positive_equity_observation_count` 和 `unavailable` 原因。月度与年度表含义对应，但各自遵循引擎 schema。

## Empyrical 0.4.0 历史核验

Empyrical 0.4.0 共享 tracker 手算核验通过：月/年独立端点、UTC 边界 ±1ns、闰年/缺期、有效周期分母、非正参考权益、溢出转 null、finish 稳定性，以及 signed-i64 极值的 i128 日历边界和 JSON 精确转换。离线 lock 与本地包构建检查通过。这是针对性核验，不是完整 suite 或基准重测。

`PeriodReturnsTracker` 接受 `analysis_timezone`（默认 `UTC`），按 UTC 纳秒排序的观测值归入该时区当地公历边界。Python 与 Rust 使用 Core 中相同固定 TZIF 数据。报告时间和边界仍为 UTC ns；诊断记录时区库身份/哈希，歧义或不存在的月初本地时刻会明确失败。该配置只改变统计日历，不改变源时间解释和事件执行。
