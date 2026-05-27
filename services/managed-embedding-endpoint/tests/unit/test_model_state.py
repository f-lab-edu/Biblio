from src.core.model_state import ModelState


class TestModelState:
    def test_initial_state_not_ready(self):
        state = ModelState()
        assert state.ready is False
        assert state.model_version == ""
        assert state.ready_model_versions == []

    def test_mark_ready(self):
        state = ModelState()
        state.mark_ready("BAAI/bge-m3")
        assert state.ready is True
        assert state.model_version == "BAAI/bge-m3"
        assert state.ready_model_versions == ["BAAI/bge-m3"]

    def test_mark_ready_tracks_multiple_versions_in_order(self):
        state = ModelState()
        state.mark_ready("fake-20260526T143000KST")
        state.mark_ready("fake-20260526T144000KST")

        assert state.ready_model_versions == [
            "fake-20260526T143000KST",
            "fake-20260526T144000KST",
        ]

    def test_clear_ready_version_resets(self):
        state = ModelState()
        state.mark_ready("BAAI/bge-m3")
        state.clear_ready_version()
        assert state.ready is False
        assert state.model_version == ""
        assert state.ready_model_versions == []
