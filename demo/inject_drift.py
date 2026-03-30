"""Injects drift into a demo corpus by swapping v1 docs with v2 (drifted) versions.

Usage:
    python -m demo.inject_drift --corpus ./demo/corpus_v1 --level moderate

Drift levels:
    mild: 2 docs, minor changes
    moderate: 5 docs, mixed severity (default)
    heavy: 9 docs, targets all golden query documents — drives accuracy to ~55-60%
    catastrophic: 10 docs, heavy corruption across entire corpus
"""

import shutil
from pathlib import Path

# Maps drift level to which drifted docs to inject
# Each tuple: (source_in_v2, description)
DRIFT_DOCS = {
    "mild": [
        ("03_regulatory_compliance.md", "heading hierarchy collapse"),
        ("08_drug_interactions.md", "hidden unicode characters"),
    ],
    "moderate": [
        ("03_regulatory_compliance.md", "heading hierarchy collapse"),
        ("11_contract_law.md", "missing paragraphs"),
        ("08_drug_interactions.md", "hidden unicode characters"),
        ("18_data_pipelines.md", "broken markdown tables"),
        ("16_cloud_architecture.md", "chunk explosion"),
    ],
    "heavy": [
        # All moderate corruptions
        ("03_regulatory_compliance.md", "heading hierarchy collapse"),
        ("11_contract_law.md", "missing paragraphs"),
        ("08_drug_interactions.md", "hidden unicode characters"),
        ("18_data_pipelines.md", "broken markdown tables"),
        ("16_cloud_architecture.md", "chunk explosion"),
        # Plus truncate the remaining golden query target docs
        ("15_dispute_resolution.md", "content truncation"),
        ("01_investment_risk.md", "content truncation"),
        ("06_clinical_trials.md", "content truncation"),
        ("13_data_privacy.md", "content truncation"),
    ],
    "catastrophic": [
        ("03_regulatory_compliance.md", "heading hierarchy collapse"),
        ("11_contract_law.md", "missing paragraphs"),
        ("08_drug_interactions.md", "hidden unicode characters"),
        ("18_data_pipelines.md", "broken markdown tables"),
        ("16_cloud_architecture.md", "chunk explosion"),
        # For catastrophic, also corrupt 5 more docs by truncating them
        ("01_investment_risk.md", "content truncation"),
        ("06_clinical_trials.md", "content truncation"),
        ("13_data_privacy.md", "content truncation"),
        ("17_api_design.txt", "content truncation"),
        ("20_ml_deployment.md", "content truncation"),
    ],
}


def inject_drift(
    corpus_dir: Path,
    v2_dir: Path,
    level: str = "moderate",
) -> list[dict]:
    """Inject drift into corpus by replacing docs with drifted versions.

    Args:
        corpus_dir: Path to the working corpus directory.
        v2_dir: Path to the v2 (drifted) corpus directory.
        level: One of "mild", "moderate", "catastrophic".

    Returns:
        List of dicts describing what was injected.
    """
    if level not in DRIFT_DOCS:
        raise ValueError(f"Unknown drift level: {level}. Use: mild, moderate, catastrophic")

    injected = []
    docs_to_inject = DRIFT_DOCS[level]

    for doc_name, description in docs_to_inject:
        v2_path = v2_dir / doc_name
        target_path = corpus_dir / doc_name

        if description == "content truncation" and target_path.exists():
            content = target_path.read_text()
            if level == "heavy":
                # Write blank stub — simulates document processing failure
                # No recognizable vocabulary means BM25 scores drop to zero
                target_path.write_text("Document unavailable. Processing pipeline error: content extraction failed.\n")
            else:
                # catastrophic: keep first 30%
                truncated = content[: int(len(content) * 0.30)]
                target_path.write_text(truncated)
            injected.append({
                "doc": doc_name,
                "drift_type": description,
                "method": "truncated_in_place",
            })
            continue

        if not v2_path.exists():
            continue

        shutil.copy2(v2_path, target_path)
        injected.append({
            "doc": doc_name,
            "drift_type": description,
            "method": "replaced_from_v2",
        })

    return injected
