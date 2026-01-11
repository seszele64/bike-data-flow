#!/usr/bin/env python3
"""
Vault Latency Benchmark Script

This script measures Vault secret read/write latency and concurrent access performance.
It outputs results to console and optionally to a JSON file for CI/CD integration.

Usage:
    python benchmark-latency.py [--vault-addr VAULT_ADDR] [--role-id ROLE_ID] \
        [--secret-id SECRET_ID] [--iterations N] [--concurrency N] \
        [--output OUTPUT_FILE] [--json]

Example:
    python scripts/vault/benchmark-latency.py \
        --vault-addr https://vault.example.com:8200 \
        --role-id my-role-id \
        --secret-id my-secret-id \
        --iterations 100 \
        --concurrency 10 \
        --output benchmark_results.json \
        --json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wrm_pipeline.vault import VaultClient
from wrm_pipeline.vault.exceptions import VaultError
from wrm_pipeline.vault.models import VaultConnectionConfig


@dataclass
class BenchmarkResult:
    """Result of a single benchmark operation."""
    operation: str
    path: str
    success: bool
    duration_ms: float
    error_message: str | None = None


@dataclass
class BenchmarkSummary:
    """Summary statistics for benchmark results."""
    operation: str
    path: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    std_dev_ms: float
    requests_per_second: float
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "operation": self.operation,
            "path": self.path,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "latency_ms": {
                "min": self.min_ms,
                "max": self.max_ms,
                "mean": self.mean_ms,
                "median": self.median_ms,
                "p50": self.p50_ms,
                "p95": self.p95_ms,
                "p99": self.p99_ms,
                "std_dev": self.std_dev_ms,
            },
            "throughput": {
                "requests_per_second": self.requests_per_second,
            },
            "errors": self.errors,
        }


def percentile(data: list[float], p: float) -> float:
    """Calculate the p-th percentile of a list of values."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    index = int(len(sorted_data) * p / 100)
    return sorted_data[min(index, len(sorted_data) - 1)]


def calculate_summary(
    operation: str,
    path: str,
    results: list[BenchmarkResult],
) -> BenchmarkSummary:
    """Calculate summary statistics from benchmark results."""
    durations = [r.duration_ms for r in results if r.success]
    failed = [r for r in results if not r.success]
    errors = list(set(r.error_message for r in failed if r.error_message))

    if not durations:
        return BenchmarkSummary(
            operation=operation,
            path=path,
            total_requests=len(results),
            successful_requests=0,
            failed_requests=len(results),
            min_ms=0.0,
            max_ms=0.0,
            mean_ms=0.0,
            median_ms=0.0,
            p50_ms=0.0,
            p95_ms=0.0,
            p99_ms=0.0,
            std_dev_ms=0.0,
            requests_per_second=0.0,
            errors=errors,
        )

    total_time = sum(durations) / 1000  # Convert to seconds

    return BenchmarkSummary(
        operation=operation,
        path=path,
        total_requests=len(results),
        successful_requests=len(durations),
        failed_requests=len(failed),
        min_ms=min(durations),
        max_ms=max(durations),
        mean_ms=statistics.mean(durations),
        median_ms=statistics.median(durations),
        p50_ms=percentile(durations, 50),
        p95_ms=percentile(durations, 95),
        p99_ms=percentile(durations, 99),
        std_dev_ms=statistics.stdev(durations) if len(durations) > 1 else 0.0,
        requests_per_second=len(durations) / total_time if total_time > 0 else 0.0,
        errors=errors,
    )


def format_duration(ms: float) -> str:
    """Format duration in milliseconds with appropriate unit."""
    if ms < 1:
        return f"{ms * 1000:.2f}µs"
    elif ms < 1000:
        return f"{ms:.2f}ms"
    else:
        return f"{ms / 1000:.2f}s"


