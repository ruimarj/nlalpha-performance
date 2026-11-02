"""Check all performance methods against independent arithmetic reference values."""

import math
import statistics
import unittest

from nlalpha_performance.provider import PerformanceProvider


class KnownPerformanceMetricsTests(unittest.TestCase):
    def setUp(self):
        self.provider = PerformanceProvider()
        self.returns = [0.1, -0.05, 0.02, -0.01]
        self.annualization = 4
        self.total = math.prod(1 + value for value in self.returns) - 1
        self.deviation = statistics.stdev(self.returns)
        self.mean = statistics.mean(self.returns)

    def test_performance(self):
        metrics = self.provider.calculate_performance(self.returns, annualization=self.annualization)
        self.assertAlmostEqual(metrics["total_return"], self.total)
        self.assertAlmostEqual(metrics["annual_return"], self.total)
        self.assertEqual(metrics["unavailable"], {})

    def test_risk(self):
        metrics = self.provider.calculate_risk(self.returns, annualization=self.annualization)
        downside = math.sqrt(sum(min(value, 0) ** 2 for value in self.returns))
        expected = {
            "annual_volatility": self.deviation * 2,
            "downside_volatility": downside,
            "sharpe_ratio": self.mean / self.deviation * 2,
            "sortino_ratio": self.mean * 4 / downside,
            "omega_ratio": 2,
            "max_drawdown": -0.05,
            "calmar_ratio": self.total / 0.05,
        }
        for metric, value in expected.items():
            with self.subTest(metric=metric):
                self.assertAlmostEqual(metrics[metric], value)
        self.assertEqual(metrics["unavailable"], {})

    def test_max_drawdown(self):
        self.assertAlmostEqual(self.provider.max_drawdown(self.returns), -0.05)

    def test_calmar_ratio(self):
        self.assertAlmostEqual(self.provider.calmar_ratio(self.returns, annualization=4), self.total / 0.05)

    def test_benchmark_relative(self):
        benchmark = [-0.02, 0.01, 0.04, -0.01]
        benchmark_mean = statistics.mean(benchmark)
        beta = sum((value - self.mean) * (reference - benchmark_mean)
                   for value, reference in zip(self.returns, benchmark)) / sum((value - benchmark_mean) ** 2 for value in benchmark)
        residual_mean = statistics.mean(value - beta * reference for value, reference in zip(self.returns, benchmark))
        excess = [value - reference for value, reference in zip(self.returns, benchmark)]
        excess_annual = statistics.mean(excess) * 4
        tracking_error = statistics.stdev(excess) * 2
        expected = {"benchmark_return": benchmark_mean * 4, "excess_return": excess_annual,
                    "tracking_error": tracking_error, "information_ratio": excess_annual / tracking_error,
                    "alpha": (1 + residual_mean) ** 4 - 1, "beta": beta}
        metrics = self.provider.calculate_benchmark_relative(self.returns, benchmark, annualization=4)
        for metric, value in expected.items():
            with self.subTest(metric=metric):
                self.assertAlmostEqual(metrics[metric], value)

    def test_basic_statistics(self):
        metrics = self.provider.calculate_basic_statistics(
            initial_equity=100, final_equity=100 * (1 + self.total), return_count=4,
            return_mean=self.mean, return_m2=sum((value - self.mean) ** 2 for value in self.returns),
            settings={"annualization": 4, "annualization_source": "configured", "risk_free_rate": 0.0,
                      "risk_free_rate_source": "configured"},
        )
        self.assertAlmostEqual(metrics["performance"]["annual_return"], self.total)
        self.assertAlmostEqual(metrics["risk"]["annual_volatility"], self.deviation * 2)
        self.assertAlmostEqual(metrics["risk"]["sharpe_ratio"], self.mean / self.deviation * 2)
