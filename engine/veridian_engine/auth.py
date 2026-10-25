from fastapi import Request


class Unauthorized(Exception):
    """Raised when the bearer token is missing or unknown."""


def client_id_from_request(request: Request) -> str:
    tokens: dict[str, str] = request.app.state.settings.tokens
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or token.strip() not in tokens:
        raise Unauthorized()
    return tokens[token.strip()]