def print_summary(summary: BenchmarkSummary) -> None:
    """Print benchmark summary to console."""
    print("\n" + "=" * 70)
    print(f"Benchmark: {summary.operation} - {summary.path}")
    print("=" * 70)
    print(f"  Total Requests:    {summary.total_requests}")
    print(f"  Successful:        {summary.successful_requests}")
    print(f"  Failed:            {summary.failed_requests}")
    print()
    print(f"  Latency (ms):")
    print(f"    Min:             {format_duration(summary.min_ms)}")
    print(f"    Max:             {format_duration(summary.max_ms)}")
    print(f"    Mean:            {format_duration(summary.mean_ms)}")
    print(f"    Median:          {format_duration(summary.median_ms)}")
    print(f"    P50:             {format_duration(summary.p50_ms)}")
    print(f"    P95:             {format_duration(summary.p95_ms)}")
    print(f"    P99:             {format_duration(summary.p99_ms)}")
    print(f"    Std Dev:         {format_duration(summary.std_dev_ms)}")
    print()
    print(f"  Throughput:        {summary.requests_per_second:.2f} req/s")
    print()

    if summary.errors:
        print(f"  Errors ({len(summary.errors)}):")
        for error in summary.errors[:5]:  # Show first 5 errors
            print(f"    - {error}")
        if len(summary.errors) > 5:
            print(f"    ... and {len(summary.errors) - 5} more")
    print("=" * 70)


