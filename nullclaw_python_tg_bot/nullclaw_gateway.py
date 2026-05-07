import json
import time
import urllib.request
from typing import Any, Optional
from urllib.error import HTTPError, URLError


class NullclawGatewayError(RuntimeError):
    pass


class NullclawGatewayClient:
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:3000",
        pairing_code: str = "",
        bearer_token: str = "",
        timeout: int = 120,
        default_channel: str = "nullwatch-bot",
    ):
        self.base_url = base_url.rstrip("/")
        self.pairing_code = pairing_code.strip()
        self.bearer_token = bearer_token.strip()
        self.timeout = timeout
        self.default_channel = default_channel

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[dict] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        payload = json.dumps(body).encode() if body is not None else None
        req_headers = {"Accept": "application/json"}
        if body is not None:
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=payload,
            headers=req_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            detail = exc.read().decode()
            raise NullclawGatewayError(
                f"nullclaw gateway {method} {path} failed with {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise NullclawGatewayError(f"Cannot reach nullclaw at {self.base_url}: {exc.reason}") from exc

    def is_alive(self) -> bool:
        try:
            self._request("GET", "/health")
            return True
        except Exception:
            return False

    def ensure_token(self) -> str:
        if self.bearer_token:
            return self.bearer_token
        if not self.pairing_code:
            raise NullclawGatewayError(
                "NULLCLAW_PAIRING_CODE or NULLCLAW_BEARER_TOKEN is required for nullclaw backend"
            )

        response = self._request(
            "POST",
            "/pair",
            headers={"X-Pairing-Code": self.pairing_code},
        )
        token = str(response.get("token") or "").strip()
        if not token:
            raise NullclawGatewayError(f"Unexpected /pair response: {response}")
        self.bearer_token = token
        return token

    def build_session_key(self, context_id: str) -> str:
        return f"a2a:{context_id}"

    def send_message(
        self,
        message: str,
        *,
        context_id: str,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if self.bearer_token or self.pairing_code:
            headers["Authorization"] = f"Bearer {self.ensure_token()}"

        payload = {
            "jsonrpc": "2.0",
            "id": f"req-{context_id}",
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": f"msg-{context_id}",
                    "contextId": context_id,
                    "role": "user",
                    "parts": [{"type": "text", "text": message}],
                }
            },
        }
        response = self._request("POST", "/a2a", body=payload, headers=headers)
        result = response.get("result", {}) if isinstance(response, dict) else {}
        return {
            "response": self._extract_text(result),
            "context_id": context_id,
            "session_key": self.build_session_key(context_id),
            "task": result,
            "raw_response": response,
        }

    def _extract_text(self, result: dict[str, Any]) -> str:
        artifacts = result.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                parts = artifact.get("parts")
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        return part["text"]

        history = result.get("history")
        if isinstance(history, list):
            for item in reversed(history):
                if not isinstance(item, dict) or item.get("role") != "agent":
                    continue
                parts = item.get("parts")
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        return part["text"]
        return ""
