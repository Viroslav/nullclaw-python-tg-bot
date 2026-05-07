import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from nullclaw_python_tg_bot.nullclaw_gateway import NullclawGatewayClient, NullclawGatewayError


class NullclawGatewayClientTests(unittest.TestCase):
    def test_is_alive_checks_ready_endpoint(self):
        client = NullclawGatewayClient(base_url="http://nullclaw:3000")

        with patch.object(client, "_request", return_value={"status": "ready"}) as mock_request:
            self.assertTrue(client.is_alive())

        mock_request.assert_called_once_with("GET", "/ready")

    def test_503_error_mentions_readiness_hint_when_body_is_empty(self):
        client = NullclawGatewayClient(base_url="http://nullclaw:3000")
        error = HTTPError(
            url="http://nullclaw:3000/a2a",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(NullclawGatewayError) as ctx:
                client._request("POST", "/a2a", body={"ping": True})

        self.assertIn("not ready yet", str(ctx.exception))
        self.assertIn("/ready", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
