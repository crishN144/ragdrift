"""upload_golden_eval tests — LangSmith client fully stubbed, no network."""
import shutil
from pathlib import Path

import pytest

from ragdrift.cli import run_init
from ragdrift.observability.langsmith_eval import upload_golden_eval

DEMO_DIR = Path(__file__).parent.parent / "demo"


@pytest.fixture
def snapshotted_corpus(tmp_path):
    work = tmp_path / "corpus"
    shutil.copytree(DEMO_DIR / "corpus_v1", work)
    run_init(work, golden=str(DEMO_DIR / "golden_queries.json"))
    return work


def test_raises_without_api_key(snapshotted_corpus, monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LangSmith is not configured"):
        upload_golden_eval(snapshotted_corpus)


def test_raises_without_golden_queries(tmp_path, monkeypatch):
    pytest.importorskip("langsmith")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_fake")
    work = tmp_path / "corpus"
    shutil.copytree(DEMO_DIR / "corpus_v1", work)
    run_init(work)  # snapshot but no golden queries
    with pytest.raises(RuntimeError, match="No golden queries"):
        upload_golden_eval(work)


def test_uploads_dataset_and_experiment_with_stubbed_client(
    snapshotted_corpus, monkeypatch
):
    langsmith = pytest.importorskip("langsmith")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_fake")

    created = {}

    class FakeDataset:
        id = "ds-123"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def has_dataset(self, dataset_name):
            return False

        def create_dataset(self, dataset_name, description):
            created["dataset_name"] = dataset_name
            return FakeDataset()

        def create_examples(self, inputs, outputs, dataset_id):
            created["examples"] = list(zip(inputs, outputs))

    class FakeRun:
        def __init__(self, outputs):
            self.outputs = outputs

    class FakeResult:
        experiment_name = "ragdrift-golden-abc"

        def __init__(self, rows):
            self._rows = rows

        def __iter__(self):
            return iter(self._rows)

    def fake_evaluate(target, data, evaluators, experiment_prefix, client):
        rows = []
        for inputs, _ in created["examples"]:
            outputs = target(inputs)
            rows.append({"run": FakeRun(outputs)})
        created["evaluated"] = len(rows)
        return FakeResult(rows)

    monkeypatch.setattr(langsmith, "Client", FakeClient)
    monkeypatch.setattr(langsmith, "evaluate", fake_evaluate)

    summary = upload_golden_eval(snapshotted_corpus, dataset_name="my-ds")

    assert created["dataset_name"] == "my-ds"
    assert created["evaluated"] == summary["num_queries"] > 0
    assert summary["experiment_name"] == "ragdrift-golden-abc"
    # clean corpus vs its own snapshot: retrieval is unchanged
    assert summary["avg_score_accuracy"] == 1.0
