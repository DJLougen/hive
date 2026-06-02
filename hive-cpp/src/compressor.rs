//! Context compression for LLM token reduction.
//!
//! Compresses agent context while preserving critical information.
//! Target: 2x compression with <0.1ms latency per message.

use std::time::Instant;
use std::collections::HashMap;

/// Compression rules for different content types.
#[derive(Debug, Clone, Copy)]
pub struct CompressionRules {
    /// Target compression ratio (2.0 = 2x compression)
    pub ratio: f64,
    /// Preserve critical patterns (errors, commands, paths)
    pub preserve_patterns: bool,
}

impl Default for CompressionRules {
    fn default() -> Self {
        Self {
            ratio: 2.0,
            preserve_patterns: true,
        }
    }
}

/// Token representation with metadata.
#[derive(Debug, Clone)]
pub struct Token {
    pub text: String,
    pub importance: f64,
    pub position: usize,
}

impl Token {
    pub fn new(text: &str, position: usize) -> Self {
        let importance = calculate_importance(text);
        Self {
            text: text.to_string(),
            importance,
            position,
        }
    }
}

/// Calculate token importance based on patterns.
fn calculate_importance(text: &str) -> f64 {
    let text_lower = text.to_lowercase();
    
    // High importance patterns
    if text_lower.contains("error") || text_lower.contains("fail") {
        return 3.0;
    }
    if text_lower.contains("warning") {
        return 2.0;
    }
    
    // File paths and commands
    if text.starts_with('/') || text.starts_with("./") || text.contains("::") {
        return 2.5;
    }
    
    // Numbers and code-like patterns
    if text.chars().any(|c| c.is_numeric()) {
        return 1.5;
    }
    
    // Default importance
    1.0
}

/// Result of compression with statistics.
#[derive(Debug, Clone)]
pub struct CompressionResult {
    pub compressed: String,
    pub original_tokens: usize,
    pub compressed_tokens: usize,
    pub ratio: f64,
    pub latency_ms: f64,
}

/// Context compressor with configurable rules.
#[derive(Debug)]
pub struct Compressor {
    rules: CompressionRules,
}

impl Compressor {
    pub fn new(rules: CompressionRules) -> Self {
        Self { rules }
    }

    /// Compress a list of tokens while preserving important information.
    pub fn compress_tokens(&self, tokens: &[&str]) -> CompressionResult {
        let start = Instant::now();
        
        let token_data: Vec<Token> = tokens
            .iter()
            .enumerate()
            .map(|(i, t)| Token::new(t, i))
            .collect();
        
        let total_tokens = token_data.len();
        let target_tokens = (total_tokens as f64 / self.rules.ratio).ceil() as usize;
        
        // Score tokens by importance
        let mut scored: Vec<(usize, f64)> = token_data
            .iter()
            .map(|t| (t.position, t.importance))
            .collect();
        
        // Sort by importance (descending), then by position (ascending)
        scored.sort_by(|a, b| {
            b.1.partial_cmp(&a.1)
                .unwrap()
                .then_with(|| a.0.cmp(&b.0))
        });
        
        // Keep top N tokens
        let keep_positions: Vec<usize> = scored
            .iter()
            .take(target_tokens)
            .map(|(pos, _)| *pos)
            .collect();
        
        // Reconstruct in original order
        let mut kept_positions = keep_positions.clone();
        kept_positions.sort();
        
        let compressed_text: String = kept_positions
            .iter()
            .map(|&pos| tokens[pos])
            .collect::<Vec<_>>()
            .join(" ");
        
        let original_tokens = tokens.len();
        let compressed_tokens = kept_positions.len();
        let ratio = original_tokens as f64 / compressed_tokens as f64;
        let latency_ms = start.elapsed().as_micros() as f64 / 1000.0;
        
        CompressionResult {
            compressed: compressed_text,
            original_tokens,
            compressed_tokens,
            ratio,
            latency_ms,
        }
    }