class VaultLatencyBenchmark:
    """Benchmark Vault latency for secret operations."""

    def __init__(
        self,
        vault_addr: str,
        role_id: str,
        secret_id: str,
        namespace: str | None = None,
        verify: bool = True,
    ):
        """Initialize the benchmark with Vault connection config."""
        self.config = VaultConnectionConfig(
            vault_addr=vault_addr,
            auth_method="approle",
            role_id=role_id,
            secret_id=secret_id,
            namespace=namespace,
            verify=verify,
        )
        self.client = VaultClient(self.config)
        self.test_secret_path = "bike-data-flow/benchmark/test"
        self.lock = threading.Lock()

    def benchmark_read(
        self,
        iterations: int = 100,
        secret_path: str | None = None,
    ) -> list[BenchmarkResult]:
        """Benchmark secret read latency."""
        path = secret_path or self.test_secret_path
        results: list[BenchmarkResult] = []

        print(f"\nBenchmarking READ operations on: {path}")
        print(f"  Iterations: {iterations}")

        for i in range(iterations):
            start = time.perf_counter()
            try:
                self.client.get_secret(path)
                duration = (time.perf_counter() - start) * 1000
                results.append(
                    BenchmarkResult(
                        operation="read",
                        path=path,
                        success=True,
                        duration_ms=duration,
                    )
                )
            except VaultError as e:
                duration = (time.perf_counter() - start) * 1000
                results.append(
                    BenchmarkResult(
                        operation="read",
                        path=path,
                        success=False,
                        duration_ms=duration,
                        error_message=str(e),
                    )
                )

            if (i + 1) % 10 == 0:
                print(f"  Progress: {i + 1}/{iterations}")

        return results

    def benchmark_write(
        self,
        iterations: int = 100,
        secret_path: str | None = None,
    ) -> list[BenchmarkResult]:
        """Benchmark secret write latency."""
        path = secret_path or self.test_secret_path
        results: list[BenchmarkResult] = []
        test_data = {"benchmark": "data", "timestamp": str(datetime.utcnow())}

        print(f"\nBenchmarking WRITE operations on: {path}")
        print(f"  Iterations: {iterations}")

        for i in range(iterations):
            test_data["iteration"] = i
            start = time.perf_counter()
            try:
                self.client.write_secret(path, test_data)
                duration = (time.perf_counter() - start) * 1000
                results.append(
                    BenchmarkResult(
                        operation="write",
                        path=path,
                        success=True,
                        duration_ms=duration,
                    )
                )
            except VaultError as e:
                duration = (time.perf_counter() - start) * 1000
                results.append(
                    BenchmarkResult(
                        operation="write",
                        path=path,
                        success=False,
                        duration_ms=duration,
                        error_message=str(e),
                    )
                )

            if (i + 1) % 10 == 0:
                print(f"  Progress: {i + 1}/{iterations}")

        return results

    def benchmark_concurrent_read(
        self,
        concurrency: int = 10,
        iterations_per_thread: int = 10,
        secret_path: str | None = None,
    ) -> list[BenchmarkResult]:
        """Benchmark concurrent secret read latency."""
        path = secret_path or self.test_secret_path
        results: list[BenchmarkResult] = []
        total_requests = concurrency * iterations_per_thread

        print(f"\nBenchmarking CONCURRENT READ operations on: {path}")
        print(f"  Concurrency: {concurrency}")
        print(f"  Iterations per thread: {iterations_per_thread}")
        print(f"  Total requests: {total_requests}")

        def worker(thread_id: int) -> list[BenchmarkResult]:
            thread_results: list[BenchmarkResult] = []
            for i in range(iterations_per_thread):
                start = time.perf_counter()
                try:
                    self.client.get_secret(path)
                    duration = (time.perf_counter() - start) * 1000
                    thread_results.append(
                        BenchmarkResult(
                            operation="concurrent_read",
                            path=path,
                            success=True,
                            duration_ms=duration,
                        )
                    )
                except VaultError as e:
                    duration = (time.perf_counter() - start) * 1000
                    thread_results.append(
                        BenchmarkResult(
                            operation="concurrent_read",
                            path=path,
                            success=False,
                            duration_ms=duration,
                            error_message=str(e),
                        )
                    )
            return thread_results

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(worker, i) for i in range(concurrency)]
            for i, future in enumerate(as_completed(futures)):
                thread_results = future.result()
                results.extend(thread_results)
                print(f"  Thread {i + 1}/{concurrency} completed")

        return results

    def benchmark_health_check(
        self,
        iterations: int = 50,
    ) -> list[BenchmarkResult]:
        """Benchmark health check latency."""
        results: list[BenchmarkResult] = []

        print(f"\nBenchmarking HEALTH CHECK operations")
        print(f"  Iterations: {iterations}")

        for i in range(iterations):
            start = time.perf_counter()
            try:
                self.client.get_health()
                duration = (time.perf_counter() - start) * 1000
                results.append(
                    BenchmarkResult(
                        operation="health_check",
                        path="sys/health",
                        success=True,
                        duration_ms=duration,
                    )
                )
            except VaultError as e:
                duration = (time.perf_counter() - start) * 1000
                results.append(
                    BenchmarkResult(
                        operation="health_check",
                        path="sys/health",
                        success=False,
                        duration_ms=duration,
                        error_message=str(e),
                    )
                )

            if (i + 1) % 10 == 0:
                print(f"  Progress: {i + 1}/{iterations}")

        return results

    def run_full_benchmark(
        self,
        read_iterations: int = 100,
        write_iterations: int = 50,
        concurrency: int = 10,
        concurrency_iterations: int = 10,
        health_check_iterations: int = 50,
        secret_path: str | None = None,
    ) -> dict[str, BenchmarkSummary]:
        """Run the full benchmark suite."""
        print("\n" + "=" * 70)
        print("VAULT LATENCY BENCHMARK")
        print("=" * 70)
        print(f"  Vault Address: {self.config.vault_addr}")
        print(f"  Namespace: {self.config.namespace or 'default'}")
        print(f"  Started: {datetime.utcnow().isoformat()}")
        print("=" * 70)

        summaries: dict[str, BenchmarkSummary] = {}

        # Ensure test secret exists for read tests
        try:
            self.client.write_secret(
                secret_path or self.test_secret_path,
                {"benchmark": "data", "created": str(datetime.utcnow())}
            )
        except VaultError:
            pass  # May already exist

        # Benchmark health checks
        health_results = self.benchmark_health_check(health_check_iterations)
        health_summary = calculate_summary("health_check", "sys/health", health_results)
        summaries["health_check"] = health_summary
        print_summary(health_summary)

        # Benchmark read operations
        read_results = self.benchmark_read(read_iterations, secret_path)
        read_summary = calculate_summary("read", secret_path or self.test_secret_path, read_results)
        summaries["read"] = read_summary
        print_summary(read_summary)

        # Benchmark write operations
        write_results = self.benchmark_write(write_iterations, secret_path)
        write_summary = calculate_summary("write", secret_path or self.test_secret_path, write_results)
        summaries["write"] = write_summary
        print_summary(write_summary)

        # Benchmark concurrent reads
        concurrent_results = self.benchmark_concurrent_read(
            concurrency, concurrency_iterations, secret_path
        )
        concurrent_summary = calculate_summary(
            "concurrent_read", secret_path or self.test_secret_path, concurrent_results
        )
        summaries["concurrent_read"] = concurrent_summary
        print_summary(concurrent_summary)

        # Print overall summary
        print("\n" + "=" * 70)
        print("BENCHMARK COMPLETE")
        print("=" * 70)
        print(f"  Finished: {datetime.utcnow().isoformat()}")

        return summaries


