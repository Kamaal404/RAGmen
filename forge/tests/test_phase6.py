"""Phase 6: blueprints as the skill emits them must run through Forge unchanged.

Earlier tests build blueprints as Python dicts, which skips the spec and let
Forge drift from it: the reference blueprint's entity templates crashed the
embedding step, and its vocabulary CSV was never read. These tests load the
real files from spec/examples, the way `forge add --blueprint` does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.blueprint import load_blueprint
from forge.chunking import chunk_document
from forge.embed import build_embedding_targets
from forge.embed.embedder import LocalEmbedder
from forge.enforce import enforce_ontology
from forge.extract import run_extraction
from forge.extract.models import ExtractedEntity, ExtractedRelation, ExtractionResult
from forge.loaders import load_source
from forge.pipeline import _embed_kwargs, run_pipeline
from forge.registry.models import Source
from forge.resolve import ResolvedEntity
from forge.resolve.resolver import EntityResolver, Vocabulary

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((REPO / "spec" / "examples").glob("*.blueprint.yaml"))


@pytest.fixture(autouse=True)
def _repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # Blueprint paths (golden_set, vocabulary sources) resolve against the
    # working directory, and the documented commands run from the repo root.
    monkeypatch.chdir(REPO)


def test_examples_exist() -> None:
    names = {p.name for p in EXAMPLES}
    assert {"firecraft.blueprint.yaml", "tenancy.blueprint.yaml"} <= names


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_reference_blueprint_loads(path: Path) -> None:
    bp = load_blueprint(path)
    assert bp["blueprint_hash"].startswith("sha256:")


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_every_embedding_target_renders(path: Path) -> None:
    """One resolved entity per declared type must render through every target.
    This is the check that would have caught the KeyError on {description}."""
    bp = load_blueprint(path)
    entities = [
        ResolvedEntity(canonical_name=f"sample {nt['label'].lower()}", label=nt["label"])
        for nt in bp["ontology"]["node_types"]
    ]
    chunks = [{"chunk_id": "c1", "text": "body", "path": "A / B"}]
    targets = build_embedding_targets(bp, chunks, entities)
    rendered_names = {name for name, _, _ in targets}
    assert rendered_names == {t["name"] for t in bp["embedding"]["targets"]}


class TestTenancyOnboarding:
    """A statute, structured unlike firecraft, onboarded with no Forge changes."""

    BP = REPO / "spec" / "examples" / "tenancy.blueprint.yaml"
    CORPUS = REPO / "spec" / "examples" / "corpora" / "tenancy-act.md"

    def _chunks(self, bp: dict):
        _, text = load_source(self.CORPUS)
        return chunk_document(text, bp["structure"], "src-tenancy", bp["blueprint_hash"])

    def test_chunks_one_per_article(self) -> None:
        bp = load_blueprint(self.BP)
        chunks = self._chunks(bp)
        assert len(chunks) == 6
        assert all(c.text.startswith("## Article") for c in chunks)
        assert chunks[2].path == "Deposits / Maximum deposit"

    def test_contents_list_is_not_chunked(self) -> None:
        bp = load_blueprint(self.BP)
        assert not any("- Part 1" in c.text for c in self._chunks(bp))

    def test_full_pipeline(self) -> None:
        bp = load_blueprint(self.BP)
        chunks = self._chunks(bp)
        chunks_data = [{"chunk_id": c.chunk_id, "text": c.text, "path": c.path} for c in chunks]
        results, _ = run_extraction(bp, chunks_data, "src-tenancy", provider="mock")
        source = Source(
            uri=str(self.CORPUS), content_hash="sha256:x", mime="text/markdown",
            blueprint_version=bp["blueprint_version"], blueprint_hash=bp["blueprint_hash"],
            source_id="src-tenancy",
        )

        result = run_pipeline(bp, source, chunks, results, store_backend="memory", embed_backend="mock")

        assert result.success, result.errors
        assert result.chunks_written == 6
        # The mock extractor uses the blueprint's first type, so nothing it
        # emits is dropped by the closed ontology.
        assert result.entities_written == 6
        assert result.entities_dropped == 0
        assert result.embeddings_written == 6
        assert result.drift_report is not None
        assert not result.drift_report.thresholds_exceeded

    def test_party_vocabulary_read_from_csv(self) -> None:
        bp = load_blueprint(self.BP)
        vocab = Vocabulary.from_blueprint_ontology(bp["ontology"], "Party")
        assert vocab is not None
        assert "lessor" in vocab.entries["landlord"]

        resolved = EntityResolver(bp).resolve([ExtractedEntity(label="Party", name="The Landlord")])
        assert resolved[0].canonical_name == "landlord"
        assert resolved[0].tier == "exact"

    def test_fuzzy_threshold_is_a_fraction(self) -> None:
        """fuzzy_threshold 0.9 means 90%, not 0.9 out of 100. Unscaled, every
        unrelated name collapsed into the nearest canonical entry."""
        pytest.importorskip("rapidfuzz")
        bp = load_blueprint(self.BP)
        typo, unrelated = EntityResolver(bp).resolve([
            ExtractedEntity(label="Party", name="landlrd"),
            ExtractedEntity(label="Party", name="holiday let"),
        ])
        assert (typo.canonical_name, typo.tier) == ("landlord", "fuzzy")
        assert (unrelated.canonical_name, unrelated.tier) == ("holiday let", "new")


class TestEnforcement:
    ONTOLOGY = {
        "closed": True,
        "node_types": [{"label": "Party"}, {"label": "Obligation"}],
        "patterns": [["Party", "OWES", "Obligation"]],
    }

    def _result(self) -> ExtractionResult:
        return ExtractionResult(
            chunk_id="c1",
            source_id="s1",
            entities=[
                ExtractedEntity(label="Party", name="landlord"),
                ExtractedEntity(label="Penalty", name="fine"),  # undeclared label
            ],
            relations=[
                ExtractedRelation("Party", "landlord", "OWES", "Obligation", "repair"),
                ExtractedRelation("Obligation", "repair", "OWES", "Party", "landlord"),  # reversed
                ExtractedRelation("Party", "landlord", "PAYS", "Obligation", "repair"),  # undeclared
            ],
        )

    def test_drops_undeclared_labels_and_patterns(self) -> None:
        out = enforce_ontology([self._result()], self.ONTOLOGY)
        assert [e.label for e in out.results[0].entities] == ["Party"]
        assert len(out.results[0].relations) == 1
        assert out.entities_dropped == 1
        assert out.relations_dropped == 2

    def test_open_ontology_passes_through(self) -> None:
        # The CLI's no-blueprint fallback is not closed; filtering against its
        # empty type list would discard everything.
        out = enforce_ontology([self._result()], {**self.ONTOLOGY, "closed": False})
        assert len(out.results[0].entities) == 2
        assert out.entities_dropped == out.relations_dropped == 0


class TestEmbedderSettings:
    BP = {"embedding": {"model": "bge-base-en-v1.5", "dimensions": 768}}

    def test_local_backend_uses_blueprint_model(self) -> None:
        assert _embed_kwargs(self.BP, "local", None) == {"dimensions": 768, "model_name": "bge-base-en-v1.5"}

    def test_mock_backend_gets_dimensions_only(self) -> None:
        assert _embed_kwargs(self.BP, "mock", None) == {"dimensions": 768}

    def test_caller_overrides_win(self) -> None:
        assert _embed_kwargs(self.BP, "mock", {"dimensions": 64}) == {"dimensions": 64}

    def test_wrong_width_vector_raises(self) -> None:
        class _Vec(list):
            def tolist(self) -> list[float]:
                return list(self)

        class _Model:
            def encode(self, text, normalize_embeddings=True):
                return _Vec([0.0] * 384)

        emb = LocalEmbedder(model_name="bge-base-en-v1.5", dimensions=768)
        emb._model = _Model()
        with pytest.raises(ValueError, match="384-d vectors"):
            emb.embed("anything")
