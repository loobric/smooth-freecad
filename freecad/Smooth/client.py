# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Smooth API client — stdlib only, no FreeCAD imports.

Speaks the **sectioned** tool schema (docs/TOOL_SCHEMA.md). The client touches
exactly two entities and stays strictly in its lane:

- ``ToolInstanceRecord`` (one per .fctb): create it, write its own
  ``clients.freecad`` section on routine sync, and — deliberately — *assert*
  the canonical facts FreeCAD's scope permits (``geometry.shape``, dimensions,
  name). A sync section write physically cannot carry ``internal``/``canonical``
  (the server 400s it), which is exactly why a FreeCAD import can never
  silently fabricate ``geometry.shape``.
- ``ToolSet`` (one per .fctl): create it, write its section, assert its
  ``name``, and set its canonical ``members`` (the promoted-out tool numbers).

All traffic goes through one seam, :func:`http_json`, so tests stub exactly one
function — the same pattern as the LinuxCNC client.
"""
import json
import socket
import urllib.error
import urllib.request

HTTP_TIMEOUT = 15  # seconds

# This client's identity (the `clients` map key) and software version, asserted
# in the envelope of every section write.
CLIENT_NAME = "freecad"
CLIENT_VERSION = "0.2.0"


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
    """Sectioned-schema client. Methods mirror the published endpoints."""

    INSTANCES = "/tool-instance-records"
    SETS = "/tool-set-records"

    def __init__(self, base_url, api_key=""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _call(self, method, path, body=None):
        return http_json(method, self.base_url + "/api/v1" + path,
                         self.api_key, body)

    def ping(self):
        return http_json("GET", self.base_url + "/api/health", self.api_key)

    # -- ToolInstanceRecords (one per .fctb) --------------------------------

    def list_instances(self):
        """All instance records: GET '' -> {items: [...]}."""
        return self._call("GET", self.INSTANCES)["items"]

    def get_instance(self, record_id):
        return self._call("GET", "%s/%s" % (self.INSTANCES, record_id))

    def create_instance(self, data=None, client_item_id=None):
        """Create a record, optionally seeding this client's section in the
        same call. The create body carries ``client`` (the section key); the
        per-section PUT below carries it in the path instead."""
        body = None
        if data is not None or client_item_id is not None:
            body = {"client": CLIENT_NAME,
                    "client_version": CLIENT_VERSION,
                    "client_item_id": client_item_id,
                    "data": data or {}}
        return self._call("POST", self.INSTANCES, body)

    def put_instance_section(self, record_id, data, client_item_id=None):
        """Routine sync: write only this client's section. Cannot touch
        canonical — the server rejects a body that tries."""
        body = {"client_version": CLIENT_VERSION,
                "client_item_id": client_item_id,
                "data": data or {}}
        return self._call(
            "PUT", "%s/%s/clients/%s" % (self.INSTANCES, record_id, CLIENT_NAME),
            body)

    def assert_instance(self, record_id, path, value, actor=CLIENT_NAME):
        """The assert door: declare one canonical fact (e.g. 'geometry.shape',
        'geometry.diameter', 'name'). Deliberate and audited."""
        return self._call(
            "POST", "%s/%s/assert" % (self.INSTANCES, record_id),
            {"path": path, "value": value, "actor": actor})

    def assert_instance_fields(self, record_id, asserts, actor=CLIENT_NAME):
        """Apply a list of ``(path, value)`` asserts (e.g. from
        ``record_to_instance_sections``); returns the records in order."""
        return [self.assert_instance(record_id, path, value, actor)
                for path, value in asserts]

    def delete_instance(self, record_id):
        """Delete an instance record. The server unbinds any slot holding it
        first (the slot keeps its data), so this never orphans a binding.
        Returns ``{"deleted": <id>}``."""
        return self._call("DELETE", "%s/%s" % (self.INSTANCES, record_id))

    # -- ToolSets (one per .fctl) -------------------------------------------

    def list_sets(self):
        return self._call("GET", self.SETS)["items"]

    def get_set(self, record_id):
        return self._call("GET", "%s/%s" % (self.SETS, record_id))

    def create_set(self, data=None, client_item_id=None):
        body = None
        if data is not None or client_item_id is not None:
            body = {"client": CLIENT_NAME,
                    "client_version": CLIENT_VERSION,
                    "client_item_id": client_item_id,
                    "data": data or {}}
        return self._call("POST", self.SETS, body)

    def put_set_section(self, record_id, data, client_item_id=None):
        body = {"client_version": CLIENT_VERSION,
                "client_item_id": client_item_id,
                "data": data or {}}
        return self._call(
            "PUT", "%s/%s/clients/%s" % (self.SETS, record_id, CLIENT_NAME),
            body)

    def assert_set(self, record_id, path, value, actor=CLIENT_NAME):
        """Declare a canonical set fact ('name' or 'machine_id')."""
        return self._call(
            "POST", "%s/%s/assert" % (self.SETS, record_id),
            {"path": path, "value": value, "actor": actor})

    def set_members(self, record_id, members, actor=CLIENT_NAME):
        """Set the canonical membership (the promoted-out tool numbers).
        ``members`` is ``[{tool_record_id, number?}]``."""
        return self._call(
            "POST", "%s/%s/members" % (self.SETS, record_id),
            {"actor": actor, "members": members})

    def reconcile_set(self, record_id):
        """Ask the server to reconcile member numbers against the bound
        machine's slots (surfacing, not silently renumbering, the cases it
        cannot infer)."""
        return self._call("POST", "%s/%s/reconcile" % (self.SETS, record_id))

    def delete_set(self, record_id):
        """Delete a tool set. The member instances are NOT deleted — only the
        collection. Returns ``{"deleted": <id>}``."""
        return self._call("DELETE", "%s/%s" % (self.SETS, record_id))
