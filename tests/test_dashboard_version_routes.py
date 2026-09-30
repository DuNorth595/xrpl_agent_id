# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Pin the /api/version, /api/liveness, /api/health, /api/monitor HTTP contract.

Boots the dashboard in-process, hits each endpoint, and asserts the response
shape. If you change a field name or remove a route, this test fails —
which is the point.
"""

from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection

import pytest

from xrpl_agent_id.dashboard import server as srv


@pytest.fixture(scope="module")
def running_server(tmp_path_factory):
    """Boot the dashboard on a free port for the duration of this module."""
    db_path = tmp_path_factory.mktemp("dashboard") / "test.db"
    # Initialize the schema.
    conn = srv.db.open_db(db_path)
    conn.close()

    # Bind to an OS-chosen port.
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.DashboardHandler)
    httpd.db_path = db_path  # type: ignore[attr-defined]
    port = httpd.server_address[1]

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    # Give the server a beat to come up.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            c = HTTPConnection("127.0.0.1", port, timeout=0.5)
            c.request("GET", "/api/liveness")
            c.getresponse().read()
            c.close()
            break
        except Exception:
            time.sleep(0.05)
    else:
        httpd.shutdown()
        pytest.fail("Dashboard did not come up within 2s")

    yield f"127.0.0.1:{port}"

    httpd.shutdown()
    httpd.server_close()


def _get_json(host_port: str, path: str) -> tuple[int, dict]:
    status, body = _get_raw(host_port, path)
    return status, json.loads(body)


def _get_raw(host_port: str, path: str) -> tuple[int, bytes]:
    host, port = host_port.split(":")
    conn = HTTPConnection(host, int(port), timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, body
    finally:
        conn.close()


def test_api_version_shape(running_server):
    status, body = _get_json(running_server, "/api/version")
    assert status == 200
    # Required fields — do not remove without bumping the contract.
    for key in (
        "package",
        "package_version",
        "server_version",
        "python_version",
        "xrpl_py_version",
        "now",
        "ok",
    ):
        assert key in body, f"/api/version missing required field {key!r}: got {body}"
    assert body["package"] == "xrpl_agent_id"
    assert body["package_version"] == "0.4.0"
    assert body["ok"] is True
    assert isinstance(body["now"], int)
    assert body["now"] > 0


def test_api_liveness_shape(running_server):
    status, body = _get_json(running_server, "/api/liveness")
    assert status == 200
    assert body["status"] == "ok"
    assert "now" in body


def test_api_health_backward_compat(running_server):
    """Legacy /api/health still resolves — returns both liveness and legacy events."""
    status, body = _get_json(running_server, "/api/health")
    assert status == 200
    assert "liveness" in body
    assert "legacy_events" in body
    assert body["liveness"]["status"] == "ok"


def test_api_monitor_shape(running_server):
    status, body = _get_json(running_server, "/api/monitor")
    assert status == 200
    assert "events" in body
    assert isinstance(body["events"], list)


def test_unknown_route_404(running_server):
    """Sanity: unknown routes should not 200 — guards against silent path typos."""
    status, _ = _get_raw(running_server, "/api/does_not_exist")
    assert status == 404
