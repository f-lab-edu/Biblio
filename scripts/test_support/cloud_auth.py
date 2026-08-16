from __future__ import annotations


def user_identity_token_command() -> list[str]:
    return ["gcloud", "auth", "print-identity-token"]
