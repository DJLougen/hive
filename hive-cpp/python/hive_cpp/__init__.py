"""Hive-cpp: Native Rust backend for Hive

This package provides Python bindings for the native Rust implementation
of Hive components: Router, Compressor, and Memory.
"""

from .hive_cpp import (
    rust_router_decide,
    rust_compress,
    rust_memory_store,
    rust_memory_retrieve,
)

__all__ = [
    "rust_router_decide",
    "rust_compress",
    "rust_memory_store",
    "rust_memory_retrieve",
]

__version__ = "0.6.1"
