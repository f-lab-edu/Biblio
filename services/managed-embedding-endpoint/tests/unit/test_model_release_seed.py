from src.core.model_release_seed import derive_seed_model_release


class TestDeriveSeedModelRelease:
    def test_derives_version_from_model_artifact_path_last_segment(self):
        seed = derive_seed_model_release(
            model_artifact_path="/home/artyom9/models/bge-m3-20260526T143000KST"
        )

        assert seed.active_model_version == "bge-m3-20260526T143000KST"
        assert seed.active_index_name == "vector-bge-m3-20260526T143000KST"

    def test_allows_explicit_active_index_name(self):
        seed = derive_seed_model_release(
            model_artifact_path="/home/artyom9/models/bge-m3-20260526T143000KST",
            active_index_name="custom-index",
        )

        assert seed.active_model_version == "bge-m3-20260526T143000KST"
        assert seed.active_index_name == "custom-index"
