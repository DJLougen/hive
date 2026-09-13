"""Hive-cpp: Native Rust backend for Hive

This package provides Python bindings for the native Rust implementation
of Hive components: Router, Compressor, and Memory.
"""

from .hive_cpp import (
    rust_compress,
    rust_memory_retrieve,
    rust_memory_store,
    rust_router_decide,
)

__all__ = [
    "rust_compress",
    "rust_memory_retrieve",
    "rust_memory_store",
    "rust_router_decide",
]

__version__ = "0.6.1"
