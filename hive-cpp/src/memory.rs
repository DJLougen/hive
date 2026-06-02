//! Causal memory graph for agent memory management.
//!
//! Provides a graph-based memory system with:
//! - Hash-based keys for O(1) lookups
//! - Causal chains (what led to what)
//! - Memory decay and importance scoring
//! - Thread-safe concurrent access

use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};


/// Unique identifier for memory nodes.
pub type MemoryKey = u64;

/// Causal relationship types between memory nodes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CausalRelation {
    /// This memory was caused by another memory
    CausedBy,
    /// This memory refines or builds on another
    Refines,
    /// This memory contradicts supersedes another
    Supersedes,
}

/// A memory node with metadata.
#[derive(Debug, Clone)]
pub struct MemoryNode {
    pub key: MemoryKey,
    pub content: String,
    pub timestamp: Instant,
    pub importance: f64,
    pub causal_relations: Vec<(MemoryKey, CausalRelation)>,
}

impl MemoryNode {
    pub fn new(key: MemoryKey, content: String, importance: f64) -> Self {
        Self {
            key,
            content,
            timestamp: Instant::now(),
            importance,
            causal_relations: Vec::new(),
        }
    }

    /// Check if this memory is stale (older than threshold).
    pub fn is_stale(&self, threshold: Duration) -> bool {
        self.timestamp.elapsed() > threshold
    }

    /// Calculate age in seconds.
    pub fn age_seconds(&self) -> f64 {
        self.timestamp.elapsed().as_secs_f64()
    }

    /// Add a causal relation to another memory.
    pub fn add_relation(&mut self, target_key: MemoryKey, relation: CausalRelation) {
        self.causal_relations.push((target_key, relation));
    }
}

/// Result of a memory retrieval operation.
#[derive(Debug, Clone)]
pub struct RetrievalResult {
    pub node: MemoryNode,
    pub score: f64,
    pub causal_chain: Vec<MemoryKey>,
}

/// Concurrent memory graph with thread-safe access.
pub struct MemoryGraph {
    nodes: Arc<RwLock<HashMap<MemoryKey, MemoryNode>>>,
    max_capacity: usize,
    decay_rate: f64, // Score decay per second
}

impl MemoryGraph {
    pub fn new(max_capacity: usize, decay_rate: f64) -> Self {
        Self {
            nodes: Arc::new(RwLock::new(HashMap::new())),
            max_capacity,
            decay_rate,
        }
    }

    /// Store a new memory node.
    pub fn store(&self, key: MemoryKey, content: String, importance: f64) -> bool {
        let mut nodes = self.nodes.write().unwrap();
        
        // Evict lowest importance if at capacity
        if nodes.len() >= self.max_capacity {
            let min_key = nodes
                .iter()
                .min_by(|a, b| {
                    a.1.importance.partial_cmp(&b.1.importance).unwrap()
                })
                .map(|(k, _)| *k);
            
            if let Some(min_key) = min_key {
                nodes.remove(&min_key);
            }
        }
        
        let node = MemoryNode::new(key, content, importance);
        nodes.insert(key, node);
        true
    }

    /// Retrieve a memory node by key.
    pub fn retrieve(&self, key: MemoryKey) -> Option<MemoryNode> {
        let nodes = self.nodes.read().unwrap();
        nodes.get(&key).cloned()
    }

