# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Loobric API access for the FreeCAD client.

This is **not** a hand-rolled HTTP client. Per the project's reference-client
principle, Python clients import ``loobric`` (the vendored single-file reference
client, ``loobric.py``) instead of reinventing transport and the API surface.
``LoobricApi`` is a thin :class:`loobric.Client` subclass that adds only the
FreeCAD-specific conveniences:

- the FreeCAD identity baked into the doors — the ``clients`` section key
  ``freecad`` and the human actor ``human@freecad``;
- the sync-lane helpers ``sync.py`` calls (create/section-write/assert on
  instance and set records), named as that module expects so the headless sync
  engine reads cleanly;
- ``ping`` (a cheap authenticated round-trip) for the window's connection label;
- a recording transport so the demoted API-log panel can still show traffic, and
  so tests can inject a fake transport through the same seam.

Errors are loobric's typed hierarchy. ``LoobricError`` is re-exported as an alias
of :class:`loobric.LoobricError` so existing ``except LoobricError`` sites keep
working; HTTP failures are :class:`loobric.HTTPError` carrying ``.status`` (e.g.
the 409 install conflict the bind UI distinguishes).
"""
import time

from . import loobric, mapping

# Re-exports so callers import error types from one place.
LoobricError = loobric.LoobricError      # back-compat alias (all loobric failures)
HTTPError = loobric.HTTPError
NotFound = loobric.NotFound

# This client's identity (the `clients` map key) and software version, stamped on
# every section write; and the actor on human-initiated operator-lane acts.
CLIENT_NAME = mapping.CLIENT_NAME        # "freecad"
CLIENT_VERSION = "0.3.1"
HUMAN_ACTOR = "human@freecad"

# Public resource tokens for the generic canonical doors (assert / section sync).
INSTANCES = "tool-instance-records"
SETS = "tool-set-records"


class LoobricApi(loobric.Client):
    """loobric.Client with FreeCAD's identity, sync-lane helpers, and a call log."""

    CALL_LOG_LIMIT = 200

    def __init__(self, base_url, api_key="", transport=None):
        # Ring buffer of recent requests for the GUI's API-log panel; each entry
        # is {method, path, status, ms, error}. Newest appended last. Set before
        # super().__init__ so the recording transport can close over it.
        self.call_log = []
        base = transport or loobric.make_request
        super().__init__(base_url=base_url or None, api_key=api_key or None,
                         transport=self._recording(base))

    # -- request seam + call log --------------------------------------------

    def _recording(self, base):
        """Wrap a transport so every call is timed and logged. A successful
        make_request implies a 2xx (it only returns then); a failure records the
        LoobricError's status where it has one (HTTPError) else None."""
        def transport(method, endpoint, **kw):
            start = time.monotonic()
            try:
                result = base(method, endpoint, **kw)
            except loobric.LoobricError as e:
                ms = int((time.monotonic() - start) * 1000)
                self._record(method, endpoint, getattr(e, "status", None), ms, str(e))
                raise
            self._record(method, endpoint, 200, int((time.monotonic() - start) * 1000))
            return result
        return transport

    def _record(self, method, path, status, ms, error=None):
        self.call_log.append({"method": method, "path": path, "status": status,
                              "ms": ms, "error": error})
        if len(self.call_log) > self.CALL_LOG_LIMIT:
            del self.call_log[:-self.CALL_LOG_LIMIT]

    def ping(self):
        """A cheap authenticated round-trip for the connection label. Raises a
        LoobricError (unreachable / auth) the caller renders; returns on success."""
        self.list_tool_sets()
        return True

    # -- ToolInstanceRecords (one per .fctb) — sync lane --------------------

    def list_instances(self):
        return self.list_tool_records()

    def get_instance(self, record_id):
        return self.get_tool_record(record_id)

    def create_instance(self, data=None, client_item_id=None):
        """Create a record, optionally seeding this client's section in the same
        call (the create body carries the section key ``client``)."""
        if data is None and client_item_id is None:
            return self.create_tool_record()
        return self.create_tool_record(
            client=CLIENT_NAME, client_version=CLIENT_VERSION,
            client_item_id=client_item_id, data=data or {})

    def put_instance_section(self, record_id, data, client_item_id=None):
        """Routine sync: write only this client's section (the server rejects a
        body that tries to touch internal/canonical)."""
        return self.sync_client_section(
            INSTANCES, record_id, CLIENT_NAME, data or {},
            client_version=CLIENT_VERSION, client_item_id=client_item_id)

    def assert_instance(self, record_id, path, value, actor=CLIENT_NAME):
        """The assert door: declare one canonical fact (name, geometry.shape, …)."""
        return self.assert_field(INSTANCES, record_id, path, value, actor)

    def assert_instance_fields(self, record_id, asserts, actor=CLIENT_NAME):
        """Apply a list of ``(path, value)`` asserts; returns the records in order."""
        return [self.assert_instance(record_id, path, value, actor)
                for path, value in asserts]

    def delete_instance(self, record_id):
        return self.delete_tool_record(record_id)

    # -- ToolCatalogRecords (browse + create-from) — M2 --------------------

    def list_catalogs(self):
        """The catalog records available to browse (ToolCatalogRecords)."""
        return self.list_catalog_records()

    def get_catalog(self, record_id):
        return self.get_catalog_record(record_id)

    def create_from_catalog(self, catalog_id, name=None):
        """Create a new UNBOUND instance from a catalog type (the catalog->
        instance door). ``name`` overrides the copied catalog name when given."""
        return self.create_instance_from_catalog(catalog_id, name=name)

    # -- ToolSets (one per .fctl) — sync lane -------------------------------

    def list_sets(self):
        return self.list_tool_sets()

    def get_set(self, record_id):
        return self.get_tool_set(record_id)

    def create_set(self, data=None, client_item_id=None):
        if data is None and client_item_id is None:
            return self._call("POST", "/%s" % SETS, body={})
        return self._call("POST", "/%s" % SETS, body={
            "client": CLIENT_NAME, "client_version": CLIENT_VERSION,
            "client_item_id": client_item_id, "data": data or {}})

    def put_set_section(self, record_id, data, client_item_id=None):
        return self.sync_client_section(
            SETS, record_id, CLIENT_NAME, data or {},
            client_version=CLIENT_VERSION, client_item_id=client_item_id)

    def assert_set(self, record_id, path, value, actor=CLIENT_NAME):
        """Declare a canonical set fact ('name' or 'machine_id')."""
        return self.assert_field(SETS, record_id, path, value, actor)

    def link_set_machine(self, record_id, machine_id, actor=HUMAN_ACTOR):
        """Link a set to a machine — a human-initiated ``machine_id`` assert. When
        linked, member numbers are inherited from the machine's tool-table entries."""
        return self.link_set_to_machine(record_id, machine_id, actor)

    def delete_set(self, record_id):
        return self.delete_tool_set(record_id)

    # -- Binding (operator lane) — Machines is the single binding surface ----

    def bind_entry(self, entry_id, instance_id=None, name=None, move=False,
                   actor=HUMAN_ACTOR):
        """Bind an instance into an entry. ``move=True`` atomically relocates an
        instance bound elsewhere (the default returns a 409 naming where it is)."""
        return super().bind_entry(entry_id, instance_id=instance_id, name=name,
                                  move=move, actor=actor)

    def bind_new(self, entry_id, name=None, actor=HUMAN_ACTOR):
        """Mint a new instance from this entry's observations and bind it (the
        'new tool' path). Omitting ``instance_id`` is what tells the server to mint."""
        return super().bind_entry(entry_id, instance_id=None, name=name, actor=actor)

    # -- Audit log (operator lane, read-only) -------------------------------

    def list_audit(self, limit=50):
        """Recent audit entries, newest first as the server returns them, capped
        client-side to ``limit``."""
        payload = self.list_audit_logs()
        logs = payload.get("logs", payload) if isinstance(payload, dict) else payload
        return (logs or [])[:limit]
