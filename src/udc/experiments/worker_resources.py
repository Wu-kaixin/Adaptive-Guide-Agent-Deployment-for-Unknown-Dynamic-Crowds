"""One numerical thread and one distinct allowed CPU per process-pool worker."""
import multiprocessing

import psutil


def initialize_worker():
    process = psutil.Process()
    allowed = process.cpu_affinity()
    identity = multiprocessing.current_process()._identity
    slot = (identity[-1] - 1) if identity else 0
    process.cpu_affinity([allowed[slot % len(allowed)]])
    if hasattr(psutil, 'NORMAL_PRIORITY_CLASS'):
        process.nice(psutil.NORMAL_PRIORITY_CLASS)
