"""
Optional worker processes for the rendering loops (`audio.synthesize`,
`soundfont.build_soundfont`).

A `jobs` argument is 1 (everything runs in this process and no pool is made),
N > 1 for up to N worker processes, or None for one per CPU this process may
use. Where processes aren't available (Pyodide, WASI) every `jobs` runs in this
process, exactly as with 1.
"""

import logging
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)


def processes_available() -> bool:
    """Whether worker processes can be started here."""
    if sys.platform in ("emscripten", "wasi"):
        return False
    try:
        # Needs working semaphores, which Pyodide and some sandboxes lack
        import multiprocessing.synchronize  # noqa: F401
    except ImportError:
        return False
    return True


def check_jobs(jobs: int | None) -> None:
    """:raises ValueError: If `jobs` isn't None or at least 1."""
    if jobs is not None and jobs < 1:
        raise ValueError(
            f"jobs must be at least 1 (or None for one per CPU), got {jobs}"
        )


def resolve_jobs(jobs: int | None, tasks: int) -> int:
    """
    How many worker processes to use for `tasks` independent tasks: 1 means run
    in this process, with no pool.

    :raises ValueError: If `jobs` isn't None or at least 1.
    """
    check_jobs(jobs)
    if jobs == 1:
        return 1
    if not processes_available():
        logger.debug("Worker processes aren't available here; rendering serially")
        return 1
    if jobs is None:
        jobs = os.process_cpu_count() or 1
    return max(1, min(jobs, tasks))


def run_in_processes[R](
    func: Callable[..., R],
    tasks: Sequence[tuple[Any, ...]],
    workers: int,
    on_done: Callable[[int, R], None],
) -> None:
    """
    Run ``func(*task)`` for every task in up to `workers` processes, calling
    ``on_done(index, result)`` here, in completion order, as each finishes.
    `func` and the tasks must be picklable. The first exception is re-raised
    after cancelling the tasks that haven't started.
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    logger.info(f"Using {workers} worker processes")
    executor = ProcessPoolExecutor(max_workers=workers)
    try:
        futures = {executor.submit(func, *task): i for i, task in enumerate(tasks)}
        for future in as_completed(futures):
            on_done(futures[future], future.result())
    finally:
        executor.shutdown(cancel_futures=True)
