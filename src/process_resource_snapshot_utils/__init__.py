"""process-resource-snapshot-utils.

A context manager that captures CPU time, memory blocks, and open fd
counts before and after a block, computing the delta for resource
attribution.
"""

from .core import ResourceSnapshot, SnapshotResult

__all__ = ["ResourceSnapshot", "SnapshotResult"]