    /// Search for memories matching a query (simple substring match).
    pub fn search(&self, query: &str, limit: usize) -> Vec<RetrievalResult> {
        let nodes = self.nodes.read().unwrap();
        let now = Instant::now();
        
        let mut results: Vec<RetrievalResult> = nodes
            .values()
            .filter(|node| {
                query.is_empty() || node.content.to_lowercase().contains(&query.to_lowercase())
            })
            .map(|node| {
                let age = now.duration_since(node.timestamp).as_secs_f64();
                let decay = (-self.decay_rate * age).exp();
                let score = node.importance * decay;
                
                RetrievalResult {
                    node: node.clone(),
                    score,
                    causal_chain: node.causal_relations.iter().map(|(k, _)| *k).collect(),
                }
            })
            .collect();
        
        results.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap());
        results.truncate(limit);
        results
    }

    /// Add a causal relation between two memories.
    pub fn add_causal_relation(&self, from_key: MemoryKey, to_key: MemoryKey, relation: CausalRelation) -> bool {
        let mut nodes = self.nodes.write().unwrap();
        if let Some(node) = nodes.get_mut(&from_key) {
            node.add_relation(to_key, relation);
            true
        } else {
            false
        }
    }

    /// Get statistics about the memory graph.
    pub fn statistics(&self) -> MemoryStats {
        let nodes = self.nodes.read().unwrap();
        
        MemoryStats {
            total_nodes: nodes.len(),
            max_capacity: self.max_capacity,
            avg_importance: nodes.values().map(|n| n.importance).sum::<f64>() / nodes.len() as f64,
            causal_relations: nodes.values().map(|n| n.causal_relations.len()).sum(),
        }
    }

    /// Garbage collect stale memories.
    pub fn gc_stale(&self, threshold: Duration) -> usize {
        let now = Instant::now();
        let stale_keys: Vec<MemoryKey> = self
            .nodes
            .read()
            .unwrap()
            .iter()
            .filter(|(_, node)| now.duration_since(node.timestamp) > threshold)
            .map(|(k, _)| *k)
            .collect();
        
        let count = stale_keys.len();
        let mut nodes = self.nodes.write().unwrap();
        for key in stale_keys {
            nodes.remove(&key);
        }
        count
    }
}

/// Memory graph statistics.
#[derive(Debug, Clone)]
pub struct MemoryStats {
    pub total_nodes: usize,
    pub max_capacity: usize,
    pub avg_importance: f64,
    pub causal_relations: usize,
}

impl MemoryStats {
    pub fn summary(&self) -> HashMap<String, String> {
        let mut map = HashMap::new();
        map.insert("total_nodes".to_string(), self.total_nodes.to_string());
        map.insert("max_capacity".to_string(), self.max_capacity.to_string());
        map.insert("avg_importance".to_string(), format!("{:.2}", self.avg_importance));
        map.insert("causal_relations".to_string(), self.causal_relations.to_string());
        map
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_store_retrieve() {
        let graph = MemoryGraph::new(100, 0.1);
        
        graph.store(1, "Test memory".to_string(), 1.0);
        let node = graph.retrieve(1).unwrap();
        
        assert_eq!(node.content, "Test memory");
        assert_eq!(node.importance, 1.0);
    }

    #[test]
    fn test_memory_staleness() {
        let graph = MemoryGraph::new(100, 0.01);
        graph.store(1, "Test".to_string(), 1.0);
        
        let node = graph.retrieve(1).unwrap();
        assert!(!node.is_stale(Duration::from_secs(1)));
    }

    #[test]
    fn test_search() {
        let graph = MemoryGraph::new(100, 0.01);
        graph.store(1, "Error: connection failed".to_string(), 1.0);
        graph.store(2, "Success: request completed".to_string(), 0.5);
        
        let results = graph.search("error", 10);
        assert_eq!(results.len(), 1);
        assert!(results[0].node.content.contains("Error"));
    }

    #[test]
    fn test_eviction() {
        let graph = MemoryGraph::new(2, 0.01);
        
        graph.store(1, "Low importance".to_string(), 0.5);
        graph.store(2, "Medium".to_string(), 0.8);
        graph.store(3, "High importance".to_string(), 1.0);
        
        // Should have evicted the lowest importance node
        assert!(graph.retrieve(1).is_none());
        assert!(graph.retrieve(2).is_some());
        assert!(graph.retrieve(3).is_some());
    }

    #[test]
    fn test_causal_relations() {
        let graph = MemoryGraph::new(100, 0.01);
        graph.store(1, "Cause".to_string(), 1.0);
        graph.store(2, "Effect".to_string(), 1.0);
        
        graph.add_causal_relation(2, 1, CausalRelation::CausedBy);
        
        let node = graph.retrieve(2).unwrap();
        assert_eq!(node.causal_relations.len(), 1);
        assert_eq!(node.causal_relations[0].0, 1);
        assert_eq!(node.causal_relations[0].1, CausalRelation::CausedBy);
    }
}
