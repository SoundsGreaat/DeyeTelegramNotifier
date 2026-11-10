"""Authenticate before routing or validation, without credentials in URLs."""

import base64
import binascii
import hmac

from starlette.responses import JSONResponse

DOC_PATHS = frozenset({"/docs", "/openapi.json"})


class ReadOnlyAuthentication:
    def __init__(self, app, token):
        self.app = app
        self.token = token.encode("ascii")

    def authenticated(self, authorization, docs):
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            return hmac.compare_digest(value.encode("utf-8"), self.token)
        if docs and scheme.lower() == "basic":
            try:
                decoded = base64.b64decode(value, validate=True)
                username, separator, password = decoded.partition(b":")
                return (
                    bool(separator)
                    and hmac.compare_digest(username, b"api")
                    and hmac.compare_digest(password, self.token)
                )
            except (ValueError, binascii.Error):
                return False
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        docs = scope["path"] in DOC_PATHS
        headers = dict(scope.get("headers", ()))
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        if not self.authenticated(authorization, docs):
            challenge = 'Basic realm="Monitoring API documentation"' if docs else "Bearer"
            response = JSONResponse(
                {"error": {"code": "unauthorized", "message": "A valid API token is required."}},
                status_code=401,
                headers={"WWW-Authenticate": challenge, "Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
        if scope["method"] not in {"GET", "HEAD"}:
            response = JSONResponse(
                {"error": {"code": "method_not_allowed", "message": "This API is read-only."}},
                status_code=405,
                headers={"Allow": "GET, HEAD", "Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)

        async def no_store(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers", ())) + [(b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, receive, no_store)
