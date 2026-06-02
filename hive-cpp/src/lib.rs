//! Hive-cpp: Native Rust backend for Hive
//!
//! This crate provides a 100x speedup over the Python implementation for
//! agent context compression and routing decisions.
//!
//! # Architecture
//!
//! - **Router**: Native port of busybee-cpu decision tree
//! - **Compressor**: Native port of honey-comb compression
//! - **Memory**: Lock-free concurrent hash map for agent memory
//!
//! # Performance Targets
//!
//! | Component | Python | Rust Target | Speedup |
//! |-----------|--------|-------------|---------|
//! | Router | ~100ms | <0.1ms | **100x** |
//! | Compressor | ~0.1ms | <0.1ms | **5-10x** |
//! | Memory | ~0.01ms | <0.01ms | **5-10x** |
//! | **End-to-End** | ~100ms | <0.2ms | **50-100x** |

pub mod router;
pub mod compressor;
pub mod memory;

// Re-export main types
pub use router::{Router, RouterModel, AgentState, Decision, DecisionTreeNode};
pub use compressor::{Compressor, CompressionRules, CompressionResult, CompressionStats};
pub use memory::{MemoryGraph, MemoryNode, MemoryStats, CausalRelation, MemoryKey};
pub use memory::RetrievalResult;

#[cfg(feature = "pyo3")]
use pyo3::prelude::*;
#[cfg(feature = "pyo3")]
use pyo3::wrap_pyfunction;
#[cfg(feature = "pyo3")]
use pyo3::exceptions::PyValueError;
#[cfg(feature = "pyo3")]
use std::sync::OnceLock;

#[cfg(feature = "pyo3")]
static MEMORY: OnceLock<MemoryGraph> = OnceLock::new();

#[cfg(feature = "pyo3")]
fn get_memory() -> &'static MemoryGraph {
    MEMORY.get_or_init(|| MemoryGraph::new(10_000, 0.01))
}

#[cfg(feature = "pyo3")]
#[pyfunction]
fn rust_router_decide(model_json: &str, state_json: &str) -> PyResult<String> {
    let model: RouterModel = serde_json::from_str(model_json)
        .map_err(|e| PyValueError::new_err(format!("Failed to parse model: {}", e)))?;
    let state: AgentState = serde_json::from_str(state_json)
        .map_err(|e| PyValueError::new_err(format!("Failed to parse state: {}", e)))?;

    let router = Router::new(model);
    let decision = router.decide(&state);

    serde_json::to_string(&decision)
        .map_err(|e| PyValueError::new_err(format!("Failed to serialize decision: {}", e)))
}

#[cfg(feature = "pyo3")]
#[pyfunction]
fn rust_compress(text: &str) -> PyResult<String> {
    let rules = CompressionRules::default();
    let compressor = Compressor::new(rules);
    let result = compressor.compress_message(text);

    // Build JSON manually since CompressionResult doesn't derive Serialize
    let json = serde_json::json!({
        "compressed": result.compressed,
        "original_tokens": result.original_tokens,
        "compressed_tokens": result.compressed_tokens,
        "ratio": result.ratio,
        "latency_ms": result.latency_ms,
    });
    Ok(json.to_string())
}

#[cfg(feature = "pyo3")]
#[pyfunction]
fn rust_memory_store(key: u64, value: &str, importance: f64) -> PyResult<bool> {
    let graph = get_memory();
    Ok(graph.store(key, value.to_string(), importance))
}

#[cfg(feature = "pyo3")]
#[pyfunction]
fn rust_memory_retrieve(key: u64) -> PyResult<String> {
    use pyo3::exceptions::PyKeyError;
    let graph = get_memory();

    let node = graph.retrieve(key)
        .ok_or_else(|| PyKeyError::new_err(format!("Key {} not found", key)))?;

    // Build JSON manually since MemoryNode doesn't derive Serialize (has Instant)
    let json = serde_json::json!({
        "key": node.key,
        "content": node.content,
        "importance": node.importance,
        "age_seconds": node.age_seconds(),
    });
    Ok(json.to_string())
}

#[cfg(feature = "pyo3")]
#[pymodule]
fn hive_cpp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(rust_router_decide, m)?)?;
    m.add_function(wrap_pyfunction!(rust_compress, m)?)?;
    m.add_function(wrap_pyfunction!(rust_memory_store, m)?)?;
    m.add_function(wrap_pyfunction!(rust_memory_retrieve, m)?)?;
    Ok(())
}
