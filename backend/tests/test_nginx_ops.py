"""Control-plane side of the Nginx op allow-list. The agent-side behaviour (nginx -t, backup, rollback) is in agent/tests/test_nginx_ops.py."""
import pytest
from fastapi import HTTPException

from app.ops import OP_META, OPERATIONS
from app.services.jobs import enqueue_job
from tests.hosting_helpers import make_server

GOOD_ROOT = "/var/www/kvl/acme/blog/public"


def test_ops_registered_with_destructive_flag():
    for n in ("nginx.stage_site", "nginx.activate_site", "nginx.reload", "nginx.rollback_site", "nginx.remove_site"):
        assert n in OPERATIONS
    assert OP_META["nginx.remove_site"]["destructive"] is True and OP_META["nginx.reload"]["destructive"] is False


@pytest.mark.parametrize("params", [
    {"site": "w-abc12345", "server_names": ["x.example.com;"], "document_root": GOOD_ROOT},
    {"site": "w-abc12345", "server_names": ["*.example.com"], "document_root": GOOD_ROOT},
    {"site": "w-abc12345", "server_names": ["example.com:80"], "document_root": GOOD_ROOT},
    {"site": "w-abc12345", "server_names": [], "document_root": GOOD_ROOT},
    {"site": "w-abc12345", "server_names": ["a.example.com"], "document_root": "/var/www/kvl/acme/../../etc/public"},
    {"site": "w-abc12345", "server_names": ["a.example.com"], "document_root": "/home/x/public"},
    {"site": "W ABC", "server_names": ["a.example.com"], "document_root": GOOD_ROOT},
    {"site": "w-abc12345", "server_names": ["a.example.com"], "document_root": GOOD_ROOT, "extra_config": "x"},
    {"site": "w-abc12345", "server_names": ["a.example.com"], "document_root": GOOD_ROOT, "php_version": "8.3; rm"},
    {"site": "w-abc12345", "server_names": ["a.example.com"], "document_root": GOOD_ROOT, "proxy_port": 22},
])
def test_stage_rejects_hostile_params(db, params):
    srv = make_server(db)
    with pytest.raises(HTTPException) as e:
        enqueue_job(db, srv.id, "nginx.stage_site", params)
    assert e.value.status_code == 422


def test_rollback_and_activate_params(db):
    srv = make_server(db)
    assert enqueue_job(db, srv.id, "nginx.rollback_site", {"site": "w-abc12345", "backup": "20260101T000000Z.conf"})
    for bad in ("../../etc/passwd", "latest", "20260101T000000Z.conf/../x"):
        with pytest.raises(HTTPException):
            enqueue_job(db, srv.id, "nginx.rollback_site", {"site": "w-abc12345", "backup": bad})
    with pytest.raises(HTTPException):
        enqueue_job(db, srv.id, "nginx.activate_site", {"site": "w-abc12345", "expected_hash": "xyz"})
    with pytest.raises(HTTPException):
        enqueue_job(db, srv.id, "nginx.reload", {"site": "x"})
