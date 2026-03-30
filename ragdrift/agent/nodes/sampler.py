"""Sampler node: decides which docs to re-check.

Adaptive sampling strategy:
1. Previously drifted docs (highest priority)
2. Recently modified files
3. Random sample of stable docs
"""
import random
from pathlib import Path

from ragdrift.agent.state import ScanState


def sampler_node(state: ScanState) -> dict:
    """Select documents to sample based on adaptive strategy."""
    all_paths = state["all_doc_paths"]
    sample_rate = state["sample_rate"]
    reference_data = state.get("reference_data", {})

    sample_size = max(1, int(len(all_paths) * sample_rate))

    # Categorize docs
    previously_drifted = []
    stable = []

    for path_str in all_paths:
        doc_id = Path(path_str).name
        ref = reference_data.get(doc_id)
        if ref and ref.get("_previously_drifted"):
            previously_drifted.append(path_str)
        else:
            stable.append(path_str)

    # Sort stable by modification time (most recent first)
    stable.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)

    # Build sample: all previously drifted + fill rest with recently modified + random
    sampled = list(previously_drifted)
    remaining_budget = sample_size - len(sampled)

    if remaining_budget > 0:
        # Take recently modified first (half of remaining)
        recent_count = min(remaining_budget // 2, len(stable))
        sampled.extend(stable[:recent_count])
        remaining_budget -= recent_count

        # Fill rest with random from remaining stable
        remaining_stable = stable[recent_count:]
        if remaining_budget > 0 and remaining_stable:
            random.seed(42)
            random_sample = random.sample(
                remaining_stable, min(remaining_budget, len(remaining_stable))
            )
            sampled.extend(random_sample)

    # Cap at sample_size
    sampled = sampled[:sample_size]

    return {"sampled_doc_paths": sampled}
