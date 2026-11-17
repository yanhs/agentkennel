"""nginx servers keep every byte: the text each guests' address gets on nginx, pinned to
golden_nginx.json — captured from master 8f788b1's own render_vhost / render_gone /
render_moved / guestctl.dashboard_vhost / owner_entry_location / files_vhost, before the
Caddy backend existed. Checked here through the paths that now write it: the renderer
NginxConf, guestctl with no conf, and the flows with nginx-shaped ops."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
GUESTS = HERE.parents[1]
for p in (GUESTS, GUESTS / "sitegate", GUESTS / "turnstile"):
    sys.path.insert(0, str(p))
import guestctl as gc  # noqa: E402
import sitegate as sg  # noqa: E402

GOLDEN = json.loads((HERE / "golden_nginx.json").read_text(encoding="utf-8"))
H = "blog-anna.example.test"


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(gc, "_registry", lambda: {"anna": {"port": 18101}})


def test_sites_through_the_renderer():
    assert sg.NginxConf.site(H, "172.31.250.11", 3000, tls=True) == GOLDEN["site_tls"]
    assert sg.NginxConf.site(H, "172.31.250.11", 3000, tls=False) == GOLDEN["site_pending"]
    assert sg.NginxConf.gone(H) == GOLDEN["gone"]
    assert sg.NginxConf.moved("sb-anna.old.test", "sb-anna.example.test") == GOLDEN["moved"]


def test_the_old_functions_themselves():
    assert sg.render_vhost(H, "172.31.250.11", 3000, tls=True) == GOLDEN["site_tls"]
    assert sg.render_gone(H) == GOLDEN["gone"]


def test_dashboards_and_files():
    assert gc.owner_entry_location() == GOLDEN["owner_entry"]
    assert gc.dashboard_vhost("anna", 18101, container_origin="https://sb-anna.example.test") \
        == GOLDEN["dashboard"]
    assert gc.dashboard_vhost("anna", 18101, container_origin="https://sb-anna.old.test") \
        == GOLDEN["dashboard_moved_origin"]
    assert gc.dashboard_vhost("anna", 18101, container_origin="https://sb-anna.example.test",
                              conf=sg.conf_for(object())) == GOLDEN["dashboard"]
    assert gc.files_vhost().replace(gc.PUBLIC_ROOT, "@PUBLIC_ROOT@") == GOLDEN["files"]


class NginxShaped:
    """Ops as the nginx tests have always faked them: no KIND, no SUFFIX."""

    def __init__(self):
        self.written = []

    def __getattr__(self, name):
        if name == "write_vhost":
            return lambda host, text: self.written.append((host, text))
        return lambda *a: None


def test_the_flows_write_the_golden_text(tmp_path, monkeypatch):
    import turnstile as ts
    book = ts.GuestBook(tmp_path / "guests.json")
    book.add("anna", 10, telegram_id=1)
    ops = NginxShaped()
    sg.Gate(book, ops, tmp_path / "sites.json", ip_of={"anna": "172.31.250.11"}.get,
            cleanup_delay=0).publish("anna", "blog", 3000)
    assert [t for _, t in ops.written] == [GOLDEN["site_pending"], GOLDEN["site_tls"]]
    monkeypatch.setattr(gc, "_container_env", lambda n: {"AGENTKENNEL_ORIGIN": "https://sb-anna.example.test"})
    ops.written.clear()
    gc.take_down_web("anna", ops, delay=0)
    assert ops.written[0][1] == sg.render_gone("sb-anna.example.test")
