# Cross-build notes for Hive (aarch64 + Jetson Thor / Grace / Orin)

Hive is a Python meta-package, so most ARM64 work is upstream of us: the
ML dependencies (PyTorch, scikit-learn, numpy) are what need to be present
in the right ABI. This note captures the working combinations as of 2026-Q2.

## 1. Quick reference

| Platform         | Architecture | GPU stack                       | Wheel source                    |
|------------------|--------------|----------------------------------|---------------------------------|
| RTX 3090 / DGX Spark | x86_64   | CUDA 12.4+                       | pypi.org                        |
| Jetson Thor      | aarch64      | CUDA 13.0 (JetPack 7)            | pypi.nvidia.com (l4t)           |
| Jetson Orin AGX  | aarch64      | CUDA 12.6 (JetPack 6.x)          | pypi.nvidia.com (l4t)           |
| Grace (server)   | aarch64      | CUDA 13.0, NVLink-C2C            | pypi.org                        |
| Raspberry Pi 5   | aarch64      | none                             | pypi.org                        |
| iPhone 17 Pro    | arm64 (mac)  | MPS                              | pypi.org                        |

## 2. Native build on Jetson

```bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv libopenblas-base
python3 -m venv .venv && . .venv/bin/activate
pip install --upgrade pip

# Jetson-specific torch (one of the two lines below depending on JetPack):
pip install --extra-index-url https://pypi.nvidia.com torch
# or, for JetPack 6: pip install /opt/nvidia/nsight-systems-*/host/target-linux-aarch64/*whl

pip install scikit-learn joblib psutil numpy
pip install -e ../busyBee-cpu ../honey-comb .
python scripts/hive_benchmark.py --quiet
```

## 3. Cross-build from x86_64

```bash
# Install qemu so aarch64 binaries can run under emulation.
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes

# Build with buildx targeting arm64.
docker buildx create --use --name hive-arm
docker buildx build \
    --platform linux/arm64 \
    -f docker/Dockerfile.aarch64 \
    -t hive:aarch64 \
    --load .
```

## 4. Code changes required for ARM64

* **No ARM-specific code paths inside Hive.** Every component is pure
  Python. The only ARM-specific runtime concern is which wheel index to
  pull PyTorch from.
* **Numpy < 2.0** on aarch64 is recommended for Jetson JetPack 6.x because
  scipy still ships its aarch64 wheels against the 1.x ABI. The
  Dockerfile pins this.
* **Tokenizer offsets** — neither busyBee-cpu nor honey-comb tokenize text;
  they use character-based heuristics. So no fastBPE/sentencepiece wheels
  are needed for Hive itself (only for the GPU LLM, which is loaded
  separately).

## 5. Common pitfalls

* `pip install torch` on aarch64 without `--extra-index-url` will install
  the x86_64 wheel and segfault on import. Always pin via the NVIDIA
  index.
* Some Jetson images ship a PEP 668-marked system Python. Either use
  `python3 -m venv .venv` or pass `--break-system-packages` to pip.
* If `psutil` is missing the host memory numbers in the benchmark read as
  zero — install it from the system package manager on minimal images.

## 6. CI matrix

A working CI matrix for Hive Step 1 is:

```yaml
strategy:
  matrix:
    include:
      - runner: ubuntu-latest        # x86_64 + CPU
        python: "3.12"
        install: ["scikit-learn", "joblib", "psutil", "numpy"]
      - runner: ubuntu-latest-gpu    # x86_64 + CUDA
        python: "3.12"
        install_extra: ["torch"]
      - runner: self-hosted-jetson   # aarch64 + CUDA
        python: "3.12"
        install_extra: ["torch"]
```
