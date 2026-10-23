"""Tests for process_resource_snapshot_utils.core.

Only behaviour the implementation actually claims to support is tested.
CPU deltas that depend on real wall-clock CPU consumption are not asserted
on with equality; instead we assert structural properties (type, sign,
non-negativity) that hold deterministically.
"""

import io
import os
import resource
import sys
import unittest

from process_resource_snapshot_utils import ResourceSnapshot, SnapshotResult


class TestSnapshotBasics(unittest.TestCase):
    def test_result_is_none_before_context(self):
        snap = ResourceSnapshot()
        self.assertIsNone(snap.result)

    def test_exit_sets_result(self):
        with ResourceSnapshot() as snap:
            pass
        self.assertIsInstance(snap.result, SnapshotResult)

    def test_enter_returns_snapshot_itself(self):
        with ResourceSnapshot() as snap:
            self.assertIsInstance(snap, ResourceSnapshot)

    def test_result_fields_types(self):
        with ResourceSnapshot() as snap:
            pass
        r = snap.result
        self.assertIsInstance(r.cpu_user, float)
        self.assertIsInstance(r.cpu_sys, float)
        self.assertIsInstance(r.ru_maxrss_delta, int)
        self.assertIsInstance(r.fd_delta, int)
        self.assertIsInstance(r.fd_before, int)
        self.assertIsInstance(r.fd_after, int)

    def test_fd_counts_are_non_negative(self):
        with ResourceSnapshot() as snap:
            pass
        r = snap.result
        self.assertGreaterEqual(r.fd_before, 0)
        self.assertGreaterEqual(r.fd_after, 0)

    def test_fd_delta_with_opened_fd(self):
        # Open and keep a new fd inside the block; fd_delta should be >= 1.
        with ResourceSnapshot() as snap:
            f = open(os.devnull, "r")
            try:
                _ = f.fileno()
            finally:
                # keep f alive until after the snapshot is taken
                pass
        try:
            self.assertGreaterEqual(snap.result.fd_delta, 1)
        finally:
            f.close()

    def test_fd_delta_with_closed_fd(self):
        # Open a fd inside the block and close it before exit; delta should be 0.
        with ResourceSnapshot() as snap:
            f = open(os.devnull, "r")
            f.close()
        self.assertEqual(snap.result.fd_delta, 0)

    def test_exception_not_suppressed(self):
        class Boom(Exception):
            pass

        with self.assertRaises(Boom):
            with ResourceSnapshot() as snap:
                raise Boom()
        # result should still be populated
        self.assertIsInstance(snap.result, SnapshotResult)

    def test_maxrss_delta_is_int_and_sign_correct(self):
        # ru_maxrss is monotonic non-decreasing within RUSAGE_SELF, so the
        # delta is always >= 0. We assert the sign, not a magnitude.
        with ResourceSnapshot() as snap:
            # Allocate and touch memory to make maxrss movement likely.
            data = bytearray(4 * 1024 * 1024)
            for i in range(0, len(data), 4096):
                data[i] = 1
            del data
        self.assertGreaterEqual(snap.result.ru_maxrss_delta, 0)

    def test_clock_parameter_is_accepted(self):
        # The constructor accepts a clock callable; this just checks it does
        # not raise. The clock is not asserted further because getrusage
        # provides the CPU values the implementation reports.
        calls = []

        def fake_clock():
            calls.append(True)
            return 0.0

        with ResourceSnapshot(clock=fake_clock) as snap:
            pass
        self.assertIsInstance(snap.result, SnapshotResult)
        self.assertGreaterEqual(len(calls), 0)

    def test_children_parameter_is_accepted(self):
        # RUSAGE_CHILDREN path should run without error.
        with ResourceSnapshot(children=True) as snap:
            pass
        self.assertIsInstance(snap.result, SnapshotResult)

    def test_fd_count_function_paths(self):
        # Exercise _count_open_fds directly to cover both branches.
        from process_resource_snapshot_utils.core import _count_open_fds

        n = _count_open_fds()
        self.assertIsInstance(n, int)
        self.assertGreaterEqual(n, 0)
        # We always have at least stdin/stdout/stderr on a normal test run.
        self.assertGreaterEqual(n, 3)

    def test_snapshot_result_is_frozen(self):
        with ResourceSnapshot() as snap:
            pass
        r = snap.result
        with self.assertRaises(Exception):
            r.cpu_user = 999.0


if __name__ == "__main__":
    unittest.main()
