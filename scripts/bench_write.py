#!/usr/bin/env python3
"""Opt-in write-scaling benchmark: ms per remember() as the store grows.

    python3 scripts/bench_write.py [--sizes 1000 3000 10000 20000] [--profile-last 1000]

Inserts N distinct, word-overlapping decision-like facts through encode.remember() into a
temp on-disk store (no daemon) and prints ms/write for each band between sizes, then a
cProfile of the last `--profile-last` writes. A bounded per-write cost shows as a flat
ms/write column.
"""
import argparse
import cProfile
import io
import os
import pstats
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from engram import encode  # noqa: E402
from engram.cortex import Cortex  # noqa: E402

TECH = ["PostgreSQL", "Redis", "Kafka", "RabbitMQ", "gRPC", "GraphQL", "Terraform", "Docker",
        "Kubernetes", "SQLite", "Nginx", "Celery", "Prometheus", "Grafana", "Vault"]
SVC = ["billing", "auth", "search", "ingest", "export", "gateway", "notifier", "scheduler",
       "reports", "catalog", "orders", "inventory", "payments", "profile", "audit"]
WHY = ["it keeps the deployment simple", "the team already knows it", "latency matters more",
       "we need transactional guarantees", "the license is permissive", "it scales horizontally",
       "operations asked for it", "it reduces cost", "the vendor support is good",
       "we need strict ordering"]


def facts(n, seed=1):
    """n distinct facts built from a small shared vocabulary (heavy word overlap)."""
    rnd = random.Random(seed)
    for i in range(n):
        yield ("We decided to use %s for the %s service because %s (ticket %d, port %d)."
               % (rnd.choice(TECH), rnd.choice(SVC), rnd.choice(WHY), i, 3000 + rnd.randrange(5000)))


def run(sizes, profile_last=0, out=None, path=None):
    """Insert up to max(sizes) facts; return [(from_n, to_n, ms_per_write, created)] per band."""
    cleanup = None
    if path is None:
        cleanup = tempfile.TemporaryDirectory()
        path = os.path.join(cleanup.name, "engram.db")
    cx = Cortex(path)
    sizes = sorted(set(sizes))
    total = sizes[-1]
    bands, prof, created = [], None, 0
    band_start, t0, lo = 0, time.perf_counter(), 0
    try:
        for i, text in enumerate(facts(total)):
            if profile_last and i == total - profile_last:
                prof = cProfile.Profile()
                prof.enable()
            r = encode.remember(cx, text)
            created += r["status"] == "created"
            if i + 1 in sizes:
                dt = time.perf_counter() - t0
                bands.append((lo, i + 1, 1000.0 * dt / (i + 1 - lo), created - band_start))
                if out:
                    print("%6d -> %6d: %7.2f ms/write  (%d created)" % bands[-1],
                          file=out, flush=True)
                lo, band_start, t0 = i + 1, created, time.perf_counter()
        if prof:
            prof.disable()
            s = io.StringIO()
            pstats.Stats(prof, stream=s).sort_stats("cumulative").print_stats(18)
            if out:
                print("\ncProfile of the last %d writes:\n%s" % (profile_last, s.getvalue()), file=out)
    finally:
        cx.close()
        if cleanup:
            cleanup.cleanup()
    return bands


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sizes", type=int, nargs="+", default=[1000, 3000, 10000, 20000])
    ap.add_argument("--profile-last", type=int, default=1000)
    a = ap.parse_args(argv)
    run(a.sizes, a.profile_last, out=sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
