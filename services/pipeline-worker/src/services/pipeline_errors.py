class AudioPreparationError(Exception):
    """Raised when audio validation, splitting, or upload cannot complete."""


class DeleteRequested(Exception):
    """Raised when a processing worker observes a pending video deletion."""
