"""Select the approved 100-case subset using the unchanged Verified adapter."""

from pathlib import Path
from src.components import Component
from src.loading import load_adapter


def create_dataset():
    directory = Path(__file__).resolve().parent.parent / "swe_bench_verified"
    dataset = load_adapter(Component("dataset", "swe-bench-verified", directory, {}, "SweBenchVerified"))
    dataset.data_path = directory / "data/swebench_verified_subset.json"
    return dataset
