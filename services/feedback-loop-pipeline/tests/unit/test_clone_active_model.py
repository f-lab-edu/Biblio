from src.infra.storage.inmemory import InMemoryArtifactStore
from src.training.runner import CloneActiveModel


async def test_clone_copies_active_serving_artifact_to_candidate_prefix() -> None:
    store = InMemoryArtifactStore({"models/active-v1/config.json": b"weights"})
    cloner = CloneActiveModel(artifact_store=store, serving_model_artifact_prefix="models")

    refs = await cloner.clone(active_model_version="active-v1", candidate_model_version="candidate-v1")

    assert store.objects["models/candidate-v1/config.json"] == b"weights"
    assert refs.source_model_version == "active-v1"
    assert refs.candidate_artifact_ref == "gs://test-bucket/models/candidate-v1/"
    assert refs.serving_artifact_ref == refs.candidate_artifact_ref


async def test_clone_is_idempotent_when_candidate_prefix_already_exists() -> None:
    # 워커가 복제 직후 죽고 같은 run이 재실행되는 상황을 재현한다.
    store = InMemoryArtifactStore(
        {
            "models/active-v1/config.json": b"weights",
            "models/candidate-v1/config.json": b"already-cloned",
        }
    )
    cloner = CloneActiveModel(artifact_store=store, serving_model_artifact_prefix="models")

    refs = await cloner.clone(active_model_version="active-v1", candidate_model_version="candidate-v1")

    # 이미 복제된 candidate 경로는 에러 없이 그대로 두고 ref만 돌려준다.
    assert store.objects["models/candidate-v1/config.json"] == b"already-cloned"
    assert refs.candidate_artifact_ref == "gs://test-bucket/models/candidate-v1/"
