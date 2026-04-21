"""Isolated ML sidecar for AIRAHOST.

This package trains from Supabase raw market observations without mutating
the live pricing pipeline.
"""

__all__ = [
    "batch_pipeline",
    "data",
    "model",
    "supabase_client",
]
