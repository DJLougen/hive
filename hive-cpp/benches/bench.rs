//! Criterion-based benchmarks for hive-cpp.
//!
//! Run with: `cargo bench` (release mode, harness = false).
//!
//! Measures:
//! - Router decision latency (target <0.1ms)
//! - Compressor per-message latency (target <0.1ms)
//! - Memory graph put/get latency (target <0.01ms)

use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};
use hive_cpp::{
    AgentState, Compressor, CompressionRules, DecisionTreeNode, MemoryGraph,
    Router, RouterModel,
};

/// Build a small realistic router model for benchmarking.
fn build_test_model() -> RouterModel {
    let leaf_read = DecisionTreeNode {
        feature: None,
        threshold: None,
        left: None,
        right: None,
        action: Some("read_file".to_string()),
    };
    let leaf_apply = DecisionTreeNode {
        feature: None,
        threshold: None,
        left: None,
        right: None,
        action: Some("apply_patch".to_string()),
    };
    let root = DecisionTreeNode {
        feature: Some("step".to_string()),
        threshold: Some(5.0),
        left: Some(Box::new(leaf_read)),
        right: Some(Box::new(leaf_apply)),
        action: None,
    };
    RouterModel {
        root,
        feature_names: vec!["step".to_string()],
        tool_names: vec!["read_file".to_string(), "apply_patch".to_string()],
    }
}

fn build_test_state(step: u32) -> AgentState {
    AgentState {
        goal: "Fix the failing test in test_auth.py".to_string(),
        step,
        last_tool: Some("read_file".to_string()),
        recent_observations: vec![
            "File read successfully".to_string(),
            "Test failed at line 42".to_string(),
        ],
        open_files: vec!["test_auth.py".to_string()],
        available_tools: vec!["read_file".to_string(), "apply_patch".to_string()],
    }
}

fn bench_router(c: &mut Criterion) {
    let mut group = c.benchmark_group("router");
    let model = build_test_model();
    let router = Router::new(model);

    for steps in &[1u32, 10, 100, 1000] {
        group.throughput(Throughput::Elements(*steps as u64));
        group.bench_with_input(BenchmarkId::from_parameter(steps), steps, |b, &steps| {
            let state = build_test_state(steps);
            b.iter(|| router.decide(&state));
        });
    }

    group.finish();
}

fn bench_compressor(c: &mut Criterion) {
    let mut group = c.benchmark_group("compressor");
    let rules = CompressionRules::default();
    let compressor = Compressor::new(rules);

    for size in &[100usize, 500, 1000, 5000] {
        group.throughput(Throughput::Elements(*size as u64));
        group.bench_with_input(BenchmarkId::from_parameter(size), size, |b, &size| {
            let tokens: Vec<String> = (0..size)
                .map(|i| format!("token_{}_{}", i, rand::random::<u8>()))
                .collect();
            let token_refs: Vec<&str> = tokens.iter().map(|s| s.as_str()).collect();
            b.iter(|| compressor.compress_tokens(&token_refs));
        });
    }

    group.finish();
}

fn bench_memory(c: &mut Criterion) {
    let mut group = c.benchmark_group("memory");
    let graph = MemoryGraph::new(10_000, 0.01);

    group.bench_function("put", |b| {
        let mut counter = 0u64;
        b.iter(|| {
            counter += 1;
            graph.store(counter, format!("value_{}", counter), 1.0);
        });
    });

    // Pre-populate for get benchmark
    for i in 0..1000u64 {
        graph.store(i, format!("value_{}", i), 1.0);
    }

    group.bench_function("get", |b| {
        let mut counter = 0u64;
        b.iter(|| {
            counter += 1;
            graph.retrieve(counter % 1000)
        });
    });

    group.bench_function("search", |b| {
        b.iter(|| graph.search("value_500", 10));
    });

    group.finish();
}

criterion_group!(benches, bench_router, bench_compressor, bench_memory);
criterion_main!(benches);
