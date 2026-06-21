#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT
#
# loobric.py is THE reference Python client for Smooth Core: a single,
# stdlib-only file that exercises every public API operation. It is licensed MIT
# (not AGPL like the server it lives beside) so MIT clients may vendor or import
# it. Other Python clients (FreeCAD, future Fusion) reuse this rather than
# writing their own HTTP client. See REBOOT.md Phase 2.5.
#
# Two layers in one file: an ops/library layer (returns data, raises typed
# errors, no printing) usable via `import loobric`, and a thin CLI shell
# (argparse + formatting) usable via `python loobric.py <verb>`.
"""
Loobric CLI Utility - Manage authentication and API keys for Smooth Core

Usage:
    # Interactive login (saves session)
    loobric --login
    loobric --logout
    
    # Session-based auth (after login)
    loobric list-keys
    loobric create-key <name> [options]
    
    # API key auth (one-off commands)
    loobric --api-key <key> list-keys
    loobric --api-key <key> --base-url https://api.loobric.com list-tool-sets

Environment Variables:
    LOOBRIC_BASE_URL - Default base URL (can be overridden with --base-url)

Authentication Priority:
    1. --api-key flag (if provided)
    2. Saved session cookie (from login)
    3. No auth (will fail for protected endpoints)
"""

import argparse
import getpass
import http.client
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any, List

# Global session state
SESSION_COOKIE: Optional[str] = None
API_KEY: Optional[str] = None
BASE_URL: str = ""  # Will be set from CLI or environment

# Session file location
SESSION_DIR = Path.home() / ".loobric"
SESSION_FILE = SESSION_DIR / "session.json"


# ---------------------------------------------------------------------------
# Library errors. The ops/library layer (Client, make_request) NEVER prints or
# exits — it raises these so importing clients can handle failure. The CLI shell
# (main) catches LoobricError and turns it into a message + exit code.
# ---------------------------------------------------------------------------

class LoobricError(Exception):
    """Base class for every error the loobric library raises."""


class ConnectionFailed(LoobricError):
    """The server could not be reached (network/DNS/refused/timeout)."""


class HTTPError(LoobricError):
    """The server returned a non-2xx status. Carries .status and .detail."""

    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class NotFound(HTTPError):
    """404 — the resource does not exist."""


class AuthRequired(HTTPError):
    """401/403 — authentication is missing or insufficient."""


def _http_error(status: int, detail: Any) -> HTTPError:
    if status == 404:
        return NotFound(status, detail)
    if status in (401, 403):
        return AuthRequired(status, detail)
    return HTTPError(status, detail)


def load_session():
    """Load session cookie and base URL from file if it exists.
    
    Returns:
        dict: Session data including base_url, session_cookie, and email (if available)
    """
    global SESSION_COOKIE, BASE_URL
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE, 'r') as f:
                data = json.load(f)
                # If BASE_URL is not set, use the one from session
                if not BASE_URL and data.get('base_url'):
                    BASE_URL = data.get('base_url')
                # Load session cookie if base URLs match
                if data.get('base_url') == BASE_URL:
                    SESSION_COOKIE = data.get('session_cookie')
                return data
        except (json.JSONDecodeError, IOError) as e:
            # Ignore errors loading session file
            pass
    return {}


def save_session(email: str = None):
    """Save session cookie and base URL to file.
    
    Args:
        email: Optional email to save with session
    """
    if SESSION_COOKIE:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        try:
            session_data = {
                'base_url': BASE_URL,
                'session_cookie': SESSION_COOKIE
            }
            if email:
                session_data['email'] = email
            
            with open(SESSION_FILE, 'w') as f:
                json.dump(session_data, f)
            # Set restrictive permissions (owner read/write only)
            SESSION_FILE.chmod(0o600)
        except IOError as e:
            print(f"Warning: Could not save session: {e}", file=sys.stderr)


def clear_session():
    """Clear saved session file."""
    if SESSION_FILE.exists():
        try:
            SESSION_FILE.unlink()
        except IOError as e:
            print(f"Warning: Could not clear session file: {e}", file=sys.stderr)


def get_connection(base_url: Optional[str] = None):
    """Create an HTTP/HTTPS connection for the given base URL (or the global)."""
    base = base_url or BASE_URL
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme == "https":
        return http.client.HTTPSConnection(parsed.netloc)
    elif parsed.scheme == "http":
        return http.client.HTTPConnection(parsed.netloc)
    else:
        raise LoobricError(f"Unsupported scheme in base URL: {parsed.scheme!r}")


