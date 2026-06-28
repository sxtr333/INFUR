from __future__ import annotations

import os
import subprocess


def cpu_worker_count(limit_pct: float) -> int:
    capped = min(float(limit_pct), 75.0)
    total = int(os.cpu_count() or 4)
    return max(1, int(total * capped / 100))


def apply_cpu_affinity(limit_pct: float) -> None:
    workers = cpu_worker_count(limit_pct)
    if not shutil_which("taskset"):
        return
    mask = ",".join(str(i) for i in range(workers))
    try:
        subprocess.run(
            ["taskset", "-p", mask, str(os.getpid())],
            check=False,
            capture_output=True,
        )
    except OSError:
        pass


def shutil_which(cmd: str) -> str | None:
    from shutil import which

    return which(cmd)
