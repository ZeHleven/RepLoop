"""Explicit eval budget clock. Never modifies application or system clocks."""
import time

DEFAULT_SOURCE = 'CLOCK_MONOTONIC_RAW' if hasattr(time, 'CLOCK_MONOTONIC_RAW') else 'monotonic'

def counter(source):
    if source == 'monotonic':
        return time.monotonic
    if source == 'CLOCK_MONOTONIC_RAW' and hasattr(time, 'CLOCK_MONOTONIC_RAW'):
        return lambda: time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
    raise ValueError('Unavailable execution clock source: '+str(source))
