# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Smooth API client — stdlib only, no FreeCAD imports.

Speaks the **sectioned** tool schema (docs/TOOL_SCHEMA.md). Two roles:

- The **sync lane**: ``ToolInstanceRecord`` (one per .fctb) and ``ToolSet``
  (one per .fctl) — create them, write their own ``clients.freecad`` section on
  routine sync, and *assert* the canonical facts FreeCAD's scope permits. A sync
  section write physically cannot carry ``internal``/``canonical`` (the server
  400s it), which is why a FreeCAD import can never fabricate ``geometry.shape``.
- The **operator lane** (mirrors the web UI): browse and act on the binding
  inbox, machines and their slots, and the audit log — confirm/reject/adopt
  proposals, bind/unbind/move/delete slots, delete machines. These are
  human-initiated (actor ``human@freecad``), going through the server's
  bind/assert/human doors, not the sync lane.

All traffic goes through one seam, :func:`http_json`, so tests stub exactly one
function — the same pattern as the LinuxCNC client. Every call is timed and
recorded in :attr:`SmoothClient.call_log` for the GUI's API-log debug panel.
"""
import json
import socket
import time
import urllib.error
import urllib.request

HTTP_TIMEOUT = 15  # seconds

# This client's identity (the `clients` map key) and software version, asserted
# in the envelope of every section write.
CLIENT_NAME = "freecad"
CLIENT_VERSION = "0.2.0"

# Actor stamped on human-initiated operator-lane acts (asserts, binds, adopts).
HUMAN_ACTOR = "human@freecad"


class SmoothError(Exception):
    """Server or network failure talking to Smooth.

    ``status`` is the HTTP status code when the server answered (so callers can
    distinguish a 409 install conflict from an unreachable host), else None."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


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
        raise SmoothError("HTTP %d from %s: %s" % (e.code, url, detail),
                          status=e.code)
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise SmoothError("cannot reach %s: %s" % (url, e))


