#!/usr/bin/env python3
"""
Baseline energy measurement: raw LLM inference without Hive preprocessing.

Measures energy consumption when sending tokens directly to vLLM,
bypassing all Hive components (routing, compression, memory).

This gives us the "without Hive" baseline to compare against the
macro benchmark results.

Usage:
    python scripts/baseline_energy.py --sessions 10 --tokens 50000
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import requests

# Add parent dir so 'hive' package is importable when running from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from hive.hardware import NVMLMonitor


def generate_tokens(n_tokens: int) -> str:
    """Generate approximately n_tokens of text.
    
    Rough heuristic: 1 token ≈ 4 characters.
    """
    # Simple repetitive text to hit target token count
    words = ["the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog"]
    text = " ".join(np.random.choice(words, size=n_tokens).tolist())
    return text


def send_to_vllm(text: str, endpoint: str = "http://localhost:8000") -> float:
    """Send text to vLLM and return processing time in seconds.
    
    Args:
        text: Input text to process
        endpoint: vLLM server endpoint
    
    Returns:
        Processing time in seconds
    """
    payload = {
        "prompt": text,
        "max_tokens": 100,  # Fixed output to isolate input cost
        "temperature": 0.0,
    }
    
    start = time.perf_counter()
    response = requests.post(f"{endpoint}/v1/completions", json=payload)
    elapsed = time.perf_counter() - start
    
    response.raise_for_status()
    return elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=10,
                       help="Number of sessions to measure")
    parser.add_argument("--tokens", type=int, default=50000,
                       help="Tokens per session")
    parser.add_argument("--endpoint", type=str, default="http://localhost:8000",
                       help="vLLM endpoint")
    parser.add_argument("--output", type=Path, default=None,
                       help="Save results to JSON file")
    args = parser.parse_args()
    
    # Initialize NVML monitor
    monitor = NVMLMonitor()
    
    print(f"Baseline energy measurement (no Hive)")
    print(f"  Sessions: {args.sessions}")
    print(f"  Tokens/session: {args.tokens:,}")
    print(f"  Endpoint: {args.endpoint}")
    print()
    
    sessions = []
    
    for i in range(args.sessions):
        print(f"Session {i+1}/{args.sessions}...", end=" ", flush=True)
        
        # Generate input
        text = generate_tokens(args.tokens)
        
        # Start energy measurement
        monitor.reset()
        t_start = time.perf_counter()
        
        # Send to vLLM
        elapsed = send_to_vllm(text, args.endpoint)
        
        # Collect energy data
        gpu_energy_j = monitor.get_total_energy_gpu_joules()
        cpu_energy_j = monitor.get_total_energy_cpu_joules()
        peak_power_w = monitor.get_peak_power_w()
        peak_memory_gb = monitor.get_peak_memory_gb()
        
        sessions.append({
            "session_id": i + 1,
            "tokens": args.tokens,
            "elapsed_s": elapsed,
            "energy_gpu_j": gpu_energy_j,
            "energy_cpu_j": cpu_energy_j,
            "total_energy_j": gpu_energy_j + cpu_energy_j,
            "peak_power_w": peak_power_w,
            "peak_memory_gb": peak_memory_gb,
        })
        
        print(f"✓ {elapsed:.2f}s, {gpu_energy_j:.2f}J GPU, {cpu_energy_j:.2f}J CPU")
    
    # Calculate averages
    avg_session = sessions[0].copy()
    avg_session["session_id"] = "average"
    for key in ["tokens", "elapsed_s", "energy_gpu_j", "energy_cpu_j", 
                "total_energy_j", "peak_power_w", "peak_memory_gb"]:
        avg_session[key] = np.mean([s[key] for s in sessions])
    
    print()
    print("=" * 60)
    print("BASELINE RESULTS (no Hive):")
    print(f"  Avg time/session:        {avg_session['elapsed_s']:.2f} s")
    print(f"  Avg GPU energy/session:  {avg_session['energy_gpu_j']:.2f} J")
    print(f"  Avg CPU energy/session:  {avg_session['energy_cpu_j']:.2f} J")
    print(f"  Avg total energy/session:{avg_session['total_energy_j']:.2f} J")
    print(f"  Peak GPU power:          {avg_session['peak_power_w']:.0f} W")
    print(f"  Peak GPU memory:         {avg_session['peak_memory_gb']:.1f} GB")
    print("=" * 60)
    
    # Save results
    if args.output:
        import json
        results = {
            "methodology": "baseline_energy",
            "description": "Raw vLLM inference without Hive preprocessing",
            "sessions": sessions,
            "averages": avg_session,
        }
        args.output.write_text(json.dumps(results, indent=2))
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
