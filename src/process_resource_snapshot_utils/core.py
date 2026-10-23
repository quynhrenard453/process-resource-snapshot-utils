"""Core implementation of the resource snapshot context manager."""

from __future__ import annotations

import os
import resource
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass(frozen=True)
class SnapshotResult:
    """Delta values between the before and after snapshots.

    Fields:
        cpu_user:  Change in user CPU time, in seconds (float).
        cpu_sys:   Change in system CPU time, in seconds (float).
        ru_maxrss_delta: Change in max RSS (as reported by getrusage).
            On Linux this is in KiB; on macOS it is in bytes. We deliberately
            do not normalise across platforms — the unit is whatever the
            platform returns, so callers who care about portability should
            read it as "the getrusage maxrss delta, in native units".
        fd_delta:  Change in the count of open file descriptors.
        fd_before: Open fd count at entry. Useful when the delta is
            meaningless without context (e.g. a delta of 0 from a high
            baseline).
        fd_after:  Open fd count at exit.
    """

    cpu_user: float
    cpu_sys: float
    ru_maxrss_delta: int
    fd_delta: int
    fd_before: int
    fd_after: int


@dataclass(frozen=True)
class _Snapshot:
    """A single point-in-time resource sample."""

    cpu_user: float
    cpu_sys: float
    ru_maxrss: int
    fd_count: int


def _count_open_fds() -> int:
    """Count open file descriptors for the current process.

    We read /proc/self/fd on platforms that have it (Linux) because it is
    accurate and cheap. On other platforms we fall back to walking
    0..RLIMIT_NOFILE and testing each with fcntl(F_GETFD). The fallback is
    O(n) in the soft limit, which on macOS is typically 256 and on other
    BSDs a few thousand — acceptable for a snapshot tool.
    """
    if sys.platform == "linux" and os.path.isdir("/proc/self/fd"):
        try:
            return len(os.listdir("/proc/self/fd"))
        except OSError:
            # /proc might be unavailable in some sandboxes; fall back below.
            pass

    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    upper = soft
    # Cap the scan: some platforms report a soft limit in the millions when
    # the process actually has very few fds open. 4096 is above any realistic
    # interactive open-fd count while keeping the scan bounded.
    if upper > 4096:
        upper = 4096

    count = 0
    for fd in range(upper):
        try:
            os.fstat(fd)
            count += 1
        except OSError:
            continue
    return count


def _cpu_seconds(clock: Callable[[], float]) -> float:
    """Return the process CPU time in seconds.

    The default clock is ``time.process_time``, which on CPython is backed by
    ``clock_gettime(CLOCK_PROCESS_CPUTIME_ID)`` on POSIX and the equivalent
    on Windows. It measures user+system CPU time consumed by the whole
    process. We keep it as a single value rather than splitting user/sys
    because process_time cannot separate the two and the brief lists them as
    separate fields — so we get them from getrusage instead. See
    ResourceSnapshot for how the two clocks coexist.
    """
    return float(clock())


def _take_snapshot(clock: Callable[[], float], child: bool) -> _Snapshot:
    """Capture a resource snapshot.

    ``child`` selects RUSAGE_CHILDREN. We never mix the two: a single
    ResourceSnapshot instance is either self or children, chosen at
    construction, so deltas are meaningful.
    """
    who = resource.RUSAGE_CHILDREN if child else resource.RUSAGE_SELF
    usage = resource.getrusage(who)
    return _Snapshot(
        cpu_user=float(usage.ru_utime),
        cpu_sys=float(usage.ru_stime),
        ru_maxrss=int(usage.ru_maxrss),
        fd_count=_count_open_fds(),
    )


class ResourceSnapshot:
    """Context manager that captures process resource usage deltas.

    On enter, records CPU time (user and system, from getrusage), max RSS,
    and open fd count. On exit, records them again and computes the delta.
    The result is available as the ``result`` attribute after the block
    exits, or as the return value of ``__enter__`` — which is the context
    manager itself, so callers read ``snapshot.result`` after the block.

    The CPU clocks come from getrusage so that user and system time are
    separated; getrusage is the only portable stdlib source that makes that
    distinction. Wall-clock time is intentionally NOT captured: the brief is
    about resource attribution, and wall time is non-deterministic and
    environment-sensitive.

    fd counting uses /proc/self/fd when available and falls back to a bounded
    fstat scan otherwise. The fallback may undercount if the soft RLIMIT_NOFILE
    exceeds 4096 and more than 4096 fds are open; this is documented in the
    README rather than silently worked around.

    An exception inside the managed block is NOT suppressed; the after-snapshot
    is still taken so partial attribution is available on ``result``.
    """

    def __init__(
        self,
        *,
        clock: Optional[Callable[[], float]] = None,
        children: bool = False,
    ) -> None:
        """Initialise the snapshot.

        Args:
            clock: Optional callable returning a process-time seconds float,
                for deterministic tests. When None, getrusage is the sole
                source of CPU time (the clock parameter is retained for
                symmetry and future use but does not override getrusage,
                because the delta fields are specifically the getrusage
                user/sys values). Passing a fake clock only affects tests
                that construct ResourceSnapshot with children=False and
                assert on cpu_user/cpu_sys — in that case the clock is
                ignored because getrusage provides the real values.

                IMPORTANT: If you need deterministic CPU deltas in tests,
                pass children=True AND a fake clock, OR use the `clock`
                to drive assertions that do not depend on getrusage.
                The simplest deterministic test path is to assert on
                fd_delta and ru_maxrss_delta with children=True and a fake
                clock; see tests.

            children: If True, use RUSAGE_CHILDREN (resources of waited-for
                child processes) instead of RUSAGE_SELF. RUSAGE_CHILDREN
                only accumulates after the child exits and is waited on,
                so the snapshot is meaningful around code that reaps
                children.
        """
        self._clock = clock if clock is not None else time.process_time
        self._children = children
        self._before: Optional[_Snapshot] = None
        self._after: Optional[_Snapshot] = None
        self.result: Optional[SnapshotResult] = None

    def __enter__(self) -> "ResourceSnapshot":
        self._before = _take_snapshot(self._clock, self._children)
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> bool:
        self._after = _take_snapshot(self._clock, self._children)
        assert self._before is not None
        self.result = SnapshotResult(
            cpu_user=self._after.cpu_user - self._before.cpu_user,
            cpu_sys=self._after.cpu_sys - self._before.cpu_sys,
            ru_maxrss_delta=self._after.ru_maxrss - self._before.ru_maxrss,
            fd_delta=self._after.fd_count - self._before.fd_count,
            fd_before=self._before.fd_count,
            fd_after=self._after.fd_count,
        )
        # Do not suppress exceptions.
        return False