class SmoothClient:
    """Sectioned-schema client. Methods mirror the published endpoints."""

    INSTANCES = "/tool-instance-records"
    SETS = "/tool-set-records"
    ENTRIES = "/tool-table-entry-records"
    MACHINES = "/machine-records"
    INBOX = "/instance-inbox"
    AUDIT = "/audit-logs"

    CALL_LOG_LIMIT = 200

    def __init__(self, base_url, api_key=""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # Ring buffer of recent requests for the GUI's API-log panel; each entry
        # is {method, path, status, ms, error}. Newest appended last.
        self.call_log = []

    # -- request seam + call log ---------------------------------------------

    def _record(self, method, path, status, ms, error=None):
        self.call_log.append({"method": method, "path": path, "status": status,
                              "ms": ms, "error": error})
        if len(self.call_log) > self.CALL_LOG_LIMIT:
            del self.call_log[:-self.CALL_LOG_LIMIT]

    def _request(self, method, url, path, body=None):
        """Time and log one request. A successful http_json implies HTTP 200
        (it only returns on 2xx); a failure records the SmoothError's status."""
        start = time.monotonic()
        try:
            result = http_json(method, url, self.api_key, body)
        except SmoothError as e:
            ms = int((time.monotonic() - start) * 1000)
            self._record(method, path, getattr(e, "status", None), ms, str(e))
            raise
        self._record(method, path, 200, int((time.monotonic() - start) * 1000))
        return result

    def _call(self, method, path, body=None):
        return self._request(method, self.base_url + "/api/v1" + path, path, body)

    def ping(self):
        return self._request("GET", self.base_url + "/api/health", "/api/health")

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
        cannot infer). Returns the record plus ``{"unreconciled": [...]}``."""
        return self._call("POST", "%s/%s/reconcile" % (self.SETS, record_id))

    def get_set_coverage(self, record_id):
        """How this set maps onto its linked machine's tool table.

        Returns the server's coverage report verbatim. When the set is not
        linked to a machine: ``{"set_id", "machine_id": null,
        "applicable": false, "reason": <str>}``. When linked:
        ``{"set_id", "machine_id", "applicable": true, "members": [...],
        "slots": [...], "summary": {...}}`` — each member carries a ``status``
        of ``in_sync`` / ``number_mismatch`` / ``absent_on_machine`` (a library
        tool not yet on the machine — the one to order/load) plus collision
        flags; each extra machine slot carries ``machine_only`` / ``unbound_slot``.
        See uihelpers.coverage_rows for turning this into display rows."""
        return self._call("GET", "%s/%s/coverage" % (self.SETS, record_id))

    def link_set_machine(self, record_id, machine_id, actor=HUMAN_ACTOR):
        """Link a set to a machine so its coverage becomes computable — a
        human-initiated ``machine_id`` assert (the operator lane). Returns the
        updated set record."""
        return self.assert_set(record_id, "machine_id", machine_id, actor=actor)

    def delete_set(self, record_id):
        """Delete a tool set. The member instances are NOT deleted — only the
        collection. Returns ``{"deleted": <id>}``."""
        return self._call("DELETE", "%s/%s" % (self.SETS, record_id))

    # -- Binding inbox (operator lane) --------------------------------------

    def list_inbox(self):
        """Open binding proposals for unbound slots: GET -> {items: [...]}.
        Each item: {id, confidence, reason, entry:{id, machine_id, tool_number},
        proposed_instance:{id, name, diameter}}."""
        return self._call("GET", self.INBOX)["items"]

    def confirm_proposal(self, proposal_id):
        """Accept a proposal — bind the proposed instance to the slot.
        Returns {"status": "confirmed", "entry_id", "instance_id"}. A 409
        SmoothError means the instance is installed elsewhere (unbind first)."""
        return self._call("POST", "%s/%s/confirm" % (self.INBOX, proposal_id))

    def reject_proposal(self, proposal_id):
        """Reject a proposal; that (slot, instance) pair is never re-proposed.
        Returns {"status": "rejected"}."""
        return self._call("POST", "%s/%s/reject" % (self.INBOX, proposal_id))

    # -- Tool table entries / machine slots (operator lane) -----------------

    def list_entries(self, machine_id=None):
        """All slots, optionally filtered to one machine: -> {items: [...]}."""
        path = self.ENTRIES
        if machine_id:
            path = "%s?machine_id=%s" % (self.ENTRIES, machine_id)
        return self._call("GET", path)["items"]

    def get_entry(self, record_id):
        return self._call("GET", "%s/%s" % (self.ENTRIES, record_id))

    def bind_entry(self, record_id, instance_id, move=False, actor=HUMAN_ACTOR):
        """Bind an instance into this slot. ``move=True`` atomically relocates
        an instance installed elsewhere; with the default ``move=False`` the
        server returns a 409 naming where it is already installed."""
        return self._call(
            "POST", "%s/%s/bind" % (self.ENTRIES, record_id),
            {"instance_id": instance_id, "actor": actor, "move": move})

    def unbind_entry(self, record_id):
        """Clear a slot's binding; the instance survives, only the install link
        dies. Returns the updated slot."""
        return self._call("POST", "%s/%s/unbind" % (self.ENTRIES, record_id))

    def adopt_entry(self, record_id, name=None, actor=HUMAN_ACTOR):
        """Mint a new instance from an unbound slot's observations and install
        it. Returns {"instance_id", "slot": {...}}."""
        body = {"actor": actor}
        if name is not None:
            body["name"] = name
        return self._call("POST", "%s/%s/adopt" % (self.ENTRIES, record_id), body)

    def delete_entry(self, record_id):
        """Remove a reported slot. Returns ``{"deleted": <id>}``. (If the
        controller re-pushes, the slot returns.)"""
        return self._call("DELETE", "%s/%s" % (self.ENTRIES, record_id))

    # -- Machines (operator lane) -------------------------------------------

    def list_machines(self):
        return self._call("GET", self.MACHINES)["items"]

    def get_machine(self, record_id):
        return self._call("GET", "%s/%s" % (self.MACHINES, record_id))

    def delete_machine(self, record_id):
        """Delete a machine and its tool-table slots (instances survive).
        Returns ``{"deleted": <id>, "entries_removed": <n>}``."""
        return self._call("DELETE", "%s/%s" % (self.MACHINES, record_id))

    # -- Audit log (operator lane, read-only) -------------------------------

    def list_audit(self, limit=50):
        """Recent audit entries (immutable operation log): -> {logs: [...]}."""
        return self._call("GET", "%s?limit=%d" % (self.AUDIT, limit))["logs"]
