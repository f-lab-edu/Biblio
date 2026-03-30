from src.core.model_state import ModelState
from src.core.settings import Settings
from src.infra.bge_loader import BgeModelLoader
from src.infra.loader_factory import build_model_loader
from src.infra.model_loader import ModelLoader


class TestBuildModelLoader:
    def test_returns_model_loader_contract(self):
        settings = Settings(MODEL_ARTIFACT_PATH="/tmp/model")
        loader = build_model_loader(settings, ModelState())

        assert isinstance(loader, ModelLoader)

    def test_current_default_loader_is_bge_loader(self):
        settings = Settings(MODEL_ARTIFACT_PATH="/tmp/model", MODEL_CACHE_DIR="/tmp/cache")
        loader = build_model_loader(settings, ModelState())

        assert isinstance(loader, BgeModelLoader)
