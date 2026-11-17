"""Site gate, after review (17.11.2026):

* a site name can't take a dashboard's address: `sb`, `sbx` and `sb-…`/`sbx-…` are
  reserved, and a new address the web server already serves (a vhost or Caddy block that is
  not one of the gate's own sites) is refused — on nginx and Caddy alike;
* a guest can't reload the web server in a loop: an unchanged republish changes nothing,
  and real changes are rate-limited (per guest and in all; the running site gate turns the
  limit on — limit=True — the older tests drive the Gate without it);
* the delayed clean-up never takes down a site published again in the meantime;
* one operation uses one backend from start to end (AutoOps may change its mind between
  operations, never in the middle of one), and the clean-up uses the backend that made
  the files."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "turnstile"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import turnstile as ts  # noqa: E402
import sitegate as sg  # noqa: E402
import webchoice  # noqa: E402


class Ops:
    def __init__(self, served=()):
        self.served = set(served)
        self.log = []

    def vhost_exists(self, host):
        return host in self.served

    def dns_create(self, host):
        self.log.append(("dns+", host))

    def dns_delete(self, host):
        self.log.append(("dns-", host))

    def write_vhost(self, host, text):
        self.served.add(host)
        self.log.append(("vhost", host))

    def remove_vhost(self, host):
        self.served.discard(host)
        self.log.append(("vhost-", host))

    def reload(self):
        self.log.append(("reload",))

    def cert_issue(self, host):
        self.log.append(("cert+", host))

    def cert_delete(self, host):
        self.log.append(("cert-", host))

    def notify(self, text):
        self.log.append(("notify",))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def book(tmp_path):
    b = ts.GuestBook(tmp_path / "guests.json")
    for i, name in enumerate(("anna", "kk", "anna-kk"), 1):
        b.add(name, 10, telegram_id=i)
    return b


IPS = {"anna": "172.31.250.11", "kk": "172.31.250.12", "anna-kk": "172.31.250.13"}


def gate(tmp_path, book, ops, **kw):
    return sg.Gate(book, ops, tmp_path / "sites.json", ip_of=IPS.get, cleanup_delay=0,
                   limit=True, **kw)


# ── names that would take a dashboard ───────────────────────────────────────────────
@pytest.mark.parametrize("site", ["sb", "sbx", "sb-anna", "sbx-1", "sb-"])
def test_dashboard_prefixes_are_reserved(site):
    with pytest.raises(ValueError):
        sg.validate_site(site)


@pytest.mark.parametrize("site", ["sbook", "sbxa", "blog", "s-b"])
def test_other_names_that_start_alike_are_fine(site):
    assert sg.validate_site(site) == site


def test_a_guest_cant_overwrite_its_own_dashboard(tmp_path, book):
    ops = Ops(served={"sb-anna.example.test"})
    with pytest.raises((ValueError, sg.GateError)):
        gate(tmp_path, book, ops).publish("anna", "sb", 3000)
    assert ops.log == []


def test_a_guest_cant_take_another_guests_dashboard(tmp_path, book):
    """kk's site `sb-anna` would be sb-anna-kk: anna-kk's dashboard."""
    ops = Ops(served={"sb-anna-kk.example.test"})
    with pytest.raises((ValueError, sg.GateError)):
        gate(tmp_path, book, ops).publish("kk", "sb-anna", 3000)
    assert ops.log == []


def test_an_address_served_but_not_a_site_is_refused(tmp_path, book):
    """Whatever made it (a dashboard, sbx., an owner's own vhost), the gate doesn't take it."""
    ops = Ops(served={"blog-anna.example.test"})
    with pytest.raises(sg.GateError) as e:
        gate(tmp_path, book, ops).publish("anna", "blog", 3000)
    assert str(e.value) == sg._t("site_taken")
    assert ops.log == []


def test_republishing_ones_own_site_still_works(tmp_path, book):
    ops = Ops()
    clock = Clock()
    g = gate(tmp_path, book, ops, clock=clock)
    g.publish("anna", "blog", 3000)
    clock.now += 3600
    assert g.publish("anna", "blog", 3001)["port"] == 3001


# ── no reload loops ───────────────────────────────────────────────────────────────
def test_an_unchanged_republish_touches_nothing(tmp_path, book):
    ops = Ops()
    g = gate(tmp_path, book, ops, clock=Clock())
    g.publish("anna", "blog", 3000)
    ops.log.clear()
    for _ in range(50):
        assert g.publish("anna", "blog", 3000)["port"] == 3000
    assert ops.log == []


def test_changes_are_rate_limited_per_guest(tmp_path, book):
    ops = Ops()
    clock = Clock()
    g = gate(tmp_path, book, ops, clock=clock)
    g.publish("anna", "blog", 3000)
    with pytest.raises(sg.GateError) as e:
        g.publish("anna", "blog", 3001)
    assert str(sg.CHANGE_EVERY + 1) in str(e.value)        # "try again in 61 s"
    with pytest.raises(sg.GateError):
        g.unpublish("anna", "blog")
    g.publish("kk", "blog", 3000)                      # another guest isn't held up
    clock.now += sg.CHANGE_EVERY
    g.publish("anna", "blog", 3001)


def test_changes_are_rate_limited_in_all(tmp_path, book, monkeypatch):
    monkeypatch.setattr(sg, "CHANGES_PER_HOUR", 3)
    ops = Ops()
    clock = Clock()
    g = gate(tmp_path, book, ops, clock=clock)
    for i, guest in enumerate(("anna", "kk", "anna-kk")):
        g.publish(guest, "blog", 3000)
    with pytest.raises(sg.GateError):
        clock.now += sg.CHANGE_EVERY
        g.publish("anna", "shop", 3000)
    clock.now += 3600
    g.publish("anna", "shop", 3000)


def test_a_refused_publish_isnt_counted(tmp_path, book):
    ops = Ops(served={"blog-anna.example.test"})
    clock = Clock()
    g = gate(tmp_path, book, ops, clock=clock)
    with pytest.raises(sg.GateError):
        g.publish("anna", "blog", 3000)
    g.publish("anna", "shop", 3000)                     # not "too often"


# ── delayed clean-up ────────────────────────────────────────────────────────────────
def test_cleanup_spares_a_site_published_again(tmp_path, book):
    timers = []
    ops = Ops()
    clock = Clock()
    g = sg.Gate(book, ops, tmp_path / "sites.json", ip_of=IPS.get, cleanup_delay=90,
                clock=clock, limit=True, timer=lambda delay, fn: timers.append(fn))
    g.publish("anna", "blog", 3000)
    clock.now += sg.CHANGE_EVERY
    g.unpublish("anna", "blog")
    clock.now += sg.CHANGE_EVERY
    g.publish("anna", "blog", 3000)
    ops.log.clear()
    timers[0]()                                          # 90 s later
    assert ("vhost-", "blog-anna.example.test") not in ops.log
    assert "blog-anna.example.test" in ops.served


def test_cleanup_runs_when_nothing_came_back(tmp_path, book):
    timers = []
    ops = Ops()
    clock = Clock()
    g = sg.Gate(book, ops, tmp_path / "sites.json", ip_of=IPS.get, cleanup_delay=90,
                clock=clock, limit=True, timer=lambda delay, fn: timers.append(fn))
    g.publish("anna", "blog", 3000)
    clock.now += sg.CHANGE_EVERY
    g.unpublish("anna", "blog")
    timers[0]()
    assert [e[0] for e in ops.log[-3:]] == ["vhost-", "reload", "cert-"]


# ── one backend per operation ──────────────────────────────────────────────────────
class Switching:
    """AutoOps' shape: current() may give another backend each time it is asked."""

    def __init__(self, *backends):
        self.backends = list(backends)
        self.asked = 0

    def current(self):
        b = self.backends[min(self.asked, len(self.backends) - 1)]
        self.asked += 1
        return b


def test_one_operation_one_backend_and_the_cleanup_uses_it(tmp_path, book):
    first, second = Ops(), Ops()
    timers = []
    clock = Clock()
    auto = Switching(first, second, first)
    g = sg.Gate(book, auto, tmp_path / "sites.json", ip_of=IPS.get, cleanup_delay=90,
                clock=clock, limit=True, timer=lambda delay, fn: timers.append(fn))
    g.publish("anna", "blog", 3000)
    assert first.log and not second.log                  # the whole publish on one
    published = list(first.log)
    clock.now += sg.CHANGE_EVERY
    g.unpublish("anna", "blog")                          # asked again: the second one
    auto.asked = 2                                       # meanwhile it would say "first"
    timers[0]()
    assert [e[0] for e in second.log] == ["vhost", "reload", "dns-", "notify",
                                          "vhost-", "reload", "cert-"]
    assert first.log == published


def test_auto_ops_doesnt_flip_on_a_failed_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(sg.CaddyOps, "VHOSTS", tmp_path / "caddy")
    monkeypatch.setattr(sg.RealOps, "VHOSTS", tmp_path / "nginx")
    answers = [webchoice.Choice("caddy", True, "caddy on 443"),
               webchoice.Choice("nginx", False, "neither nginx nor AgentKennel's Caddy is running", "fix"),
               webchoice.Choice("nginx", True, "nginx is running")]
    now = [0.0]
    auto = sg.AutoOps(probe=lambda: answers.pop(0), clock=lambda: now[0], ttl=30)
    assert auto.current().KIND == "caddy"
    now[0] = 31
    assert auto.current().KIND == "caddy"              # nothing reports active: keep Caddy
    now[0] = 62
    assert auto.current().KIND == "nginx"              # a working nginx: switch
