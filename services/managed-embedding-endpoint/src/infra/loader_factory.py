from src.core.model_state import ModelState
from src.core.settings import Settings
from src.infra.bge_loader import BgeModelLoader
from src.infra.model_loader import ModelLoader


def build_model_loader(settings: Settings, model_state: ModelState) -> ModelLoader:
    """Build the configured model loader for production startup."""
    return BgeModelLoader(
        model_state,
        model_cache_dir=settings.model_cache_dir,
        embedding_max_length=settings.embedding_max_length,
    )
