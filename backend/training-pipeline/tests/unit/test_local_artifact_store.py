"""Property tests for the LocalArtifactStore adapter (task 8.10)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_training_pipeline.adapters.local_artifact_store import LocalArtifactStore
from aqros_training_pipeline.domain.ports import ArtifactAlreadyExistsError

_names = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="_"),
    min_size=1,
    max_size=20,
)


# Feature: training-pipeline, Property 22: artifact path deterministically encodes
# model name and version; distinct pairs produce distinct locations.
@settings(max_examples=100)
@given(name=_names, version=st.integers(min_value=1, max_value=999))
async def test_property_22_path_encodes_name_and_version(
    name: str, version: int, tmp_path_factory: pytest.TempPathFactory
) -> None:
    store = LocalArtifactStore(str(tmp_path_factory.mktemp("artifacts")))
    path = await store.write_artifact(name, version, b"x")
    assert name in path
    assert f"v{version}" in path


# Feature: training-pipeline, Property 23: Artifact_Store never overwrites a
# previously persisted artifact.
@settings(max_examples=50)
@given(name=_names, version=st.integers(min_value=1, max_value=999))
async def test_property_23_never_overwrites(
    name: str, version: int, tmp_path_factory: pytest.TempPathFactory
) -> None:
    store = LocalArtifactStore(str(tmp_path_factory.mktemp("artifacts")))
    await store.write_artifact(name, version, b"original")
    with pytest.raises(ArtifactAlreadyExistsError):
        await store.write_artifact(name, version, b"replacement")
    assert await store.read_artifact(name, version) == b"original"


# Feature: training-pipeline, Property 24: write-then-read round trip returns
# identical bytes.
@settings(max_examples=100)
@given(name=_names, version=st.integers(min_value=1, max_value=999), data=st.binary(max_size=256))
async def test_property_24_round_trip(
    name: str, version: int, data: bytes, tmp_path_factory: pytest.TempPathFactory
) -> None:
    store = LocalArtifactStore(str(tmp_path_factory.mktemp("artifacts")))
    await store.write_artifact(name, version, data)
    assert await store.read_artifact(name, version) == data
