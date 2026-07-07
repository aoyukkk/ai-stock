import asyncio
import json
from typing import Any

from backend.main import app


async def call_app(path: str) -> tuple[int, dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    requests = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> dict[str, Any]:
        if requests:
            return requests.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }

    await app(scope, receive, send)

    status = next(
        message["status"]
        for message in messages
        if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return status, json.loads(body)


def get_health_response() -> tuple[int, dict[str, Any]]:
    return asyncio.run(call_app("/health"))


def test_backend_main_app_can_be_imported() -> None:
    assert app is not None


def test_health_returns_200() -> None:
    status, _ = get_health_response()
    assert status == 200


def test_health_returns_success_true() -> None:
    _, body = get_health_response()
    assert body["success"] is True


def test_health_returns_status_ok() -> None:
    _, body = get_health_response()
    assert body["data"]["status"] == "ok"


def test_health_response_shape() -> None:
    _, body = get_health_response()
    assert set(body) == {"success", "data", "message"}
