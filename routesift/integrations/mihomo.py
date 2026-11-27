"""Bounded HTTP requests to the local Mihomo controller."""

import json
from http.client import HTTPConnection, HTTPSConnection, HTTPException
from urllib.parse import urlsplit


class MihomoAPI:
    def __init__(self, url, headers):
        self.url = urlsplit(url)
        self.headers = headers or {}

    def request(self, method, path):
        connection_type = HTTPSConnection if self.url.scheme == "wss" else HTTPConnection
        connection = connection_type(self.url.hostname, self.url.port, timeout=2)
        try:
            connection.request(method, path, headers=self.headers)
            with connection.getresponse() as response:
                if response.status == 204:
                    return None
                if response.status != 200:
                    raise HTTPException(f"Mihomo HTTP {response.status}")
                return json.loads(response.read(16 * 1024 * 1024))
        finally:
            connection.close()
