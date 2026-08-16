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
CLIENT_VERSION = "0.7.0"
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

    # -- Cutting data presets (server >= 0.13.0) ---------------------------
    # These mirror loobric-cli 1.6.0's contribute_preset/list_presets verbs;
    # fold into the vendored loobric.py on its next refresh.

    def contribute_preset(self, record_id, body):
        """The audited contribution door: one preset (a recommendation with
        a source), replace-own on (origin, label). ``body`` is the ready
        contribution dict from presetsync.translate; the actor is this
        client's identity — origin and transcriber are both 'freecad' for
        the client's own presets. (This dict-shaped adapter wraps the
        vendored generic verb the same way put_instance_section wraps
        sync_client_section.)"""
        payload = dict(body)
        payload.setdefault("actor", CLIENT_NAME)
        return self._call("POST",
                          f"/{INSTANCES}/{record_id}/presets", body=payload)

    def list_instance_presets(self, record_id):
        """The instance's preset union (own + linked catalog, scope-marked)."""
        return self.list_presets(INSTANCES, record_id)

    def delete_instance_preset(self, record_id, entry_id):
        """Prune one contribution (the delete door; may 403 on scoped keys)."""
        return self.delete_preset(INSTANCES, record_id, entry_id)

    # -- ToolCatalogRecords (browse + create-from) — M2 --------------------
    # NOTE: the record verbs are list_catalog_records/get_catalog_record;
    # bare list_catalogs/get_catalog are the vendored CATALOG-entity verbs
    # (named collections, server >= 0.14.0) — the old shadowing aliases that
    # hid them are gone, callers name what they mean.

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
        """Declare a canonical set fact ('name')."""
        return self.assert_field(SETS, record_id, path, value, actor)

    def active_setups(self):
        """Every active setup row (machine_id + tool_set_id). Which machine runs
        which set is operator-owned (`loobric use-set`); the CAM side only READS
        it. A pre-setups server (404) reads as no setups."""
        try:
            return self.list_setups(status="active")
        except LoobricError:
            return []

    def setup_view(self, machine_id):
        """The machine's derived setup view (ready / claims / notes)."""
        return self.reconciliation(machine_id)

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