def make_request(
    method: str,
    endpoint: str,
    body: Optional[Dict[str, Any]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    require_auth: bool = False,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    session_cookie: Optional[str] = None,
    raw_body: Optional[bytes] = None,
    content_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Make an HTTP request to the Smooth API and return parsed JSON.

    The transport for both the CLI and the `Client` library. It NEVER prints or
    exits — on failure it raises a `LoobricError` subclass (`NotFound`,
    `AuthRequired`, `HTTPError`, `ConnectionFailed`). `base_url` / `api_key` /
    `session_cookie` override the module globals so a `Client` can carry its own
    config; when omitted the globals (set by the CLI) are used.

    Args:
        method: HTTP method (GET, POST, DELETE, …)
        endpoint: API path relative to /api/v1 (e.g. "/tool-set-records")
        body: Optional request body (JSON-encoded)
        extra_headers: Optional additional headers
        require_auth: reserved; auth is decided by the server (solo vs multi-user)
        base_url / api_key / session_cookie: per-call config overriding the globals

    Returns:
        Parsed JSON response (``{}`` for an empty 2xx body).
    """
    global SESSION_COOKIE, API_KEY

    conn = get_connection(base_url)
    path = urllib.parse.urljoin("/api/v1/", endpoint.lstrip("/"))
    headers = dict(extra_headers or {})
    headers["Accept"] = "application/json"
    if raw_body is None:
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type

    # Prefer API key over session cookie. With neither, send anyway and let the
    # server decide: a solo-mode server (SMOOTH_SOLO=1) accepts it; a multi-user
    # server returns 401. The client must not pre-judge auth.
    key = api_key if api_key is not None else API_KEY
    cookie = session_cookie if session_cookie is not None else SESSION_COOKIE
    if key:
        headers["Authorization"] = f"Bearer {key}"
    elif cookie:
        headers["Cookie"] = f"session={cookie}"

    # A raw body (e.g. a multipart upload) overrides the JSON path. Otherwise
    # send the body whenever one is given, including an empty {} — some POST
    # endpoints (e.g. record creation) require a JSON body even when it carries
    # only defaults. `if body` would wrongly drop {} as falsy.
    if raw_body is not None:
        send_body = raw_body
    else:
        send_body = json.dumps(body) if body is not None else None

    try:
        conn.request(method, path, body=send_body, headers=headers)
        response = conn.getresponse()
        status = response.status
        content = response.read().decode("utf-8")

        # Capture the session cookie from a login response (CLI session auth).
        set_cookie = response.getheader("set-cookie") or response.getheader("Set-Cookie")
        if set_cookie:
            for part in set_cookie.split(";"):
                part = part.strip()
                if part.startswith("session="):
                    SESSION_COOKIE = part.split("=", 1)[1]
                    break

        if 200 <= status < 300:
            return json.loads(content) if content.strip() else {}
        try:
            detail = json.loads(content).get("detail", content)
        except json.JSONDecodeError:
            detail = content
        raise _http_error(status, detail)
    except (http.client.HTTPException, ConnectionError, OSError) as e:
        raise ConnectionFailed(f"{e} (server at {base_url or BASE_URL})")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Client — the reference library surface. Every method returns parsed data and
# raises a LoobricError subclass on failure; none of them print. This is what
# other Python clients (FreeCAD, Fusion) import and reuse:
#
#     from loobric import Client, NotFound
#     c = Client(base_url="http://nas:8000", api_key="…")   # solo: api_key omitted
#     for s in c.list_tool_sets(): ...
#
# IDs are real server ids; prefix matching (a CLI convenience) lives in the CLI
# shell below, not here. The CLI builds a Client from the module globals via
# _client(); the library delegates transport to make_request so a test that
# patches loobric.make_request intercepts both layers.
# ---------------------------------------------------------------------------

class Client:
    """A reusable Smooth API client. Returns data; raises LoobricError."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 session_cookie: Optional[str] = None, transport=None):
        self.base_url = (base_url or BASE_URL or "").rstrip("/")
        self.api_key = api_key
        self.session_cookie = session_cookie
        # transport(method, endpoint, **kw) -> dict, raising LoobricError. Defaults
        # to make_request (real HTTP); a test can inject one that calls the app
        # in-process. None => resolve make_request at call time (so patching the
        # module-level make_request still intercepts).
        self._transport = transport

    def _send(self, method: str, endpoint: str, **kw):
        return (self._transport or make_request)(
            method, endpoint, base_url=self.base_url or None,
            api_key=self.api_key, session_cookie=self.session_cookie, **kw)

    def _call(self, method: str, endpoint: str, body: Optional[Dict[str, Any]] = None,
              require_auth: bool = True) -> Dict[str, Any]:
        return self._send(method, endpoint, body=body, require_auth=require_auth)

    # -- tool sets -----------------------------------------------------------
    def list_tool_sets(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/tool-set-records").get("items", [])

    def get_tool_set(self, record_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/tool-set-records/{record_id}")

    def create_tool_set(self, name: Optional[str] = None,
                        actor: str = "human@cli") -> Dict[str, Any]:
        rec = self._call("POST", "/tool-set-records", body={})
        if name is not None:
            rec = self.assert_field("tool-set-records", rec["internal"]["id"], "name", name, actor)
        return rec

    def delete_tool_set(self, record_id: str) -> Dict[str, Any]:
        return self._call("DELETE", f"/tool-set-records/{record_id}")

    def link_set_to_machine(self, set_id: str, machine_id: str,
                            actor: str = "human@cli") -> Dict[str, Any]:
        return self.assert_field("tool-set-records", set_id, "machine_id", machine_id, actor)

    def set_members(self, set_id: str, members: List[Dict[str, Any]],
                    actor: str = "human@cli") -> Dict[str, Any]:
        """Replace a tool set's membership. `members` is a list of
        `{tool_record_id, number?}`."""
        return self._call("POST", f"/tool-set-records/{set_id}/members",
                          body={"members": members, "actor": actor})

    def _member_payload(self, set_id: str) -> List[Dict[str, Any]]:
        """Current membership as the `{tool_record_id, number}` payload the
        members door expects (number is the bare int, or None for unknown)."""
        rec = self.get_tool_set(set_id)
        out = []
        for m in (rec.get("canonical") or {}).get("members") or []:
            num = m.get("number")
            out.append({"tool_record_id": m["tool_record_id"],
                        "number": num.get("value") if isinstance(num, dict) else num})
        return out

    def add_to_set(self, set_id: str, tool_record_ids: List[str],
                   actor: str = "human@cli") -> Dict[str, Any]:
        """Add tool record(s) to a set, keeping existing members and their
        numbers. Tools already in the set are skipped — membership is a set, not
        a bag. Read-modify-write over the replace-only members door."""
        members = self._member_payload(set_id)
        have = {m["tool_record_id"] for m in members}
        for tid in tool_record_ids:
            if tid not in have:
                members.append({"tool_record_id": tid, "number": None})
                have.add(tid)
        return self.set_members(set_id, members, actor)

    def remove_from_set(self, set_id: str, tool_record_ids: List[str],
                        actor: str = "human@cli") -> Dict[str, Any]:
        """Remove tool record(s) from a set, keeping the rest. Read-modify-write
        over the replace-only members door."""
        drop = set(tool_record_ids)
        members = [m for m in self._member_payload(set_id)
                   if m["tool_record_id"] not in drop]
        return self.set_members(set_id, members, actor)

    # -- machines ------------------------------------------------------------
    def list_machines(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/machine-records").get("items", [])

    def get_machine(self, record_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/machine-records/{record_id}")

    def create_machine(self, name: Optional[str] = None,
                       controller_type: Optional[str] = None,
                       actor: str = "human@cli") -> Dict[str, Any]:
        """Mint a machine, then assert its name/controller (canonical changes go
        through the assert door, never the create)."""
        rec = self._call("POST", "/machine-records", body={})
        rid = rec["internal"]["id"]
        if name is not None:
            rec = self.assert_field("machine-records", rid, "name", name, actor)
        if controller_type is not None:
            rec = self.assert_field("machine-records", rid, "controller_type", controller_type, actor)
        return rec

    def delete_machine(self, record_id: str) -> Dict[str, Any]:
        return self._call("DELETE", f"/machine-records/{record_id}")

    # -- tool (instance) records --------------------------------------------
    def list_tool_records(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/tool-instance-records").get("items", [])

    def get_tool_record(self, record_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/tool-instance-records/{record_id}")

    def delete_tool_record(self, record_id: str) -> Dict[str, Any]:
        return self._call("DELETE", f"/tool-instance-records/{record_id}")

    # -- machine tool-table entries (ToolTableEntry) ------------------------
    def list_entries(self, machine_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if machine_id:
            return self._call(
                "GET", f"/tool-table-entry-records?machine_id={machine_id}").get("items", [])
        return self._call("GET", "/tool-table-entry-records").get("items", [])

    def get_entry(self, record_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/tool-table-entry-records/{record_id}")

    def sync_entries(self, machine_id: str, entries: List[Dict[str, Any]],
                     client: str = "loobric", machine_name: Optional[str] = None,
                     mode: str = "merge", client_version: str = "") -> Dict[str, Any]:
        """The controller-side tool-table push: upsert a machine's tool-table
        entries by tool_number in one call (numbers/offsets observed). Returns
        ``{"items": [...], "removed_tool_numbers": [...]}``.

        ``entries`` is the wire field too (the old server-side term was purged
        with everything else, REBOOT R10)."""
        return self._call("POST", "/tool-table-entry-records/sync", body={
            "machine_id": machine_id, "client": client,
            "machine_name": machine_name or machine_id,
            "client_version": client_version, "mode": mode, "entries": entries,
        })

    def bind_entry(self, entry_id: str, instance_id: Optional[str] = None,
                   name: Optional[str] = None, move: bool = False,
                   actor: Optional[str] = None) -> Dict[str, Any]:
        """Bind an instance into an entry. Omit instance_id to MINT a new
        instance from the entry's observations (the 'new tool' path) and bind it."""
        body: Dict[str, Any] = {}
        if instance_id is not None:
            body["instance_id"] = instance_id
        if name is not None:
            body["name"] = name
        if move:
            body["move"] = True
        if actor is not None:
            body["actor"] = actor
        return self._call("POST", f"/tool-table-entry-records/{entry_id}/bind", body=body)

    def unbind_entry(self, entry_id: str) -> Dict[str, Any]:
        return self._call("POST", f"/tool-table-entry-records/{entry_id}/unbind")

    def delete_entry(self, entry_id: str) -> Dict[str, Any]:
        return self._call("DELETE", f"/tool-table-entry-records/{entry_id}")

    # -- the canonical 'assert' door ----------------------------------------
    def assert_field(self, resource: str, record_id: str, path: str, value: Any,
                     actor: str = "human@cli") -> Dict[str, Any]:
        return self._call("POST", f"/{resource}/{record_id}/assert",
                          body={"path": path, "value": value, "actor": actor})

    # -- inbox ---------------------------------------------------------------
    def list_inbox(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/instance-inbox").get("items", [])

    def confirm_proposal(self, proposal_id: str) -> Dict[str, Any]:
        return self._call("POST", f"/instance-inbox/{proposal_id}/confirm")

    def reject_proposal(self, proposal_id: str) -> Dict[str, Any]:
        return self._call("POST", f"/instance-inbox/{proposal_id}/reject")

    # -- auth & keys ---------------------------------------------------------
    def register(self, email: str, password: str) -> Dict[str, Any]:
        return self._call("POST", "/auth/register",
                          body={"email": email, "password": password}, require_auth=False)

    def login(self, email: str, password: str) -> Dict[str, Any]:
        return self._call("POST", "/auth/login",
                          body={"email": email, "password": password}, require_auth=False)

    def logout(self) -> Dict[str, Any]:
        return self._call("POST", "/auth/logout", require_auth=False)

    def list_keys(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/auth/keys")

    def create_key(self, name: str, scopes: Optional[List[str]] = None,
                   tags: Optional[List[str]] = None,
                   expires_at: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"name": name}
        if scopes:
            payload["scopes"] = scopes
        if tags:
            payload["tags"] = tags
        if expires_at:
            payload["expires_at"] = expires_at
        return self._call("POST", "/auth/keys", body=payload)

    def revoke_key(self, key_id: str) -> Dict[str, Any]:
        return self._call("DELETE", f"/auth/keys/{key_id}")

    def whoami(self) -> Dict[str, Any]:
        return self._call("GET", "/auth/me")

    def server_version(self) -> Dict[str, Any]:
        """The server's build identity ({version, commit}). Unauthenticated, so
        it works before login; a NotFound means the server predates the endpoint
        (an older build)."""
        return self._call("GET", "/version", require_auth=False)

    def change_password(self, current_password: str, new_password: str) -> Dict[str, Any]:
        return self._call("POST", "/auth/change-password",
                          body={"current_password": current_password,
                                "new_password": new_password})

    # -- the canonical observe door + the client-section sync door ----------
    def observe_field(self, resource: str, record_id: str, path: str, value: Any,
                      client: str, machine: str, unit: Optional[str] = None) -> Dict[str, Any]:
        body = {"path": path, "value": value, "client": client, "machine": machine}
        if unit is not None:
            body["unit"] = unit
        return self._call("POST", f"/{resource}/{record_id}/observe", body=body)

    def sync_client_section(self, resource: str, record_id: str, client: str, data: dict,
                            client_version: str = "",
                            client_item_id: Optional[str] = None) -> Dict[str, Any]:
        """The sync door: write only this client's own section. Physically
        cannot touch internal/canonical (the server rejects that)."""
        return self._call("PUT", f"/{resource}/{record_id}/clients/{client}", body={
            "client_version": client_version, "client_item_id": client_item_id, "data": data,
        })

    # -- record creation (instance / catalog / entry) ------------------------
    def create_tool_record(self, **section) -> Dict[str, Any]:
        return self._call("POST", "/tool-instance-records", body=dict(section))

    def list_catalog_records(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/tool-catalog-records").get("items", [])

    def get_catalog_record(self, record_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/tool-catalog-records/{record_id}")

    def create_catalog_record(self, source: str,
                              fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Seeded, atomic catalog-record create. `source` is the declared actor —
        the server stamps `asserted:<source>` on every field; the client never
        writes provenance. `fields` carries the nominal {value, unit} leaves
        (name/manufacturer/product_code + optional geometry/item_type)."""
        return self._call("POST", "/tool-catalog-records",
                          body={"actor": source, **(fields or {})})

    def create_instance_from_catalog(self, catalog_id: str,
                                     name: Optional[str] = None,
                                     qa: Optional[Dict[str, Any]] = None,
                                     cert: Optional[str] = None) -> Dict[str, Any]:
        """Create a new physical instance from a catalog type via the catalog->
        instance door. The server stamps the catalog_type_id link as
        asserted:<requester> and leaves the instance UNBOUND (a catalog is not a
        machine position). `name` overrides the copied catalog name when given.

        Optional manufacturer QA: `qa` is a geometry-shaped {value, unit} map and
        `cert` its certificate/serial; the server stamps each measured field
        observed:manufacturer@<serial> (the client never sends a raw source)."""
        body: Dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if qa is not None:
            body["qa"] = qa
        if cert is not None:
            body["cert"] = cert
        return self._call("POST",
                          f"/tool-catalog-records/{catalog_id}/create-instance",
                          body=body)

    def create_entry(self, machine_id: str, **section) -> Dict[str, Any]:
        return self._call("POST", "/tool-table-entry-records",
                          body={"machine_id": machine_id, **section})

    # -- users (admin) -------------------------------------------------------
    def create_user(self, email: str, password: str, **extra) -> Dict[str, Any]:
        return self._call("POST", "/users",
                          body={"email": email, "password": password, **extra})

    def update_user(self, user_id: str, **fields) -> Dict[str, Any]:
        return self._call("PATCH", f"/users/{user_id}", body=dict(fields))

    def update_user_roles(self, user_id: str, **fields) -> Dict[str, Any]:
        return self._call("PATCH", f"/users/{user_id}/roles", body=dict(fields))

    # -- manufacturer catalogs ----------------------------------------------
    def list_catalogs(self) -> Any:
        return self._call("GET", "/catalogs")

    def get_catalog(self, catalog_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/catalogs/{catalog_id}")

    def catalog_analytics(self, catalog_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/catalogs/{catalog_id}/analytics")

    def create_catalog(self, **fields) -> Dict[str, Any]:
        return self._call("POST", "/catalogs", body=dict(fields))

    def update_catalog(self, catalog_id: str, **fields) -> Dict[str, Any]:
        return self._call("PATCH", f"/catalogs/{catalog_id}", body=dict(fields))

    # -- change detection ----------------------------------------------------
    def changes_max_version(self, entity_type: str) -> Dict[str, Any]:
        return self._call("GET", f"/changes/{entity_type}/max-version")

    def changes_since_version(self, entity_type: str, version: int) -> Dict[str, Any]:
        return self._call(
            "GET", f"/changes/{entity_type}/since-version?since_version={version}")

    def changes_since_timestamp(self, entity_type: str, timestamp: str) -> Dict[str, Any]:
        return self._call(
            "GET", f"/changes/{entity_type}/since-timestamp?since_timestamp={timestamp}")

    # -- audit log -----------------------------------------------------------
    def list_audit_logs(self) -> Any:
        return self._call("GET", "/audit-logs")

    # -- backup (admin) ------------------------------------------------------
    def export_backup(self) -> Any:
        return self._call("GET", "/backup/export")

    def import_backup(self, backup_json: str, filename: str = "backup.json") -> Any:
        """Restore from a backup JSON document. /backup/import is a multipart
        file upload, so build the body by hand (stdlib, no requests)."""
        boundary = "----loobricFormBoundary7MA4YWxkTrZu0gW"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/json\r\n\r\n"
            f"{backup_json}\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        return self._send("POST", "/backup/import", raw_body=body,
                          content_type=f"multipart/form-data; boundary={boundary}")

    # -- account -------------------------------------------------------------
    def reset_account(self) -> Dict[str, Any]:
        """Wipe all of the caller's tool data, keeping the account + keys."""
        return self._call("POST", "/account/reset", body={})


def _client() -> Client:
    """Build a Client from the CLI's current global config (BASE_URL/API_KEY/
    session). Used by the CLI shell so its commands exercise the same library
    other clients import."""
    return Client(base_url=BASE_URL, api_key=API_KEY, session_cookie=SESSION_COOKIE)


def register(email: str = None, password: str = None):
    """Register a new user account.
    
    First user registration is open. Subsequent registrations require admin auth.
    
    Args:
        email: User email address (will prompt if not provided)
        password: User password (will prompt if not provided)
    """
    # Prompt for email if not provided
    if not email:
        email = input("Email: ").strip()
        if not email:
            print("Error: Email is required", file=sys.stderr)
            sys.exit(1)
    
    # Prompt for password if not provided
    if not password:
        password = getpass.getpass("Password: ")
        if not password:
            print("Error: Password is required", file=sys.stderr)
            sys.exit(1)
        # Confirm password
        password_confirm = getpass.getpass("Confirm Password: ")
        if password != password_confirm:
            print("Error: Passwords do not match", file=sys.stderr)
            sys.exit(1)
    
    data = _client().register(email, password)
    print("✓ Registration successful!")
    print(f"  User: {data.get('email', 'Unknown')}")
    if data.get('id'):
        print(f"  User ID: {data.get('id')}")
    print("\nYou can now login with:")
    print(f"  loobric login {email}")


def login(email: str = None, password: str = None, base_url: str = None):
    """Authenticate and capture session cookie.
    
    Args:
        email: User email address (will prompt if not provided)
        password: User password (will prompt if not provided)
        base_url: Base URL (will prompt if not provided and not in session)
    """
    global SESSION_COOKIE, BASE_URL
    
    # Prompt for base URL if not provided
    if base_url:
        BASE_URL = base_url.rstrip("/")
    elif not BASE_URL:
        url_input = input("Base URL [http://127.0.0.1:8000]: ").strip()
        if not url_input:
            url_input = "http://127.0.0.1:8000"
        BASE_URL = url_input.rstrip("/")
    
    # Prompt for email if not provided
    if not email:
        email = input("Email: ").strip()
        if not email:
            print("Error: Email is required", file=sys.stderr)
            sys.exit(1)
    
    # Prompt for password if not provided
    if not password:
        password = getpass.getpass("Password: ")
        if not password:
            print("Error: Password is required", file=sys.stderr)
            sys.exit(1)
    
    data = _client().login(email, password)
    print("✓ Login successful!")
    print(f"  User: {data.get('email', 'Unknown')}")
    if data.get('id'):
        print(f"  User ID: {data.get('id')}")
    if not SESSION_COOKIE:
        print("⚠ Warning: No session cookie received. Authentication may have failed.", file=sys.stderr)
    else:
        save_session(email=email)
        print(f"  Session saved to {SESSION_FILE}")
        print(f"  Base URL: {BASE_URL}")


def create_key(
    name: str,
    scopes: Optional[str] = None,
    tags: Optional[str] = None,
    expires_at: Optional[str] = None
):
    """Create a new API key.
    
    Args:
        name: Descriptive name for the API key
        scopes: Space-separated list of scopes (e.g., 'read write:items')
        tags: Space-separated list of tags (e.g., 'production mill-3')
        expires_at: ISO 8601 datetime string for expiration
    """
    data = _client().create_key(
        name,
        scopes=scopes.strip().split() if scopes else None,
        tags=tags.strip().split() if tags else None,
        expires_at=expires_at,
    )

    # Output the plain API key first (for easy copying/piping)
    print(data.get('key'))
    
    # Then output details to stderr so they don't interfere with key capture
    print("\n✓ API key created successfully!", file=sys.stderr)
    print(f"  ID: {data.get('id')}", file=sys.stderr)
    print(f"  Name: {data.get('name')}", file=sys.stderr)
    print(f"  Scopes: {', '.join(data.get('scopes', []))}", file=sys.stderr)
    if data.get("tags"):
        print(f"  Tags: {', '.join(data.get('tags', []))}", file=sys.stderr)
    if data.get("expires_at"):
        print(f"  Expires: {data.get('expires_at')}", file=sys.stderr)
    print("\n⚠ Warning: Save the key now — it won't be shown again!", file=sys.stderr)
    print("\nTo use this key, set the environment variable:", file=sys.stderr)
    print(f"  export LOOBRIC_API_KEY={data.get('key')}", file=sys.stderr)


def list_keys():
    """List all API keys for the authenticated user."""
    data = _client().list_keys()

    if not data:
        print("No API keys found.")
        return
    
    print("\nAPI Keys:")
    print("-" * 80)
    for key in data:
        print(f"  ID: {key.get('id')}")
        print(f"  Name: {key.get('name')}")
        print(f"  Status: {'active' if key.get('is_active', True) else 'REVOKED'}")
        print(f"  Scopes: {', '.join(key.get('scopes', []))}")
        if key.get('tags'):
            print(f"  Tags: {', '.join(key.get('tags', []))}")
        if key.get('created_at'):
            print(f"  Created: {key.get('created_at')}")
        if key.get('expires_at'):
            print(f"  Expires: {key.get('expires_at')}")
        if key.get('last_used_at'):
            print(f"  Last Used: {key.get('last_used_at')}")
        print("-" * 80)


def revoke_key(key_id: str):
    """Revoke (delete) an API key.
    
    Args:
        key_id: The ID of the key to revoke
    """
    _client().revoke_key(key_id)
    print(f"✓ API key {key_id} revoked successfully.")


def list_tool_sets():
    """List the user's tool sets (v2 facade: /api/v1/tool-set-records)."""
    items = _client().list_tool_sets()

    if not items:
        print("No tool sets found.")
        return

    print(f"\nTool Sets ({len(items)}):")
    print("=" * 80)
    for tool_set in items:
        print(f"  ID: {_rid(tool_set)}")
        print(f"  Name: {_cval(tool_set, 'name')}")
        members = (tool_set.get("canonical") or {}).get("members") or []
        print(f"  Members: {len(members)} tool record(s)")
        if _ival(tool_set, 'updated_at'):
            print(f"  Updated: {_ival(tool_set, 'updated_at')}")
        print(f"  Version: {_ival(tool_set, 'version')}")
        print("=" * 80)


def show_tool_set(set_handle):
    """Show one tool set and its members: each member's number (with the source
    that vouches for it), the tool it points at, and that tool's geometry."""
    s = _resolve_tool_set(set_handle)
    members = (s.get("canonical") or {}).get("members") or []
    print(f"\nTool Set {_rid(s)}")
    print("=" * 78)
    print(f"  Name: {_cval(s, 'name') or '(unnamed)'}")
    machine = _cval(s, "machine_id")
    if machine:
        mname = {_rid(m): _cval(m, "name") for m in _client().list_machines()}.get(machine)
        print(f"  Machine: {mname or str(machine)[:8]} (member numbers inherited)")
    else:
        print("  Machine: not linked")
    print(f"  Members: {len(members)}")
    if not members:
        print("  (none yet — add tools with 'loobric add-to-set')")
        print("=" * 78)
        return
    tools = {_rid(t): t for t in _client().list_tool_records()}

    def _num(m):
        v = (m.get("number") or {}).get("value")
        return v if v is not None else float("inf")

    for m in sorted(members, key=_num):
        tid = m.get("tool_record_id")
        num = m.get("number") or {}
        t = tools.get(tid)
        tname = (_cval(t, "name") if t else None) or (str(tid)[:8] if tid else "?")
        ndisp = f"T{num['value']}" if num.get("value") is not None else "(no #)"
        dia = _cval(t, "geometry", "diameter") if t else None
        extra = f"  ⌀{dia}" if dia is not None else ""
        print(f"  {ndisp:>6}  {tname}{extra}  [{num.get('source', 'unknown')}]")
    print("=" * 78)


def add_to_set(set_handle, tools):
    """Add one or more tools to a tool set (existing members are kept)."""
    s = _resolve_tool_set(set_handle)
    recs = [_resolve_record(t) for t in tools]
    updated = _client().add_to_set(_rid(s), [_rid(r) for r in recs])
    n = len((updated.get("canonical") or {}).get("members") or [])
    label = _cval(s, "name") or str(_rid(s))[:8]
    names = ", ".join(_cval(r, "name") or str(_rid(r))[:8] for r in recs)
    print(f"✓ Added {len(recs)} tool(s) to '{label}' — {names}. Now {n} member(s).")


def remove_from_set(set_handle, tools):
    """Remove one or more tools from a tool set (the rest are kept)."""
    s = _resolve_tool_set(set_handle)
    recs = [_resolve_record(t) for t in tools]
    updated = _client().remove_from_set(_rid(s), [_rid(r) for r in recs])
    n = len((updated.get("canonical") or {}).get("members") or [])
    label = _cval(s, "name") or str(_rid(s))[:8]
    names = ", ".join(_cval(r, "name") or str(_rid(r))[:8] for r in recs)
    print(f"✓ Removed {len(recs)} tool(s) from '{label}' — {names}. Now {n} member(s).")


def list_pending():
    """List inbox items awaiting review (binding proposals).

    The inbox holds what sync could not decide on its own (G2): heuristic
    binding proposals awaiting a human. Resolve with `resolve <id> confirm`
    or `resolve <id> reject`.
    """
    items = _client().list_inbox()
    if not items:
        print("Inbox is empty - nothing pending.")
        return

    print(f"\n{len(items)} unrecognized machine tool(s) - best-guess matches below.")
    print()
    print("This is an identity question, NOT a conflict: the machine reported")
    print("tools the server doesn't recognize yet. Confirming or rejecting")
    print("overwrites NOTHING on either side.")
    print()
    print("  confirm = 'same tool': links the machine entry to the record so")
    print("            future changes route between them. Both keep their data.")
    print("  reject  = 'different tools': drops this suggestion permanently.")
    print("            The entry stays unbound and keeps syncing fine.")
    print("  unsure? = reject. A rejected pair can be linked manually later;")
    print("            a wrong confirm is currently hard to undo.")
    print("=" * 78)
    for item in items:
        entry = item.get("entry", {})
        proposed = item.get("proposed_instance", {})
        print(f"  ID: {item.get('id')[:8]}")
        print(f"  Machine entry: T{entry.get('tool_number')}")
        print(f"  Proposed match: {proposed.get('name')}")
        print(f"  Confidence: {item.get('confidence'):.0%} - {item.get('reason')}")
        print("-" * 78)
    print("Resolve with: loobric resolve <id> confirm|reject")


def resolve_pending(item_id: str, action: str):
    """Confirm or reject an inbox item.

    Accepts unique id prefixes (like git short SHAs): anything shorter than
    a full UUID is matched against the open inbox items client-side.

    Args:
        item_id: Inbox item id or unique prefix (from `pending`)
        action: "confirm" (bind entry to proposed record) or "reject"
    """
    # The confirm/reject responses no longer echo the entry/instance, so resolve
    # the item against the open inbox first — this both supports id prefixes and
    # gives us the details for a friendly message.
    open_items = _client().list_inbox()
    matches = [i for i in open_items if i.get("id", "").startswith(item_id)]
    if not matches:
        print(f"Error: no open inbox item starts with '{item_id}'", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(f"Error: '{item_id}' is ambiguous ({len(matches)} matches):", file=sys.stderr)
        for m in matches:
            entry = m.get("entry", {})
            print(f"  {m['id'][:8]}  T{entry.get('tool_number')} "
                  f"-> {m.get('proposed_instance', {}).get('name')}", file=sys.stderr)
        sys.exit(1)
    item = matches[0]
    entry = item.get("entry", {})
    proposed = item.get("proposed_instance", {})
    c = _client()
    (c.confirm_proposal if action == "confirm" else c.reject_proposal)(item["id"])
    if action == "confirm":
        print(f"Linked: T{entry.get('tool_number')} and '{proposed.get('name')}' are "
              f"now the same tool. No data was changed on either side; future "
              f"changes will route between them.")
    else:
        print(f"Dismissed: T{entry.get('tool_number')} is not '{proposed.get('name')}'. "
              f"This suggestion won't reappear; the entry stays unbound.")


# ---------------------------------------------------------------------------
# Sectioned-record accessors (docs/TOOL_SCHEMA.md). Every v2 record is the
# three-section shape {internal, canonical, clients}; the server-owned id lives
# at internal.id and every canonical field is a provenance-tagged leaf
# {value, unit?, source}. All listing/parsing routes through these so the CLI
# never reaches for the retired flat top-level fields again.
# ---------------------------------------------------------------------------

def _rid(record: Dict[str, Any]) -> Optional[str]:
    """The server-owned record id: internal.id."""
    return (record.get("internal") or {}).get("id")


def _ival(record: Dict[str, Any], key: str) -> Any:
    """An internal-section value (version, created_at, updated_at, machine_id)."""
    return (record.get("internal") or {}).get(key)


def _cfield(record: Dict[str, Any], *path: str) -> Dict[str, Any]:
    """The canonical leaf at a dotted path, e.g. ('geometry','diameter') ->
    {value, unit?, source}. Returns {} when absent."""
    node = record.get("canonical") or {}
    for p in path:
        node = (node or {}).get(p) or {}
    return node if isinstance(node, dict) else {}


def _cval(record: Dict[str, Any], *path: str) -> Any:
    """The `.value` of the canonical leaf at a dotted path (None when absent)."""
    return _cfield(record, *path).get("value")


def _match_id(items: List[Dict[str, Any]], prefix: str, label: str) -> Dict[str, Any]:
    """Resolve a possibly-abbreviated id against a list (git short-SHA style).

    An exact match wins outright; otherwise a unique prefix match is used.
    Exits with a helpful message on no/ambiguous match. Works on sectioned
    records: the id is internal.id and the human label is canonical.name.value.
    """
    exact = [i for i in items if _rid(i) == prefix]
    if exact:
        return exact[0]
    # An exact, unique name is as good as an id (humans think in names).
    by_name = [i for i in items if _cval(i, "name") == prefix]
    if len(by_name) == 1:
        return by_name[0]
    # Otherwise an id prefix, falling back to a name prefix.
    matches = [i for i in items if str(_rid(i) or "").startswith(prefix)]
    if not matches:
        matches = [i for i in items if str(_cval(i, "name") or "").startswith(prefix)]
    if not matches:
        print(f"Error: no {label} matches '{prefix}'", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(f"Error: '{prefix}' is ambiguous ({len(matches)} {label}s):", file=sys.stderr)
        for m in matches:
            print(f"  {str(_rid(m))[:8]}  {_cval(m, 'name') or ''}", file=sys.stderr)
        sys.exit(1)
    return matches[0]


def _resolve_machine(prefix: str) -> Dict[str, Any]:
    return _match_id(_client().list_machines(), prefix, "machine")


def _resolve_record(prefix: str) -> Dict[str, Any]:
    return _match_id(_client().list_tool_records(), prefix, "tool record")


def _resolve_tool_set(prefix: str) -> Dict[str, Any]:
    return _match_id(_client().list_tool_sets(), prefix, "tool set")


def _resolve_catalog(handle: str) -> Dict[str, Any]:
    """Resolve a catalog record by id / unique id-prefix / name / product_code
    (same shape as the other resolvers; on ambiguity it prints the candidates).
    A catalog record carries a manufacturer product_code, so humans reach for it
    as readily as the id or name — all three are first-class handles here."""
    items = _client().list_catalog_records()
    # An exact id, name, or product_code wins outright.
    for keyfn in (lambda r: _rid(r),
                  lambda r: _cval(r, "name"),
                  lambda r: _cval(r, "product_code")):
        exact = [r for r in items if keyfn(r) == handle]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            _ambiguous_catalog(handle, exact)
    # Otherwise a unique prefix on the id, the name, or the product_code.
    matches = [r for r in items
               if str(_rid(r) or "").startswith(handle)
               or str(_cval(r, "name") or "").startswith(handle)
               or str(_cval(r, "product_code") or "").startswith(handle)]
    if not matches:
        print(f"Error: no catalog record matches '{handle}'", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        _ambiguous_catalog(handle, matches)
    return matches[0]


def _ambiguous_catalog(handle: str, candidates: List[Dict[str, Any]]) -> None:
    print(f"Error: '{handle}' is ambiguous ({len(candidates)} catalog records):",
          file=sys.stderr)
    for c in candidates:
        print(f"  {str(_rid(c))[:8]}  {_cval(c, 'name') or ''}  "
              f"{_cval(c, 'product_code') or ''}".rstrip(), file=sys.stderr)
    sys.exit(1)


def _resolve_entry(machine: Dict[str, Any], tool_number: int) -> Dict[str, Any]:
    """Find a machine's tool-table entry record (a sectioned entry) by its
    observed tool number. v2 mutations (bind/unbind/delete-entry) key off
    the entry's own record id, not machine+tool_number, so callers resolve the
    entry first."""
    items = _client().list_entries(_rid(machine))
    for entry in items:
        if _cval(entry, "tool_number") == tool_number:
            return entry
    print(f"Error: machine '{_cval(machine, 'name')}' has no tool T{tool_number}",
          file=sys.stderr)
    sys.exit(1)


def _confirm(message: str, assume_yes: bool) -> bool:
    """Guard a destructive action. --yes skips; non-interactive requires it."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"{message}\nRefusing to proceed without confirmation; re-run with --yes.",
              file=sys.stderr)
        sys.exit(1)
    return input(f"{message} [y/N]: ").strip().lower() in ("y", "yes")


def list_machines():
    """List the user's machines (id, name, controller)."""
    items = _client().list_machines()
    if not items:
        print("No machines found.")
        return
    print(f"\nMachines ({len(items)}):")
    print("=" * 78)
    for m in items:
        print(f"  ID: {_rid(m)}")
        print(f"  Name: {_cval(m, 'name')}")
        if _cval(m, "controller_type"):
            print(f"  Controller: {_cval(m, 'controller_type')}")
        print("=" * 78)


def list_tools():
    """List the user's tool instance records (the public facade)."""
    items = _client().list_tool_records()
    if not items:
        print("No tool records found.")
        return
    print(f"\nTool Records ({len(items)}):")
    print("=" * 78)
    for t in items:
        print(f"  ID: {_rid(t)}")
        print(f"  Name: {_cval(t, 'name')}")
        bits = []
        shape = _cval(t, "geometry", "shape")
        if shape:
            bits.append(str(shape))
        dia = _cfield(t, "geometry", "diameter")
        if dia.get("value") is not None:
            bits.append(f"⌀{dia['value']}{dia.get('unit', '')}")
        if bits:
            print(f"  Geometry: {' · '.join(bits)}")
        print("=" * 78)


def show_tool_table(machine_id: str):
    """List a machine's tool-table entries and their bind state."""
    machine = _resolve_machine(machine_id)
    name = _cval(machine, "name")
    items = _client().list_entries(_rid(machine))
    if not items:
        print(f"{name}: empty tool table.")
        return
    plural = "entries" if len(items) != 1 else "entry"
    print(f"\n{name} tool table ({len(items)} {plural}):")
    print("=" * 78)
    for e in items:
        rec_id = _cval(e, "bound_instance_id")
        state = f"bound -> {str(rec_id)[:8]}" if rec_id else "unbound"
        dia = _cval(e, "offsets", "diameter")
        line = f"  T{_cval(e, 'tool_number')}: {_cval(e, 'description') or '—'}"
        if dia is not None:
            line += f"  ⌀{dia}"
        print(f"{line}  [{state}]")
    print("=" * 78)


def link_machine(set_id: str, machine_id: str, actor: str = "human@cli"):
    """Link a tool set to a machine: member numbers are inherited from its entries."""
    tool_set = _resolve_tool_set(set_id)
    machine = _resolve_machine(machine_id)
    _client().link_set_to_machine(_rid(tool_set), _rid(machine), actor)
    set_name = _cval(tool_set, "name") or str(_rid(tool_set))[:8]
    print(f"✓ Tool set '{set_name}' now linked to machine '{_cval(machine, 'name')}'.")


def delete_machine(machine_id: str, assume_yes: bool = False):
    """Delete a machine and its tool-table entries (tool records untouched)."""
    machine = _resolve_machine(machine_id)
    name = _cval(machine, "name")
    if not _confirm(
        f"Delete machine '{name}' and its tool-table entries?", assume_yes
    ):
        print("Aborted.")
        return
    _client().delete_machine(_rid(machine))
    print(f"✓ Deleted machine '{name}'. Tool records were not affected.")


def delete_tool(record_id: str, assume_yes: bool = False):
    """Delete a tool record; entries bound to it are unbound, not orphaned."""
    rec = _resolve_record(record_id)
    name = _cval(rec, "name")
    if not _confirm(
        f"Delete tool record '{name}'? Bound machine entries will be unbound "
        f"(their data stays on the machine).", assume_yes
    ):
        print("Aborted.")
        return
    _client().delete_tool_record(_rid(rec))
    print(f"✓ Deleted tool record '{name}'. Any bound entries were unbound; "
          f"their data stays on the machine.")


def delete_entry(machine_id: str, tool_number: int, assume_yes: bool = False):
    """Remove a machine-reported tool-table entry by tool number."""
    machine = _resolve_machine(machine_id)
    name = _cval(machine, "name")
    entry = _resolve_entry(machine, tool_number)
    if not _confirm(
        f"Remove T{tool_number} from '{name}'?", assume_yes
    ):
        print("Aborted.")
        return
    _client().delete_entry(_rid(entry))
    print(f"✓ Removed T{tool_number} from '{name}'. "
          f"If the controller pushes it again, it returns.")


def bind_entry(machine_id: str, tool_number: int, record_id: str):
    """Link an unbound entry to an owned tool record (overwrites nothing)."""
    machine = _resolve_machine(machine_id)
    m_name = _cval(machine, "name")
    entry = _resolve_entry(machine, tool_number)
    rec = _resolve_record(record_id)
    _client().bind_entry(_rid(entry), instance_id=_rid(rec))
    print(f"✓ Linked T{tool_number} @ {m_name} -> '{_cval(rec, 'name')}'. "
          f"Nothing was overwritten on either side.")


def unbind_entry(machine_id: str, tool_number: int):
    """Unbind an entry; it keeps its data and becomes eligible for suggestions."""
    machine = _resolve_machine(machine_id)
    m_name = _cval(machine, "name")
    entry = _resolve_entry(machine, tool_number)
    _client().unbind_entry(_rid(entry))
    print(f"✓ Unbound T{tool_number} @ {m_name}. The entry keeps its data.")


def create_record(args):
    """Context-aware create-record: from a machine entry (-> BOUND instance) or
    from a catalog record (-> UNBOUND instance). The two paths are mutually
    exclusive; the outcome message names which one ran."""
    qa_path = getattr(args, "qa", None)
    cert = getattr(args, "cert", None)
    if getattr(args, "from_catalog", None):
        if args.machine or args.tool_number is not None:
            print("Error: --from-catalog cannot be combined with MACHINE/TOOL_NUMBER",
                  file=sys.stderr)
            sys.exit(1)
        create_record_from_catalog(args.from_catalog, args.name,
                                   qa_path=qa_path, cert=cert)
    else:
        if qa_path or cert:
            print("Error: --qa/--cert are only valid with --from-catalog "
                  "(manufacturer QA is recorded when creating from a catalog record)",
                  file=sys.stderr)
            sys.exit(1)
        if not args.machine or args.tool_number is None:
            print("Error: create-record needs MACHINE TOOL_NUMBER (entry form) "
                  "or --from-catalog CATALOG", file=sys.stderr)
            sys.exit(1)
        create_record_from_entry(args.machine, args.tool_number, args.name)


def create_record_from_entry(machine_id: str, tool_number: int, name: str = None):
    """Mint a new instance record from a entry's observations and bind it, in one
    step. The bind endpoint mints a new instance when no instance_id is given."""
    machine = _resolve_machine(machine_id)
    m_name = _cval(machine, "name")
    entry = _resolve_entry(machine, tool_number)
    result = _client().bind_entry(_rid(entry), name=name)
    bound = (result.get("canonical", {}).get("bound_instance_id") or {}).get("value")
    rec_id = str(bound or "")[:8]
    print(f"✓ Created a record from T{tool_number} @ {m_name} and bound it "
          f"(record {rec_id}).")


def create_record_from_catalog(catalog_handle: str, name: str = None,
                               qa_path: str = None, cert: str = None):
    """Create a new UNBOUND instance from a catalog record (the catalog->instance
    door). The server stamps the catalog_type_id link as asserted:<requester>;
    the instance is left unbound (it sits in no machine entry). The name defaults
    to the catalog record's name unless --name overrides it.

    Optional manufacturer QA: --qa is a geometry-shaped JSON file ({diameter:
    {value, unit}, ...}) and --cert its certificate/serial. --cert is required
    iff --qa is given; the server stamps each measured field
    observed:manufacturer@<serial>."""
    if qa_path and not cert:
        print("Error: --qa requires --cert (the certificate/serial the QA "
              "measurements are recorded against)", file=sys.stderr)
        sys.exit(1)
    if cert and not qa_path:
        print("Error: --cert requires --qa (a certificate needs QA measurements "
              "to certify)", file=sys.stderr)
        sys.exit(1)
    qa = None
    if qa_path:
        with open(qa_path) as f:
            text = f.read().strip()
        try:
            qa = json.loads(text) if text else {}
        except json.JSONDecodeError as e:
            print(f"Error: invalid JSON in --qa file: {e}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(qa, dict):
            print("Error: --qa JSON must be a geometry-shaped object", file=sys.stderr)
            sys.exit(1)
    catalog = _resolve_catalog(catalog_handle)
    cat_name = _cval(catalog, "name")
    rec = _client().create_instance_from_catalog(_rid(catalog), name=name,
                                                 qa=qa, cert=cert)
    inst_id = str(_rid(rec) or "")[:8]
    qa_note = f" with manufacturer QA ({cert})" if qa else ""
    print(f"✓ Created instance {inst_id} from {cat_name}{qa_note} — unbound "
          f"(no machine entry yet).")


def create_machine(name, controller=None):
    """Create a machine and assert its name (and optional controller)."""
    rec = _client().create_machine(name=name, controller_type=controller)
    print(f"✓ Created machine '{name}' ({str(_rid(rec))[:8]}).")
    if controller:
        print(f"  Controller: {controller}")


def create_set(name):
    """Create a tool set and assert its name."""
    rec = _client().create_tool_set(name=name)
    print(f"✓ Created tool set '{name}' ({str(_rid(rec))[:8]}).")


def _load_catalog_fields(file=None) -> Dict[str, Any]:
    """Read the nominal-fields JSON for a catalog record: from --file, else from
    stdin (the '-' convention). An empty/absent stream is an empty object — the
    convenience flags may supply every field by hand. The JSON carries values +
    units; provenance is never in it (the server stamps it from --source)."""
    if file:
        with open(file) as f:
            text = f.read()
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = ""
    text = text.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON for catalog record: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print("Error: catalog record JSON must be an object", file=sys.stderr)
        sys.exit(1)
    return data


def _flag_field(fields: Dict[str, Any], key: str, value: Any) -> None:
    """A convenience flag supplies/overrides a top-level nominal field's value,
    keeping any unit already present in the JSON."""
    if value is None:
        return
    leaf = fields.get(key)
    if isinstance(leaf, dict):
        leaf["value"] = value
    else:
        fields[key] = {"value": value}


def create_catalog_record(source, file=None, name=None, manufacturer=None,
                          product_code=None, diameter=None, flutes=None):
    """Create a catalog record (a ToolCatalogRecord) in one atomic, audited call.

    Nominal fields arrive as JSON on stdin (the '-' convention) or via --file,
    with thin convenience flags (--name/--manufacturer/--product-code/--diameter/
    --flutes) for the by-hand case. --source is the declared actor; the server
    stamps `asserted:<source>` on every field — the client never writes
    provenance. name, manufacturer and product_code are required (the identity
    floor)."""
    fields = _load_catalog_fields(file)
    _flag_field(fields, "name", name)
    _flag_field(fields, "manufacturer", manufacturer)
    _flag_field(fields, "product_code", product_code)
    if diameter is not None:
        fields.setdefault("geometry", {})["diameter"] = {"value": diameter, "unit": "mm"}
    if flutes is not None:
        fields.setdefault("geometry", {})["flutes"] = {"value": flutes}
    rec = _client().create_catalog_record(source=source, fields=fields)
    print(f"✓ Created catalog record '{_cval(rec, 'name')}' "
          f"({str(_rid(rec))[:8]}). Every field carries source "
          f"'asserted:{source}'.")


def list_catalog_records():
    """List the user's catalog records (ToolCatalogRecords)."""
    items = _client().list_catalog_records()
    if not items:
        print("No catalog records found.")
        return
    print(f"\nCatalog Records ({len(items)}):")
    print("=" * 78)
    for c in items:
        print(f"  ID: {_rid(c)}")
        print(f"  Name: {_cval(c, 'name')}")
        ident = "  ".join(p for p in (_cval(c, "manufacturer"),
                                      _cval(c, "product_code")) if p)
        if ident:
            print(f"  {ident}")
        dia = _cfield(c, "geometry", "diameter")
        if dia.get("value") is not None:
            print(f"  Geometry: ⌀{dia['value']}{dia.get('unit', '')}")
        print("=" * 78)


def _print_field(label, leaf, indent="  "):
    """Show one canonical field with its provenance — value, optional unit, and
    the source it came from (the whole point: fabrication stays visible)."""
    value = leaf.get("value")
    unit = leaf.get("unit")
    disp = "—" if value is None else (f"{value} {unit}" if unit else f"{value}")
    print(f"{indent}{label}: {disp}  [{leaf.get('source', '')}]")


def show_catalog_record(catalog):
    """Show one catalog record with full provenance — every field with its source."""
    rec = _resolve_catalog(catalog)
    canonical = rec.get("canonical") or {}
    print(f"\nCatalog Record {_rid(rec)}")
    print("=" * 78)
    for key in ("name", "manufacturer", "product_code", "item_type"):
        leaf = canonical.get(key)
        if isinstance(leaf, dict) and "source" in leaf:
            _print_field(key, leaf)
    geometry = canonical.get("geometry") or {}
    present = {k: v for k, v in geometry.items() if isinstance(v, dict)}
    if present:
        print("  geometry:")
        for gkey, leaf in present.items():
            _print_field(gkey, leaf, indent="    ")
    print("=" * 78)


def _parse_entry(spec):
    """Parse a --entry spec 'N[:description[:diameter]]' into a tool-table entry."""
    parts = spec.split(":")
    try:
        tool_number = int(parts[0])
    except ValueError:
        print(f"Error: bad --entry '{spec}': first field must be a tool number",
              file=sys.stderr)
        sys.exit(1)
    entry = {"tool_number": tool_number}
    if len(parts) > 1 and parts[1]:
        entry["description"] = parts[1]
    if len(parts) > 2 and parts[2]:
        try:
            entry["offsets"] = {"diameter": float(parts[2]), "diameter_unit": "mm"}
        except ValueError:
            print(f"Error: bad --entry '{spec}': diameter must be a number", file=sys.stderr)
            sys.exit(1)
    return entry


def push_table(machine_id, entry_specs, client="loobric", snapshot=False):
    """Push a tool table to a machine — the controller-side sync (stand-in for a
    real controller client). Each --entry is 'N[:description[:diameter]]'."""
    machine = _resolve_machine(machine_id)
    name = _cval(machine, "name") or str(_rid(machine))[:8]
    entries = [_parse_entry(s) for s in (entry_specs or [])]
    res = _client().sync_entries(
        _rid(machine), entries, client=client, machine_name=name,
        mode="snapshot" if snapshot else "merge",
    )
    n = len(res.get("items", []))
    print(f"✓ Pushed {n} tool-table entr{'y' if n == 1 else 'ies'} to '{name}' "
          f"as client '{client}'.")
    removed = res.get("removed_tool_numbers", [])
    if removed:
        print(f"  Removed (snapshot): {', '.join('T' + str(t) for t in removed)}")


def reset_account(assume_yes=False):
    """Wipe ALL tool data for this account (keeps login + API keys)."""
    if not _confirm(
        "Delete ALL tool data for this account (records, sets, machines, "
        "entries)? Login and API keys are kept.", assume_yes
    ):
        print("Aborted.")
        return
    res = _client().reset_account()
    deleted = res.get("deleted", {}) or {}
    total = sum(deleted.values()) if isinstance(deleted, dict) else 0
    detail = ", ".join(f"{k}={v}" for k, v in deleted.items()) if deleted else ""
    print(f"✓ Account reset — deleted {total} item(s).{(' ' + detail) if detail else ''}")


def whoami():
    """Show which server we're talking to, the authenticated account, and the
    server's build identity (the fastest 'is this the server/code I expect?'
    check)."""
    client = _client()
    print(f"  Server: {client.base_url or '(none set)'}")
    me = client.whoami()
    print(f"  Email:  {me.get('email')}")
    print(f"  Role:   {me.get('role')}")
    print(f"  Admin:  {me.get('is_admin')}")
    if me.get("id"):
        print(f"  ID:     {me.get('id')}")
    # Server build identity — an older server has no /version endpoint, which is
    # itself the answer to "is it running my code?"
    try:
        v = client.server_version()
        print(f"  Build:  {v.get('version', '?')} ({v.get('commit', '?')})")
    except NotFound:
        print("  Build:  unknown — older server with no /version endpoint")


def list_audit(limit=50):
    """Show recent audit-log entries (who changed what, when)."""
    data = _client().list_audit_logs()
    items = data.get("logs", []) if isinstance(data, dict) else (data or [])
    if not items:
        print("No audit entries.")
        return
    for e in list(items)[:limit]:
        when = e.get("created_at") or e.get("timestamp") or ""
        print(f"  {when}  {e.get('operation', ''):8}  "
              f"{e.get('entity_type', '')}  {str(e.get('entity_id', ''))[:8]}")


def backup_export(path=None):
    """Export a full account backup (admin)."""
    data = _client().export_backup()
    text = data if isinstance(data, str) else json.dumps(data, indent=2)
    if path:
        with open(path, "w") as f:
            f.write(text)
        print(f"✓ Backup written to {path}.")
    else:
        print(text)


def backup_import(path):
    """Restore an account backup from a JSON file (admin)."""
    with open(path) as f:
        backup_json = f.read()
    _client().import_backup(backup_json)
    print(f"✓ Backup imported from {path}.")


def assert_canonical(resource, record_id, path, value, actor="human@cli"):
    """Assert a canonical field (the assert door): loobric assert <resource> <id> <path> <value>."""
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed = value
    rec = _client().assert_field(resource, record_id, path, parsed, actor=actor)
    print(f"✓ Asserted {path}={parsed!r} on {resource}/{str(_rid(rec))[:8]}.")


def ping():
    """Check server health/connectivity."""
    conn = get_connection()
    path = "/api/health"
    headers = {
        "Accept": "application/json",
    }
    
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        status = response.status
        content = response.read().decode("utf-8")
        
        if 200 <= status < 300:
            data = json.loads(content) if content.strip() else {}
            print("✓ Server is healthy")
            print(f"  Status: {data.get('status', 'ok')}")
            if data.get('version'):
                print(f"  Version: {data.get('version')}")
            print(f"  URL: {BASE_URL}")
        else:
            print(f"✗ Server returned HTTP {status}")
            print(f"  URL: {BASE_URL}")
            sys.exit(1)
    except Exception as e:
        print(f"✗ Server unreachable at {BASE_URL}")
        print(f"  Error: {e}")
        sys.exit(1)
    finally:
        conn.close()


def logout():
    """End session."""
    global SESSION_COOKIE
    if not SESSION_COOKIE:
        print("No active session to logout.")
        return

    _client().logout()
    SESSION_COOKIE = None
    clear_session()
    print("✓ Logged out successfully.")
    print(f"  Session cleared from {SESSION_FILE}")


def _run(fn, *args, **kwargs):
    """Run a CLI command, turning library errors into a message + exit code.
    The library raises; the CLI shell is where that becomes user-facing output."""
    try:
        return fn(*args, **kwargs)
    except LoobricError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Loobric API Key Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # First time setup - register a user on fresh database
  loobric --base-url http://127.0.0.1:8000 register admin@example.com
  
  # Interactive login (prompts for URL, email, password)
  loobric --login
  
  # Login with specific URL and email
  loobric --base-url http://127.0.0.1:8000 login user@example.com
  
  # After login, base URL is saved - just run commands
  loobric list-keys
  loobric create-key "My Key" --scopes "read write:items"
  
  # Create a key with tags and expiration
  loobric create-key "Backup Script" \\
    --scopes "read" --tags "backup production" --expires-at "2025-12-31T23:59:59Z"
  
  # Revoke an API key
  loobric revoke-key <key_id>

  # The sync loop, end to end (machine ids/names accept unique prefixes)
  loobric create-machine millstone --controller linuxcnc
  loobric push millstone --entry "3:1/4 downcut:6.35" --entry "7:vee:6.0"
  loobric tool-table millstone
  loobric create-record millstone 3 --name "1/4 downcut"   # mint + bind T3
  loobric bind millstone 7 <record>     # or bind an existing record
  loobric unbind millstone 7

  # Catalog records -> a physical instance (unbound: not in any machine yet)
  loobric create-record --from-catalog B201            # by product code
  loobric create-record --from-catalog B201 --name "1/4 downcut, lot 7"
  loobric create-record --from-catalog B201 \\
    --qa qa.json --cert "kennametal@SN12345"           # + manufacturer QA

  # Inspect
  loobric list-machines
  loobric list-tools
  loobric list-tool-sets
  loobric pending                       # binding proposals awaiting review
  loobric audit --limit 20

  # Tool sets
  loobric create-set "Aluminum job"
  loobric link-machine "Aluminum job" millstone

  # Canonical assert door
  loobric assert tool-set-records <id> name "Aluminum job v2"

  # Admin / housekeeping
  loobric reset --yes                   # wipe all tool data (keeps login + keys)
  loobric backup-export --out backup.json
  loobric backup-import backup.json
  loobric delete-machine millstone --yes
  loobric ping
  loobric logout

Environment Variables:
  LOOBRIC_BASE_URL - Default base URL (can override with --base-url)
  LOOBRIC_API_KEY  - API key for authentication (alternative to login)
"""
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Interactive login (prompts for URL, email, password)"
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="End current session (clears session cookie)"
    )
    parser.add_argument(
        "--api-key",
        help="API key for authentication (overrides session cookie and $LOOBRIC_API_KEY)"
    )
    parser.add_argument(
        "--base-url", "-b",
        default=os.environ.get("LOOBRIC_BASE_URL"),
        help="Base API URL (default: $LOOBRIC_BASE_URL or saved session)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    subparsers = parser.add_subparsers(dest="command", required=False)

    # === register ===
    register_parser = subparsers.add_parser(
        "register",
        help="Register a new user account",
        description="Create a new user account. Use this for the first user on a fresh database."
    )
    register_parser.add_argument("email", nargs="?", help="User email address (will prompt if not provided)")
    register_parser.add_argument(
        "--password", "-p",
        help="Password (will be prompted if not provided)"
    )
    register_parser.set_defaults(func=lambda args: register(
        email=args.email,
        password=args.password
    ))

    # === login ===
    login_parser = subparsers.add_parser(
        "login",
        help="Authenticate with email/password",
        description="Authenticate with email and password to create a session."
    )
    login_parser.add_argument("email", nargs="?", help="User email address (will prompt if not provided)")
    login_parser.add_argument(
        "--password", "-p",
        help="Password (will be prompted if not provided)"
    )
    login_parser.add_argument(
        "--url",
        help="Base URL (will be prompted if not provided)"
    )
    login_parser.set_defaults(func=lambda args: login(
        email=args.email,
        password=args.password,
        base_url=args.url or args.base_url
    ))

    # === create-key ===
    create_parser = subparsers.add_parser("create-key", help="Generate a new API key")
    create_parser.add_argument("name", help="Name for the API key (e.g., 'My App')")
    create_parser.add_argument("--scopes", help="Space-separated scopes, e.g., 'read write'")
    create_parser.add_argument("--tags", help="Space-separated tags, e.g., 'production mill-3'")
    create_parser.add_argument("--expires-at", help="ISO datetime, e.g., 2025-12-31T23:59:59Z")
    create_parser.set_defaults(func=lambda args: create_key(
        args.name, args.scopes, args.tags, args.expires_at
    ))

    # === list-keys ===
    list_parser = subparsers.add_parser("list-keys", help="List all API keys")
    list_parser.set_defaults(func=lambda _: list_keys())

    # === revoke-key ===
    revoke_parser = subparsers.add_parser("revoke-key", help="Revoke an API key")
    revoke_parser.add_argument("key_id", help="ID of the key to revoke")
    revoke_parser.set_defaults(func=lambda args: revoke_key(args.key_id))

    # === list-tool-sets ===
    list_tool_sets_parser = subparsers.add_parser(
        "list-tool-sets",
        help="List tool sets",
        description="List the user's tool sets (named collections of ToolRecords)."
    )
    list_tool_sets_parser.set_defaults(func=lambda args: list_tool_sets())

    # === show-tool-set ===
    show_tool_set_parser = subparsers.add_parser(
        "show-tool-set",
        help="Show one tool set and its members",
        description="Show a tool set's members — each member's number (with the "
                    "source that vouches for it), the tool, and its geometry. SET "
                    "resolves by id, name, or unique prefix.",
    )
    show_tool_set_parser.add_argument("set", help="Tool set id, name, or unique prefix")
    show_tool_set_parser.set_defaults(func=lambda args: show_tool_set(args.set))

    # === link-machine ===
    link_parser = subparsers.add_parser(
        "link-machine",
        help="Link a tool set to a machine",
        description="Assert a tool set's machine_id so its member numbers are "
                    "inherited from that machine's tool table."
    )
    link_parser.add_argument("set", help="Tool set id or unique prefix")
    link_parser.add_argument("machine", help="Machine id or unique prefix")
    link_parser.set_defaults(func=lambda args: link_machine(args.set, args.machine))

    # === pending / resolve (v2 inbox) ===
    pending_parser = subparsers.add_parser(
        "pending", help="List inbox items awaiting review (binding proposals)"
    )
    pending_parser.set_defaults(func=lambda _: list_pending())

    resolve_parser = subparsers.add_parser(
        "resolve", help="Confirm or reject an inbox item"
    )
    resolve_parser.add_argument("item_id", help="Inbox item id or unique prefix (see 'pending')")
    resolve_parser.add_argument("action", choices=["confirm", "reject"])
    resolve_parser.set_defaults(func=lambda args: resolve_pending(
        args.item_id, args.action
    ))

    # === machines / tool records / entries (v2 management) ===
    list_machines_parser = subparsers.add_parser(
        "list-machines", help="List machines (id, name, controller)"
    )
    list_machines_parser.set_defaults(func=lambda _: list_machines())

    list_tools_parser = subparsers.add_parser(
        "list-tools", help="List tool records (the public facade)"
    )
    list_tools_parser.set_defaults(func=lambda _: list_tools())

    tool_table_parser = subparsers.add_parser(
        "tool-table", help="Show a machine's tool-table entries and bind state"
    )
    tool_table_parser.add_argument("machine", help="Machine id or unique prefix")
    tool_table_parser.set_defaults(func=lambda args: show_tool_table(args.machine))

    delete_machine_parser = subparsers.add_parser(
        "delete-machine",
        help="Delete a machine and its tool-table entries (records untouched)",
    )
    delete_machine_parser.add_argument("machine", help="Machine id or unique prefix")
    delete_machine_parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip the confirmation prompt"
    )
    delete_machine_parser.set_defaults(func=lambda args: delete_machine(args.machine, args.yes))

    delete_tool_parser = subparsers.add_parser(
        "delete-tool",
        help="Delete a tool record (bound entries are unbound, not orphaned)",
    )
    delete_tool_parser.add_argument("record", help="Tool record id or unique prefix")
    delete_tool_parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip the confirmation prompt"
    )
    delete_tool_parser.set_defaults(func=lambda args: delete_tool(args.record, args.yes))

    delete_entry_parser = subparsers.add_parser(
        "delete-entry", help="Remove a machine-reported tool-table entry"
    )
    delete_entry_parser.add_argument("machine", help="Machine id or unique prefix")
    delete_entry_parser.add_argument("tool_number", type=int, help="Tool number (e.g. 3)")
    delete_entry_parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip the confirmation prompt"
    )
    delete_entry_parser.set_defaults(
        func=lambda args: delete_entry(args.machine, args.tool_number, args.yes)
    )

    bind_parser = subparsers.add_parser(
        "bind", help="Link an unbound entry to a tool record"
    )
    bind_parser.add_argument("machine", help="Machine id or unique prefix")
    bind_parser.add_argument("tool_number", type=int, help="Tool number (e.g. 3)")
    bind_parser.add_argument("record", help="Tool record id or unique prefix")
    bind_parser.set_defaults(
        func=lambda args: bind_entry(args.machine, args.tool_number, args.record)
    )

    unbind_parser = subparsers.add_parser(
        "unbind", help="Unbind an entry (it keeps its data)"
    )
    unbind_parser.add_argument("machine", help="Machine id or unique prefix")
    unbind_parser.add_argument("tool_number", type=int, help="Tool number (e.g. 3)")
    unbind_parser.set_defaults(
        func=lambda args: unbind_entry(args.machine, args.tool_number)
    )

    create_record_parser = subparsers.add_parser(
        "create-record",
        help="Create a tool instance — from an entry (bound) or a catalog (unbound)",
        description="Two context-aware forms. MACHINE TOOL_NUMBER creates an "
                    "instance from a machine entry and BINDS it to that position. "
                    "--from-catalog creates an instance from a catalog record and "
                    "leaves it UNBOUND (a catalog is not a machine position).",
    )
    create_record_parser.add_argument(
        "machine", nargs="?", help="Machine id or unique prefix (entry form)"
    )
    create_record_parser.add_argument(
        "tool_number", nargs="?", type=int, help="Tool number, e.g. 3 (entry form)"
    )
    create_record_parser.add_argument(
        "--from-catalog", metavar="CATALOG",
        help="Create an UNBOUND instance from a catalog record "
             "(id/prefix/name/product_code)",
    )
    create_record_parser.add_argument(
        "--name",
        help="Name for the new instance (entry form: defaults to the entry "
             "description; catalog form: defaults to the catalog record's name)",
    )
    create_record_parser.add_argument(
        "--qa", metavar="FILE",
        help="Manufacturer QA: a geometry-shaped JSON file ({diameter:{value,"
             "unit}, ...}) measured on the certified tool (catalog form only; "
             "requires --cert)",
    )
    create_record_parser.add_argument(
        "--cert",
        help="Certificate/serial the --qa measurements are recorded against; the "
             "server stamps them observed:manufacturer@<serial> (required iff --qa)",
    )
    create_record_parser.set_defaults(func=create_record)

    # === create-machine ===
    create_machine_parser = subparsers.add_parser(
        "create-machine", help="Create a machine and name it"
    )
    create_machine_parser.add_argument("name", help="Machine name (e.g. millstone)")
    create_machine_parser.add_argument("--controller", help="Controller type (e.g. linuxcnc)")
    create_machine_parser.set_defaults(
        func=lambda args: create_machine(args.name, args.controller)
    )

    # === create-set ===
    create_set_parser = subparsers.add_parser(
        "create-set", help="Create a tool set and name it"
    )
    create_set_parser.add_argument("name", help="Tool set name")
    create_set_parser.set_defaults(func=lambda args: create_set(args.name))

    add_to_set_parser = subparsers.add_parser(
        "add-to-set", help="Add one or more tools to a tool set",
        description="Append tool record(s) to a set's membership. Existing "
                    "members (and their numbers) are kept; a tool already in the "
                    "set is skipped. SET and each TOOL resolve by id, name, or "
                    "unique prefix.",
    )
    add_to_set_parser.add_argument("set", help="Tool set id, name, or unique prefix")
    add_to_set_parser.add_argument("tool", nargs="+",
                                   help="Tool record id(s), name(s), or unique prefix(es)")
    add_to_set_parser.set_defaults(func=lambda args: add_to_set(args.set, args.tool))

    remove_from_set_parser = subparsers.add_parser(
        "remove-from-set", help="Remove one or more tools from a tool set",
        description="Remove tool record(s) from a set's membership; the rest are "
                    "kept. SET and each TOOL resolve by id, name, or unique prefix.",
    )
    remove_from_set_parser.add_argument("set", help="Tool set id, name, or unique prefix")
    remove_from_set_parser.add_argument("tool", nargs="+",
                                        help="Tool record id(s), name(s), or unique prefix(es)")
    remove_from_set_parser.set_defaults(
        func=lambda args: remove_from_set(args.set, args.tool))

    # === create-catalog-record ===
    create_catalog_parser = subparsers.add_parser(
        "create-catalog-record",
        help="Create a catalog record from JSON (stdin/--file) or flags",
        description="Create a ToolCatalogRecord in one atomic, audited call. "
                    "Nominal fields come from JSON on stdin (the '-' convention) "
                    "or --file, plus convenience flags. --source is the declared "
                    "actor; the server stamps 'asserted:<source>' on every field.",
    )
    create_catalog_parser.add_argument(
        "input", nargs="?",
        help="'-' to read JSON from stdin (default when piped), or a path",
    )
    create_catalog_parser.add_argument(
        "--source", required=True,
        help="Declared actor, e.g. manufacturer:kennametal "
             "(server stamps asserted:<source> on every field)",
    )
    create_catalog_parser.add_argument("--file", help="Read nominal fields JSON from this file")
    create_catalog_parser.add_argument("--name", help="Catalog record name")
    create_catalog_parser.add_argument("--manufacturer", help="Manufacturer")
    create_catalog_parser.add_argument("--product-code", help="Manufacturer product code")
    create_catalog_parser.add_argument("--diameter", type=float, help="Nominal diameter (mm)")
    create_catalog_parser.add_argument("--flutes", type=int, help="Flute count")
    create_catalog_parser.set_defaults(func=lambda args: create_catalog_record(
        source=args.source,
        file=args.file or (args.input if args.input and args.input != "-" else None),
        name=args.name, manufacturer=args.manufacturer,
        product_code=args.product_code, diameter=args.diameter, flutes=args.flutes,
    ))

    # === list-catalog-records ===
    list_catalog_parser = subparsers.add_parser(
        "list-catalog-records", help="List catalog records (ToolCatalogRecords)"
    )
    list_catalog_parser.set_defaults(func=lambda _: list_catalog_records())

    # === show-catalog-record ===
    show_catalog_parser = subparsers.add_parser(
        "show-catalog-record", help="Show one catalog record with provenance"
    )
    show_catalog_parser.add_argument(
        "catalog", help="Catalog record id, unique prefix, name, or product_code"
    )
    show_catalog_parser.set_defaults(func=lambda args: show_catalog_record(args.catalog))

    # === push ===
    push_parser = subparsers.add_parser(
        "push", help="Push a tool table to a machine (controller-side sync)"
    )
    push_parser.add_argument("machine", help="Machine id, name, or unique prefix")
    push_parser.add_argument(
        "--entry", action="append", dest="entries", metavar="N[:DESC[:DIA]]",
        help="A tool-table entry, e.g. --entry '3:1/4 downcut:6.35' (repeatable)"
    )
    push_parser.add_argument("--client", default="loobric",
                             help="Client name stamped on the push (default: loobric)")
    push_parser.add_argument("--snapshot", action="store_true",
                             help="Snapshot mode: entries absent from this push are removed")
    push_parser.set_defaults(
        func=lambda args: push_table(args.machine, args.entries, args.client, args.snapshot)
    )

    # === reset ===
    reset_parser = subparsers.add_parser(
        "reset", help="Wipe all tool data for this account (keeps login + API keys)"
    )
    reset_parser.add_argument("--yes", "-y", action="store_true",
                              help="Skip the confirmation prompt")
    reset_parser.set_defaults(func=lambda args: reset_account(args.yes))

    # === whoami ===
    whoami_parser = subparsers.add_parser("whoami", help="Show the authenticated account")
    whoami_parser.set_defaults(func=lambda _: whoami())

    # === audit ===
    audit_parser = subparsers.add_parser("audit", help="Show recent audit-log entries")
    audit_parser.add_argument("--limit", type=int, default=50, help="Max entries (default 50)")
    audit_parser.set_defaults(func=lambda args: list_audit(args.limit))

    # === backup-export / backup-import ===
    backup_export_parser = subparsers.add_parser(
        "backup-export", help="Export a full account backup (admin)")
    backup_export_parser.add_argument("--out", help="Write to this file (default: stdout)")
    backup_export_parser.set_defaults(func=lambda args: backup_export(args.out))

    backup_import_parser = subparsers.add_parser(
        "backup-import", help="Restore an account backup from a JSON file (admin)")
    backup_import_parser.add_argument("file", help="Path to the backup JSON file")
    backup_import_parser.set_defaults(func=lambda args: backup_import(args.file))

    # === assert (the canonical assert door) ===
    assert_parser = subparsers.add_parser(
        "assert", help="Assert a canonical field: assert <resource> <id> <path> <value>")
    assert_parser.add_argument("resource", help="e.g. machine-records, tool-set-records")
    assert_parser.add_argument("record_id", help="Record id")
    assert_parser.add_argument("path", help="Canonical path, e.g. name")
    assert_parser.add_argument("value", help="Value (JSON-parsed if possible, else string)")
    assert_parser.set_defaults(
        func=lambda args: assert_canonical(args.resource, args.record_id, args.path, args.value))

    # === ping ===
    ping_parser = subparsers.add_parser(
        "ping",
        help="Check server health",
        description="Check if the server is reachable and healthy."
    )
    ping_parser.set_defaults(func=lambda _: ping())

    # === logout ===
    logout_parser = subparsers.add_parser(
        "logout",
        help="End current session",
        description="End the current session (clears session cookie)."
    )
    logout_parser.set_defaults(func=lambda _: logout())

    # Optional shell tab-completion. argcomplete is NOT a dependency — this file
    # stays stdlib-only and fully runnable without it; when argcomplete is
    # present and registered (see README/CLI.md), it wires up completion for
    # every subcommand and flag derived from this parser. No-op when absent.
    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    # Parse args
    args = parser.parse_args()
    
    # Set global state
    global BASE_URL, API_KEY
    
    # Handle --login shortcut
    if args.login:
        _run(login)
        return

    # Handle --logout shortcut
    if args.logout:
        # Load session to get BASE_URL for logout request
        API_KEY = os.environ.get("LOOBRIC_API_KEY")
        if not API_KEY:
            session_data = load_session()
        _run(logout)
        return
    
    # Set BASE_URL from args or environment first (before loading session)
    if args.base_url:
        BASE_URL = args.base_url.rstrip("/")
    
    # Load session first (for session-based auth)
    session_data = load_session()
    
    # Set API key only if explicitly provided via --api-key flag
    # Environment variable is NOT used automatically to avoid conflicts with session auth
    if args.api_key:
        API_KEY = args.api_key
    
    # Validate BASE_URL is set
    if not BASE_URL:
        print("Error: Base URL required. Use --base-url, set LOOBRIC_BASE_URL, or run 'loobric --login' first", file=sys.stderr)
        sys.exit(1)
    
    if args.verbose:
        print(f"Base URL: {BASE_URL}", file=sys.stderr)
        if API_KEY:
            print(f"Using API key from --api-key flag", file=sys.stderr)
        elif SESSION_COOKIE:
            print(f"Using saved session from {SESSION_FILE}", file=sys.stderr)
        else:
            print(f"No authentication (login required for protected endpoints)", file=sys.stderr)

    # Run command if one was provided
    if hasattr(args, 'func'):
        _run(args.func, args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()