    /// Compress a full message string.
    pub fn compress_message(&self, message: &str) -> CompressionResult {
        let tokens: Vec<&str> = message.split_whitespace().collect();
        self.compress_tokens(&tokens)
    }

    /// Batch compress multiple messages.
    pub fn batch_compress(&self, messages: &[&str]) -> Vec<CompressionResult> {
        messages
            .iter()
            .map(|msg| self.compress_message(msg))
            .collect()
    }

    /// Get compression statistics.
    pub fn statistics(&self, results: &[CompressionResult]) -> CompressionStats {
        let total_original: usize = results.iter().map(|r| r.original_tokens).sum();
        let total_compressed: usize = results.iter().map(|r| r.compressed_tokens).sum();
        let total_latency: f64 = results.iter().map(|r| r.latency_ms).sum();
        
        let avg_ratio = total_original as f64 / total_compressed as f64;
        let avg_latency = total_latency / results.len() as f64;
        
        CompressionStats {
            total_messages: results.len(),
            total_original_tokens: total_original,
            total_compressed_tokens: total_compressed,
            avg_compression_ratio: avg_ratio,
            avg_latency_ms: avg_latency,
        }
    }
}

/// Batch compression statistics.
#[derive(Debug, Clone)]
pub struct CompressionStats {
    pub total_messages: usize,
    pub total_original_tokens: usize,
    pub total_compressed_tokens: usize,
    pub avg_compression_ratio: f64,
    pub avg_latency_ms: f64,
}

impl CompressionStats {
    /// Summarize stats as key-value pairs.
    pub fn summary(&self) -> HashMap<String, String> {
        let mut map = HashMap::new();
        map.insert("total_messages".to_string(), self.total_messages.to_string());
        map.insert("total_original_tokens".to_string(), self.total_original_tokens.to_string());
        map.insert("total_compressed_tokens".to_string(), self.total_compressed_tokens.to_string());
        map.insert("avg_compression_ratio".to_string(), format!("{:.2}", self.avg_compression_ratio));
        map.insert("avg_latency_ms".to_string(), format!("{:.3}", self.avg_latency_ms));
        map
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_compression() {
        let rules = CompressionRules {
            ratio: 2.0,
            preserve_patterns: true,
        };
        let compressor = Compressor::new(rules);
        
        let message = "This is a test message that should be compressed to roughly half its original length while preserving important information";
        let result = compressor.compress_message(message);
        
        assert_eq!(result.original_tokens, 19);
        assert!(result.compressed_tokens <= 10); // Should be ~half
        assert!(result.ratio >= 1.5);
        assert!(result.latency_ms < 0.1); // Target: <0.1ms
    }

    #[test]
    fn test_importance_scoring() {
        let error_text = "Error: connection failed";
        let normal_text = "processing request";
        
        let error_imp = calculate_importance(error_text);
        let normal_imp = calculate_importance(normal_text);
        
        assert!(error_imp > normal_imp);
    }

    #[test]
    fn test_pattern_preservation() {
        let rules = CompressionRules {
            ratio: 2.0,
            preserve_patterns: true,
        };
        let compressor = Compressor::new(rules);
        
        let msg = "Error occurred at /path/to/file.cpp line 42 causing system::failure";
        let result = compressor.compress_message(msg);
        
        // Should preserve error keywords and paths
        assert!(result.compressed.contains("Error") || result.compressed.contains("error"));
    }

    #[test]
    fn test_batch_compression() {
        let rules = CompressionRules::default();
        let compressor = Compressor::new(rules);
        
        let messages = vec![
            "First message with some content",
            "Second message with error detected",
            "Third message with path /test/file.rs",
        ];
        
        let results = compressor.batch_compress(&messages);
        assert_eq!(results.len(), 3);
        
        let stats = compressor.statistics(&results);
        assert_eq!(stats.total_messages, 3);
        assert!(stats.avg_compression_ratio >= 1.5);
    }
}
