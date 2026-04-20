from src.core.model_state import ModelState


class TestModelState:
    def test_initial_state_not_ready(self):
        state = ModelState()
        assert state.ready is False
        assert state.model_version == ""

    def test_mark_ready(self):
        state = ModelState()
        state.mark_ready("BAAI/bge-m3")
        assert state.ready is True
        assert state.model_version == "BAAI/bge-m3"

    def test_mark_not_ready_resets(self):
        state = ModelState()
        state.mark_ready("BAAI/bge-m3")
        state.mark_not_ready()
        assert state.ready is False
        assert state.model_version == ""
