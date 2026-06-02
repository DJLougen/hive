//! Native Rust implementation of router (busybee-cpu port).
//!
//! Implements a decision tree classifier for tool selection based on
//! SWE-Bench style state features. Targets <0.1ms per decision.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DecisionTreeNode {
    pub feature: Option<String>,
    pub threshold: Option<f64>,
    pub left: Option<Box<DecisionTreeNode>>,
    pub right: Option<Box<DecisionTreeNode>>,
    pub action: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RouterModel {
    pub root: DecisionTreeNode,
    pub feature_names: Vec<String>,
    pub tool_names: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentState {
    pub goal: String,
    pub step: u32,
    pub last_tool: Option<String>,
    pub recent_observations: Vec<String>,
    pub open_files: Vec<String>,
    pub available_tools: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Decision {
    pub action: String,
    pub confidence: f64,
    pub reasoning: String,
    pub latency_ms: f64,
}

pub struct Router {
    model: RouterModel,
}

impl Router {
    pub fn new(model: RouterModel) -> Self {
        Self { model }
    }

    pub fn load(path: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let data = std::fs::read_to_string(path)?;
        let model: RouterModel = serde_json::from_str(&data)?;
        Ok(Self::new(model))
    }

    pub fn extract_features(&self, state: &AgentState) -> HashMap<String, f64> {
        let mut features = HashMap::new();
        
        // Goal length (proxy for complexity)
        features.insert("goal_length".to_string(), state.goal.len() as f64);
        
        // Step number
        features.insert("step".to_string(), state.step as f64);
        
        // Last tool one-hot encoding
        for tool in &self.model.tool_names {
            let key = format!("last_tool_{}", tool);
            features.insert(
                key,
                if state.last_tool.as_deref() == Some(tool) { 1.0 } else { 0.0 },
            );
        }
        
        // Observation summary
        let avg_obs_len = if state.recent_observations.is_empty() {
            0.0
        } else {
            state.recent_observations.iter().map(|s| s.len() as f64).sum::<f64>()
                / state.recent_observations.len() as f64
        };
        features.insert("avg_obs_length".to_string(), avg_obs_len);
        features.insert("num_observations".to_string(), state.recent_observations.len() as f64);
        
        // File context
        features.insert("num_open_files".to_string(), state.open_files.len() as f64);
        
        // Available tools
        features.insert("num_available_tools".to_string(), state.available_tools.len() as f64);
        
        features
    }

    pub fn decide(&self, state: &AgentState) -> Decision {
        let start = std::time::Instant::now();
        let features = self.extract_features(state);
        
        let mut node = &self.model.root;
        let mut depth = 0;

        loop {
            if let Some(action) = &node.action {
                let latency_ms = start.elapsed().as_micros() as f64 / 1000.0;
                return Decision {
                    action: action.clone(),
                    confidence: 1.0, // Perfect confidence in leaf
                    reasoning: format!("Decision tree depth: {}", depth),
                    latency_ms,
                };
            }

            // Extract feature value
            let feature = node.feature.as_ref().expect("Non-leaf node has feature");
            let threshold = node.threshold.expect("Non-leaf node has threshold");
            let value = features.get(feature).copied().unwrap_or(0.0);

            node = if value <= threshold {
                node.left.as_ref().expect("Non-leaf node has left child")
            } else {
                node.right.as_ref().expect("Non-leaf node has right child")
            };
            depth += 1;
        }
    }

    pub fn batch_decide(&self, states: &[AgentState]) -> Vec<Decision> {
        states.iter().map(|state| self.decide(state)).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_test_model() -> RouterModel {
        RouterModel {
            root: DecisionTreeNode {
                feature: Some("step".to_string()),
                threshold: Some(5.0),
                left: Some(Box::new(DecisionTreeNode {
                    feature: None,
                    threshold: None,
                    left: None,
                    right: None,
                    action: Some("read_file".to_string()),
                })),
                right: Some(Box::new(DecisionTreeNode {
                    feature: None,
                    threshold: None,
                    left: None,
                    right: None,
                    action: Some("apply_patch".to_string()),
                })),
                action: None,
            },
            feature_names: vec!["step".to_string()],
            tool_names: vec!["read_file".to_string(), "apply_patch".to_string()],
        }
    }

    #[test]
    fn test_router_decide() {
        let model = make_test_model();
        let router = Router::new(model);

        let state = AgentState {
            goal: "Fix bug".to_string(),
            step: 3,
            last_tool: Some("read_file".to_string()),
            recent_observations: vec!["file read".to_string()],
            open_files: vec!["test.py".to_string()],
            available_tools: vec!["read_file".to_string(), "apply_patch".to_string()],
        };

        let decision = router.decide(&state);
        assert_eq!(decision.action, "read_file");
        assert!(decision.latency_ms < 1.0); // Target: <0.1ms in release, <1.0ms in debug
    }

    #[test]
    fn test_router_with_step_gt_threshold() {
        let model = make_test_model();
        let router = Router::new(model);

        let state = AgentState {
            goal: "Fix bug".to_string(),
            step: 10,
            last_tool: Some("read_file".to_string()),
            recent_observations: vec!["file read".to_string()],
            open_files: vec!["test.py".to_string()],
            available_tools: vec!["read_file".to_string(), "apply_patch".to_string()],
        };

        let decision = router.decide(&state);
        assert_eq!(decision.action, "apply_patch");
    }

    #[test]
    fn test_batch_decide() {
        let model = make_test_model();
        let router = Router::new(model);

        let states = vec![
            AgentState {
                goal: "Fix bug".to_string(),
                step: 3,
                last_tool: None,
                recent_observations: vec![],
                open_files: vec![],
                available_tools: vec![],
            },
            AgentState {
                goal: "Fix bug".to_string(),
                step: 10,
                last_tool: None,
                recent_observations: vec![],
                open_files: vec![],
                available_tools: vec![],
            },
        ];

        let decisions = router.batch_decide(&states);
        assert_eq!(decisions.len(), 2);
        assert_eq!(decisions[0].action, "read_file");
        assert_eq!(decisions[1].action, "apply_patch");
    }
}
