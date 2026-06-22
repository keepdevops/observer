"""Versioned message contracts for the observer bus.

Each module groups the envelopes for one capability (registry, resource, lifecycle, data,
tools, meta). `base.py` holds the version stamp shared by every envelope. The exported JSON
Schema (see `bus/schema_export.py`) is the single cross-repo, cross-language contract.
"""
