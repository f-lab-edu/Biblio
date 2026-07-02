class AuthenticationFailed(Exception):
    default_message = "Authentication credentials are invalid."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class CsrfValidationFailed(Exception):
    default_message = "CSRF token is missing or invalid."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
