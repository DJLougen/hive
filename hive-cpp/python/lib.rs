//! PyO3 bindings for hive-cpp
//!
//! This module provides Python bindings for the native Rust backend.

use pyo3::prelude::*;
use pyo3::wrap_pyfunction;
use hive_cpp::{Router, RouterModel, AgentState, Compressor, Memory, MemoryKey};

/// Python wrapper for Rust Router
#[pyclass(name = "NativeRouter")]
struct PyRouter {
    router: Router,
}

#[pymethods]
impl PyRouter {
    #[new]
    fn new(threshold: f64) -> PyResult<Self> {
        let model = RouterModel::default();
        let router = Router::new(model, threshold);
        Ok(Self { router })
    }

    #[classmethod]
    fn from_json(_cls: &Bound<'_, PyType>, json_str: &str, threshold: f64) -> PyResult<Self> {
        let model: RouterModel = serde_json::from_str(json_str)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("Invalid JSON: {}", e)))?;
        let router = Router::new(model, threshold);
        Ok(Self { router })
    }

    fn decide(&self, state: &str) -> PyResult<String> {
        let agent_state: AgentState = serde_json::from_str(state)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("Invalid state JSON: {}", e)))?;
        let decision = self.router.decide(&agent_state);
        serde_json::to_string(&decision)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("Serialization error: {}", e)))
    }
}

/// Python wrapper for Rust Compressor
#[pyclass(name = "NativeCompressor")]
struct PyCompressor {
    compressor: Compressor,
}

#[pymethods]
impl PyCompressor {
    #[new]
    fn new(min_length: usize, threshold: f64) -> PyResult<Self> {
        let compressor = Compressor::new(min_length as i32, threshold);
        Ok(Self { compressor })
    }

    fn should_compress(&self, context: &str) -> bool {
        self.compressor.should_compress(context)
    }
}

/// Python wrapper for Rust Memory
#[pyclass(name = "NativeMemory")]
struct PyMemory {
    memory: std::sync::Arc<std::sync::Mutex<Memory>>,
}

#[pymethods]
impl PyMemory {
    #[new]
    fn new(capacity: usize) -> PyResult<Self> {
        let memory = Memory::new(capacity);
        Ok(Self {
            memory: std::sync::Arc::new(std::sync::Mutex::new(memory)),
        })
    }

    fn get(&self, key: &str) -> PyResult<Option<(String, f64)>> {
        let mem = self.memory.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;
        
        // Create a MemoryKey from the string
        let mut key_data = [0u64; 4];
        for (i, byte) in key.bytes().take(32).enumerate() {
            key_data[i / 8] |= (byte as u64) << ((i % 8) * 8);
        }
        let mk = MemoryKey { data: key_data };
        
        if let Some((tokens, score)) = mem.get(&mk) {
            Ok(Some((tokens.to_string(), score)))
        } else {
            Ok(None)
        }
    }

    fn insert(&self, key: &str, tokens: &str, score: f64) -> PyResult<()> {
        let mut mem = self.memory.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;
        
        // Create a MemoryKey from the string
        let mut key_data = [0u64; 4];
        for (i, byte) in key.bytes().take(32).enumerate() {
            key_data[i / 8] |= (byte as u64) << ((i % 8) * 8);
        }
        let mk = MemoryKey { data: key_data };
        
        mem.insert(&mk, tokens, score);
        Ok(())
    }

    fn search(&self, query: &str, k: usize) -> PyResult<Vec<(String, String, f64)>> {
        let mem = self.memory.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;
        
        let results = mem.search(query, k);
        let py_results: Vec<(String, String, f64)> = results
            .into_iter()
            .map(|(key, tokens, score)| {
                // Convert MemoryKey back to string (lossy)
                let mut key_str = String::new();
                for &val in &key.data {
                    for i in 0..8 {
                        let byte = (val >> (i * 8)) as u8;
                        if byte != 0 {
                            key_str.push(byte as char);
                        }
                    }
                }
                (key_str, tokens.to_string(), score)
            })
            .collect();
        
        Ok(py_results)
    }

    fn stats(&self) -> PyResult<(usize, usize)> {
        let mem = self.memory.lock().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Lock error: {}", e))
        })?;
        let stats = mem.stats();
        Ok((stats.len, stats.capacity))
    }
}

/// Module initialization
#[pymodule]
fn hive_cpp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyRouter>()?;
    m.add_class::<PyCompressor>()?;
    m.add_class::<PyMemory>()?;
    Ok(())
}
