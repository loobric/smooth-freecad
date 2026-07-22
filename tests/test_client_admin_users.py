# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the admin account-roster verb on the loobric reference client.

list_users() is a thin door onto GET /api/v1/admin/users — the read-only
"how many accounts exist, and who are they?" roster. We inject a fake transport
and assert the method/endpoint it emits, and that an older server (NotFound)
surfaces cleanly rather than as a stack trace.
"""
import pytest

from freecad.Loobric import loobric


@pytest.mark.unit
def test_list_users_hits_admin_users_endpoint():
    captured = []

    def transport(method, endpoint, **kw):
        captured.append((method, endpoint))
        return {"total": 2, "users": [{"email": "a@x"}, {"email": "b@x"}]}

    client = loobric.Client("https://loobric.example", api_key="k", transport=transport)
    result = client.list_users()

    assert captured == [("GET", "/admin/users")]
    assert result["total"] == 2
    assert len(result["users"]) == 2


@pytest.mark.unit
def test_list_users_propagates_notfound_on_older_server():
    def transport(method, endpoint, **kw):
        raise loobric.NotFound(404, "no such endpoint")

    client = loobric.Client("https://loobric.example", api_key="k", transport=transport)
    with pytest.raises(loobric.NotFound):
        client.list_users()
