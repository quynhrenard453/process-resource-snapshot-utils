# process-resource-snapshot-utils

A context manager that captures CPU time, max RSS, and open file-descriptor
count before and after a block, and reports the delta.

## Usage

```python
from process_resource_snapshot_utils import ResourceSnapshot

with ResourceSnapshot() as snap:
    # work being attributed goes here
    data = bytearray(2 * 1024 * 1024)
    for i in range(0, len(data), 4096):
        data[i] = 1

r = snap.result
print("cpu_user_delta_s:", r.cpu_user)
print("cpu_sys_delta_s:", r.cpu_sys)
print("ru_maxrss_delta:", r.ru_maxrss_delta)
print("fd_delta:", r.fd_delta, "before:", r.fd_before, "after:", r.fd_after)
```

`ResourceSnapshot` accepts two keyword arguments:

- `clock` — optional callable returning process CPU seconds. The CPU
  deltas reported in `SnapshotResult` come from `resource.getrusage` so
  that user and system time are separated; the `clock` parameter exists
  for symmetry and for tests that want to inject a fake. Passing it does
  not override the getrusage values.
- `children` — when `True`, use `RUSAGE_CHILDREN` instead of `RUSAGE_SELF`,
  so the snapshot reflects waited-for child processes.

The exported names are `ResourceSnapshot` and `SnapshotResult`.

## Why this exists

Attributing resource consumption to a specific block of code needs a
before/after sample of the right counters. The standard library has the
pieces (`resource.getrusage`, `/proc/self/fd`, `time.process_time`) but no
single object that pairs them and computes deltas. This library is that
object, and nothing more.

The deliberate trade-off: CPU time is taken from `getrusage` rather than
`time.process_time` because the brief asks for user and system time as
separate fields, and `getrusage` is the only portable stdlib source that
splits them. Wall-clock time is not captured at all — it is
non-deterministic and out of scope for resource attribution.

## Awkward edges

- `ru_maxrss` units differ by platform: KiB on Linux, bytes on macOS. The
  library does not normalise; `ru_maxrss_delta` is in whatever units the
  platform returns. Call it "native units" and scale in the caller if
  needed.
- On platforms without `/proc/self/fd`, fd counting falls back to a bounded
  scan of `fstat` over `0..min(RLIMIT_NOFILE soft limit, 4096)`. If your
  process has more than 4096 fds open and no `/proc`, the count will
  understate. Linux has `/proc` so this only affects BSDs and macOS with
  unusual fd usage.
- `RUSAGE_CHILDREN` only accumulates after a child has been waited for.
  A snapshot around `os.fork`/`os.waitpid` is meaningful; a snapshot around
  a child that is never reaped is not.
- If the wrapped block raises, the after-snapshot is still taken and
  `result` is populated, but the exception is re-raised (not suppressed).

## Running the tests

```
PYTHONPATH=src python -m unittest discover -s tests
```
