# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Smooth facade API client — stdlib only, no FreeCAD imports.

Speaks ONLY the published v2 facade (ToolRecord, Library); deep-entity
routes are off-limits by design (smooth-core decision G3). All traffic
goes through one seam, http_json(), so tests stub exactly one function —
the same pattern as the LinuxCNC client.
"""
import json
import socket
import urllib.error
import urllib.request

HTTP_TIMEOUT = 15  # seconds


class SmoothError(Exception):
    """Server or network failure talking to Smooth."""


def http_json(method, url, api_key, body=None, timeout=HTTP_TIMEOUT):
    """One JSON request. Raises SmoothError on any failure."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if api_key:
        request.add_header("Authorization", "Bearer %s" % api_key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise SmoothError("HTTP %d from %s: %s" % (e.code, url, detail))
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise SmoothError("cannot reach %s: %s" % (url, e))


class SmoothClient:
    """Thin facade client. Methods mirror the published endpoints."""

    def __init__(self, base_url, api_key=""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _call(self, method, path, body=None):
        return http_json(method, self.base_url + "/api/v1" + path,
                         self.api_key, body)

    def ping(self):
        return http_json("GET", self.base_url + "/api/health", self.api_key)

    # ToolRecords
    def list_records(self):
        return self._call("GET", "/tool-records?limit=1000")["items"]

    def create_records(self, items):
        return self._call("POST", "/tool-records", {"items": items})

    def update_records(self, items):
        return self._call("PATCH", "/tool-records", {"items": items})

    def get_record(self, record_id):
        return self._call("GET", "/tool-records/%s" % record_id)

    # Libraries
    def list_libraries(self):
        return self._call("GET", "/libraries")["items"]

    def create_libraries(self, items):
        return self._call("POST", "/libraries", {"items": items})

    def update_libraries(self, items):
        return self._call("PATCH", "/libraries", {"items": items})