def main() -> int:
    """Main entry point for the benchmark script."""
    parser = argparse.ArgumentParser(
        description="Benchmark Vault secret operations latency"
    )
    parser.add_argument(
        "--vault-addr",
        default=os.environ.get("VAULT_ADDR", "https://localhost:8200"),
        help="Vault server address (default: VAULT_ADDR env var)",
    )
    parser.add_argument(
        "--role-id",
        default=os.environ.get("VAULT_ROLE_ID"),
        help="AppRole role ID (default: VAULT_ROLE_ID env var)",
    )
    parser.add_argument(
        "--secret-id",
        default=os.environ.get("VAULT_SECRET_ID"),
        help="AppRole secret ID (default: VAULT_SECRET_ID env var)",
    )
    parser.add_argument(
        "--namespace",
        default=os.environ.get("VAULT_NAMESPACE"),
        help="Vault Enterprise namespace (default: VAULT_NAMESPACE env var)",
    )
    parser.add_argument(
        "--verify",
        type=bool,
        default=True,
        help="Verify TLS certificates (default: True)",
    )
    parser.add_argument(
        "--read-iterations",
        type=int,
        default=100,
        help="Number of read iterations (default: 100)",
    )
    parser.add_argument(
        "--write-iterations",
        type=int,
        default=50,
        help="Number of write iterations (default: 50)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Number of concurrent threads for concurrent benchmarks (default: 10)",
    )
    parser.add_argument(
        "--concurrency-iterations",
        type=int,
        default=10,
        help="Iterations per thread for concurrent benchmarks (default: 10)",
    )
    parser.add_argument(
        "--health-iterations",
        type=int,
        default=50,
        help="Number of health check iterations (default: 50)",
    )
    parser.add_argument(
        "--secret-path",
        default=None,
        help="Secret path to use for benchmarks",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output JSON file path (optional)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON to stdout",
    )

    args = parser.parse_args()

    # Validate required arguments
    if not args.vault_addr:
        print("Error: VAULT_ADDR is required")
        parser.print_help()
        return 1

    if not args.role_id or not args.secret_id:
        print("Error: VAULT_ROLE_ID and VAULT_SECRET_ID are required")
        parser.print_help()
        return 1

    try:
        benchmark = VaultLatencyBenchmark(
            vault_addr=args.vault_addr,
            role_id=args.role_id,
            secret_id=args.secret_id,
            namespace=args.namespace,
            verify=args.verify,
        )

        summaries = benchmark.run_full_benchmark(
            read_iterations=args.read_iterations,
            write_iterations=args.write_iterations,
            concurrency=args.concurrency,
            concurrency_iterations=args.concurrency_iterations,
            health_check_iterations=args.health_iterations,
            secret_path=args.secret_path,
        )

        # Output results
        if args.json:
            output = {
                "timestamp": datetime.utcnow().isoformat(),
                "vault_addr": args.vault_addr,
                "namespace": args.namespace,
                "summaries": {k: v.to_dict() for k, v in summaries.items()},
            }
            print(json.dumps(output, indent=2))

        if args.output:
            output = {
                "timestamp": datetime.utcnow().isoformat(),
                "vault_addr": args.vault_addr,
                "namespace": args.namespace,
                "configuration": {
                    "read_iterations": args.read_iterations,
                    "write_iterations": args.write_iterations,
                    "concurrency": args.concurrency,
                    "concurrency_iterations": args.concurrency_iterations,
                    "health_iterations": args.health_iterations,
                },
                "summaries": {k: v.to_dict() for k, v in summaries.items()},
            }
            with open(args.output, "w") as f:
                json.dump(output, f, indent=2)
            print(f"\nResults saved to: {args.output}")

        return 0

    except VaultError as e:
        print(f"\nError: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nBenchmark interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
