"""POST /api/restart: answer first, then reload the code on disk.

The proxy is a plain stdlib server usually started by hand, so there is no
supervisor to lean on: the process re-executes itself in place, which keeps the
port and the PID and needs no unit file.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from op1_studio import server
from op1_studio.config import StudioConfig

SRC = Path(__file__).resolve().parents[1] / "src"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def json_request(port: int, path: str, method: str = "GET"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    return res.status, json.loads(raw or b"{}")


@pytest.fixture()
def live(data_dir):
    """A real Handler on a real socket, in a thread, with a temp data dir."""
    cfg = StudioConfig(data_dir=data_dir, mount=None, port=0)
    cfg.ensure_dirs()
    previous = getattr(server.Handler, "app", None)
    server.Handler.app = server.StudioApp(cfg)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield cfg, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        if previous is None:
            del server.Handler.app
        else:
            server.Handler.app = previous


# -- the wire ---------------------------------------------------------------

def test_health_reports_the_version_pid_and_uptime(live):
    _cfg, port = live
    status, body = json_request(port, "/api/health")
    assert status == 200
    assert body["ok"] is True
    assert body["version"] == server.VERSION
    assert body["pid"] == os.getpid()
    assert body["uptime_s"] >= 0


def test_restart_refuses_an_ephemeral_port(live):
    """Port 0 cannot be re-bound by the new image, so say so instead of dying."""
    cfg, port = live
    assert cfg.port == 0
    status, body = json_request(port, "/api/restart", "POST")
    assert status == 409
    assert body["ok"] is False
    assert "manually" in body["error"]


def test_restart_answers_ok_then_schedules_the_reload(live, monkeypatch):
    cfg, port = live
    cfg.port = free_port()
    scheduled = []
    monkeypatch.setattr(server, "schedule_restart", lambda config: scheduled.append(config))

    status, body = json_request(port, "/api/restart", "POST")

    assert status == 200
    assert body["ok"] is True
    assert body["restarting"] is True
    assert body["version"] == server.VERSION
    assert scheduled == [cfg]


# -- the reload mechanics ---------------------------------------------------

def test_restart_argv_runs_the_package_so_relative_imports_survive(data_dir):
    argv = server.restart_argv()
    assert argv[0] == sys.executable
    assert argv[1:] == ["-m", "op1_studio.server"]


def test_restart_env_pins_pythonpath_and_the_live_config(data_dir, tmp_path):
    mount = tmp_path / "54FF-1FEE"
    cfg = StudioConfig(data_dir=data_dir, mount=mount, host="127.0.0.1", port=8123)

    env = server.restart_env(cfg)

    assert str(SRC) in env["PYTHONPATH"].split(os.pathsep)
    assert env["OP1_STUDIO_PORT"] == "8123"
    assert env["OP1_STUDIO_HOST"] == "127.0.0.1"
    assert env["OP1_STUDIO_DATA"] == str(data_dir)
    assert env["OP1_MOUNT"] == str(mount)


def test_restart_env_drops_a_stale_mount_when_none_is_configured(data_dir, monkeypatch):
    monkeypatch.setenv("OP1_MOUNT", "/media/stale/OP-1")
    cfg = StudioConfig(data_dir=data_dir, mount=None, port=8123)

    env = server.restart_env(cfg)

    assert "OP1_MOUNT" not in env


def test_schedule_restart_calls_the_reload_after_the_delay():
    calls = []
    server.schedule_restart(StudioConfig(port=8123), exec_fn=lambda: calls.append("exec"), delay=0.01)
    time.sleep(0.25)
    assert calls == ["exec"]


# -- end to end: a real process re-executes itself --------------------------

def wait_health(port: int, until=None, deadline: float = 20.0) -> dict:
    """Poll /api/health until it answers (and satisfies ``until``, if given)."""
    end = time.monotonic() + deadline
    last = None
    while time.monotonic() < end:
        try:
            status, body = json_request(port, "/api/health")
            if status == 200:
                last = body
                if until is None or until(body):
                    return body
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"the proxy never answered /api/health (last: {last})")


def test_the_proxy_reloads_in_place_on_the_same_port(tmp_path):
    """POST /api/restart brings back fresh code, same socket, same PID."""
    port = free_port()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["OP1_STUDIO_DATA"] = str(tmp_path / "data")
    env["OP1_STUDIO_PORT"] = str(port)
    env.pop("OP1_MOUNT", None)
    env.pop("INVOCATION_ID", None)

    proc = subprocess.Popen(
        [sys.executable, "-m", "op1_studio.server"],
        cwd=str(tmp_path),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        first = wait_health(port)
        time.sleep(1.2)          # uptime must be old enough to tell the two apart
        before = wait_health(port)
        assert before["uptime_s"] >= 1.0

        status, body = json_request(port, "/api/restart", "POST")
        assert status == 200, body
        assert body["restarting"] is True

        after = wait_health(port, until=lambda b: b["uptime_s"] < before["uptime_s"])
        assert after["pid"] == first["pid"], "execv keeps the PID"
        assert proc.poll() is None

        status, _ = json_request(port, "/api/status")
        assert status == 200
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
