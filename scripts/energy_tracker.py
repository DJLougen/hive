#!/usr/bin/env python3
"""Energy measurement using NVML for GPU power tracking.

Measures both GPU and CPU energy consumption during code execution.
Uses context manager pattern for clean measurement windows.
"""

import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

try:
    import pynvml
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except Exception:
    NVML_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except Exception:
    PSUTIL_AVAILABLE = False


@dataclass
class EnergyMeasurement:
    """Container for energy measurement results."""
    gpu_energy_joules: float
    cpu_energy_joules: float
    total_energy_joules: float
    duration_seconds: float
    gpu_samples: list  # (timestamp, power_watts)
    cpu_samples: list  # (timestamp, power_watts)
    
    @property
    def avg_gpu_power_watts(self) -> float:
        if not self.gpu_samples:
            return 0.0
        return sum(p for _, p in self.gpu_samples) / len(self.gpu_samples)
    
    @property
    def avg_cpu_power_watts(self) -> float:
        if not self.cpu_samples:
            return 0.0
        return sum(p for _, p in self.cpu_samples) / len(self.cpu_samples)


class EnergyTracker:
    """Tracks GPU and CPU energy consumption in background thread."""
    
    def __init__(self, sample_interval_ms: int = 10):
        if not NVML_AVAILABLE:
            raise RuntimeError("NVML not available - cannot measure GPU energy")
        
        self.sample_interval = sample_interval_ms / 1000.0
        self.running = False
        self.thread = None
        self.gpu_samples = []
        self.cpu_samples = []
        self.start_time = None
        
        # Get GPU handle
        self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        
    def _sample_loop(self):
        """Background sampling loop."""
        try:
            process = psutil.Process() if PSUTIL_AVAILABLE else None
        except:
            process = None
            
        while self.running:
            t = time.time()
            
            # Sample GPU power (in milliwatts from NVML)
            try:
                gpu_power_mw = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle)
                gpu_power_w = gpu_power_mw / 1000.0
                self.gpu_samples.append((t, gpu_power_w))
            except Exception as e:
                print(f"GPU sample error: {e}")
                pass
            
            # Sample CPU power via psutil (approximation)
            if process is not None:
                try:
                    # psutil doesn't give CPU power directly, but we can estimate
                    # using CPU percent and TDP
                    cpu_percent = process.cpu_percent()
                    # Rough estimate: CPU power = (percent/100) * 125W (typical TDP)
                    cpu_power_w = (cpu_percent / 100.0) * 125.0
                    self.cpu_samples.append((t, cpu_power_w))
                except:
                    pass
            
            # Sleep until next sample
            elapsed = time.time() - t
            sleep_time = max(0, self.sample_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    def start(self):
        """Start background sampling."""
        if self.running:
            return
            
        self.running = True
        self.gpu_samples = []
        self.cpu_samples = []
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._sample_loop, daemon=True)
        self.thread.start()
    
    def stop(self) -> EnergyMeasurement:
        """Stop sampling and return measurements."""
        if not self.running:
            raise RuntimeError("Not running")
            
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
            
        end_time = time.time()
        duration = end_time - self.start_time
        
        # Integrate power over time to get energy
        gpu_energy = self._integrate(self.gpu_samples)
        cpu_energy = self._integrate(self.cpu_samples)
        
        return EnergyMeasurement(
            gpu_energy_joules=gpu_energy,
            cpu_energy_joules=cpu_energy,
            total_energy_joules=gpu_energy + cpu_energy,
            duration_seconds=duration,
            gpu_samples=self.gpu_samples.copy(),
            cpu_samples=self.cpu_samples.copy()
        )
    
    @staticmethod
    def _integrate(samples: list) -> float:
        """Integrate power samples to get energy (Joules)."""
        if len(samples) < 2:
            return 0.0
            
        energy = 0.0
        for i in range(len(samples) - 1):
            t1, p1 = samples[i]
            t2, p2 = samples[i + 1]
            dt = t2 - t1
            avg_power = (p1 + p2) / 2.0
            energy += avg_power * dt
            
        return energy


@contextmanager
def measure_energy(sample_interval_ms: int = 10):
    """Context manager for energy measurement.
    
    Usage:
        with measure_energy() as tracker:
            # do work
        result = tracker.result
        print(f"Consumed {result.total_energy_joules:.2f} J")
    """
    tracker = EnergyTracker(sample_interval_ms)
    tracker.start()
    
    class Result:
        result: Optional[EnergyMeasurement] = None
    
    result = Result()
    
    try:
        yield result
    finally:
        result.result = tracker.stop()


if __name__ == "__main__":
    # Test the tracker
    print("Testing energy tracker...")
    
    with measure_energy() as m:
        # Simulate some GPU work
        import torch
        if torch.cuda.is_available():
            x = torch.randn(1000, 1000, device='cuda')
            for i in range(100):
                x = torch.mm(x, x.T)
        
        # Simulate some CPU work
        import time
        time.sleep(0.5)
    
    print("\nMeasurement results:")
    print(f"  Duration: {m.result.duration_seconds:.3f} s")
    print(f"  GPU energy: {m.result.gpu_energy_joules:.2f} J")
    print(f"  CPU energy: {m.result.cpu_energy_joules:.2f} J")
    print(f"  Total energy: {m.result.total_energy_joules:.2f} J")
    print(f"  Avg GPU power: {m.result.avg_gpu_power_watts:.1f} W")
    print(f"  Avg CPU power: {m.result.avg_cpu_power_watts:.1f} W")
    
    # Calculate per-second energy rate
    energy_per_sec = m.result.total_energy_joules / m.result.duration_seconds
    print(f"\nEnergy rate: {energy_per_sec:.2f} J/s")
