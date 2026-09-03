"""Benchmark 8-page parse time and count BeautifulSoup constructions.

Usage:
    python scripts/benchmark_8pages.py
"""

import os
import sys
import time
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from app.services.parser import ReadabilityParser

FIXTURES = [
    ("p1", "p1.html", "https://www.funq.jp/randonnee/article/1068247/"),
    ("p2", "p2.html", "https://hobby.watch.impress.co.jp/docs/news/2112227.html"),
    ("p3", "p3.html", "https://internet.watch.impress.co.jp/docs/ranking/mousepro/2111089.html"),
    ("p4", "p4.html", "https://lovewalker.jp/elem/000/004/385/4385643/"),
    ("p5", "p5.html", "https://kaelife.hondaaccess.jp/entry/20260526_01"),
    ("p6", "p6.html", "https://transit.jp/sponsored/newzealandairline_christchurch/"),
    ("p7", "p7.html", "https://kaden.watch.impress.co.jp/docs/news/2112195.html"),
    ("p8", "p8.html", "https://tabiiro.jp/likes/articles/view/2409/"),
]

BENCH_DIR = os.path.join(HERE, "tests", "fixtures", "benchmark_pages")


def run_benchmark():
    pages = []
    for pid, fname, url in FIXTURES:
        fpath = os.path.join(BENCH_DIR, fname)
        if not os.path.exists(fpath):
            print(f"Missing fixture {fpath}")
            return
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            html = f.read()
        kb = len(html.encode("utf-8")) // 1024
        pages.append((pid, kb, url, html))

    # Instrument BeautifulSoup construction counter
    orig_init = BeautifulSoup.__init__
    bs_count = 0

    def counted_init(self, *args, **kwargs):
        nonlocal bs_count
        bs_count += 1
        return orig_init(self, *args, **kwargs)

    BeautifulSoup.__init__ = counted_init

    parser = ReadabilityParser()

    # Warmup
    for pid, kb, url, html in pages:
        parser.extract_with_images(html, base_url=url)

    # Measurement run
    per_page_times = []
    per_page_bs = []

    print(f"{'Page':<5} {'Size':<8} {'Time (ms)':<12} {'BeautifulSoup Calls':<20}")
    print("-" * 50)

    for pid, kb, url, html in pages:
        bs_before = bs_count
        # Run 5 iterations per page and take average
        iter_times = []
        for _ in range(5):
            t0 = time.perf_counter()
            parser.extract_with_images(html, base_url=url)
            dt = (time.perf_counter() - t0) * 1000
            iter_times.append(dt)

        bs_per_single_parse = (bs_count - bs_before) // 5
        avg_time = sum(iter_times) / len(iter_times)
        per_page_times.append(avg_time)
        per_page_bs.append(bs_per_single_parse)

        print(f"{pid:<5} {kb:<4} KB   {avg_time:>6.1f} ms    {bs_per_single_parse:>6d} constructions")

    total_time = sum(per_page_times)
    avg_time = total_time / len(per_page_times)
    total_bs = sum(per_page_bs)
    print("-" * 50)
    print(f"TOTAL: {total_time:.1f} ms / {len(pages)} pages = {avg_time:.1f} ms average")
    print(f"TOTAL BeautifulSoup constructions per full 8-page run: {total_bs}")


if __name__ == "__main__":
    run_benchmark()
