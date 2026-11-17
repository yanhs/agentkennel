"""The guests' addresses as Caddy site blocks (guests/sitegate/caddyconf.py): each nginx
directive of sitegate.render_vhost / render_gone / render_moved and nginx/sbx.conf.template
has its Caddy counterpart here. Text checks first; then, when a caddy binary is around
(PATH or CADDY_BIN), `caddy adapt` must accept every block."""

import os
import shutil
import subprocess
import warnings
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import caddyconf as cc  # noqa: E402

HOST = "sb-anna.example.test"
ENTRY = ("/_owner-entry", "http://127.0.0.1:3011/api/guests/enter")


def caddy_bin():
    """The caddy the real-Caddy tests use: CADDY_BIN, PATH, or where install.sh puts it."""
    for c in (os.environ.get("CADDY_BIN"), shutil.which("caddy"), "/usr/local/bin/caddy",
              "/usr/bin/caddy"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


NO_CADDY = ("real-Caddy tests SKIPPED: no caddy binary in CADDY_BIN, PATH, /usr/local/bin "
            "or /usr/bin (set CADDY_BIN=/path/to/caddy to run them)")
if not caddy_bin():
    warnings.warn(NO_CADDY)            # shown in pytest's warnings summary, not only with -rs


# ── a guest's dashboard / a published site ────────────────────────────────────────
def test_site_proxies_to_the_sandbox_only():
    text = cc.render_site(HOST, "127.0.0.1", 18101)
    assert text.startswith(f"# guest site {HOST} -> sandbox 127.0.0.1:18101")
    assert f"\n{HOST} {{\n" in text
    assert text.count("reverse_proxy ") == 1
    assert "reverse_proxy 127.0.0.1:18101 {" in text


def test_site_limits_like_nginx():
    text = cc.render_site(HOST, "172.31.250.11", 3000)
    assert "max_size 50MiB" in text                      # client_max_body_size 50m
    assert "header_up X-Real-IP {remote_host}" in text    # proxy_set_header X-Real-IP
    assert "stream_close_delay 10m" in text               # a reload doesn't cut terminals at
                                                          # once; old handlers last 10 min


def test_owner_entry_goes_to_the_panel_at_one_exact_path():
    text = cc.render_site(HOST, "127.0.0.1", 18101, entry=ENTRY)
    block = text[text.index("handle /_owner-entry {"):]
    block = block[:block.index("\n\t}\n")]
    assert "rewrite * /api/guests/enter?{query}" in block
    assert "reverse_proxy 127.0.0.1:3011" in block
    assert text.count("3011") == 1
    assert text.index("handle /_owner-entry {") < text.index("reverse_proxy 127.0.0.1:18101")
    assert cc.has_entry(text, "/_owner-entry")
    assert not cc.has_entry(cc.render_site(HOST, "127.0.0.1", 18101), "/_owner-entry")


def test_published_sites_have_no_entry():
    text = cc.render_site("blog-anna.example.test", "172.31.250.11", 8080)
    assert "_owner-entry" not in text and "3011" not in text


def test_origin_swap_only_without_upgrade():
    text = cc.render_site(HOST, "127.0.0.1", 18101,
                          origin=(f"https://{HOST}", "https://sb-anna.old.test"))
    assert f'header Origin "https://{HOST}"' in text
    assert "not header Upgrade *" in text
    assert 'request_header @sbx_origin Origin "https://sb-anna.old.test"' in text
    assert "Origin" not in cc.render_site(HOST, "127.0.0.1", 18101)


@pytest.mark.parametrize("bad", [("https://x", 'https://y" }\nevil {'), ("https://x\n", "https://y")])
def test_origins_that_could_break_the_file_are_refused(bad):
    with pytest.raises(ValueError):
        cc.render_site(HOST, "127.0.0.1", 18101, origin=bad)


@pytest.mark.parametrize("host,ip,port", [("Bad Host", "127.0.0.1", 1),
                                          ("x.test {", "127.0.0.1", 1),
                                          ("a.test", "127.0.0.1; evil", 1),
                                          ("a.test", "127.0.0.1", "80 {")])
def test_names_are_checked_before_they_reach_the_file(host, ip, port):
    with pytest.raises(ValueError):
        cc.render_site(host, ip, port)


def test_serves_tells_a_live_dashboard_from_a_stub():
    assert cc.serves(cc.render_site(HOST, "127.0.0.1", 18101), 18101)
    assert not cc.serves(cc.render_site(HOST, "127.0.0.1", 18101), 18102)
    assert not cc.serves(cc.render_site(HOST, "127.0.0.1", 18101, tls=False), 18101)
    assert not cc.serves(cc.render_gone(HOST), 18101)


# ── waiting, gone, moved ────────────────────────────────────────────────────────
def test_pending_answers_404_on_its_own_name():
    text = cc.render_site(HOST, "127.0.0.1", 18101, tls=False)
    assert "certificate pending" in text and "respond 404" in text
    assert "reverse_proxy" not in text


def test_gone_is_404_on_http_and_https():
    text = cc.render_gone(HOST)
    assert f"http://{HOST}, https://{HOST} {{" in text and "respond 404" in text
    assert "reverse_proxy" not in text


def test_moved_redirects_every_path():
    text = cc.render_moved("sb-anna.old.test", HOST)
    assert "sb-anna.old.test {" in text
    assert f"redir https://{HOST}{{uri}} permanent" in text


# ── sbx.<domain> ────────────────────────────────────────────────────────────────
def test_files_go_through_the_symlink_safe_server():
    """Caddy's file_server follows symlinks (a guest's link to a host file would be
    served): /f/ goes to publicfiles.py instead; everything else is 404."""
    text = cc.render_files("sbx.example.test", 8795)
    assert "file_server" not in text
    assert "handle /f/* {" in text and "reverse_proxy 127.0.0.1:8795" in text
    for h in ('Content-Security-Policy "sandbox"', 'X-Content-Type-Options "nosniff"',
              'X-Robots-Tag "noindex, nofollow"', 'Cache-Control "no-cache"'):
        assert f"header_down {h}" in text
    assert text.rstrip().endswith("handle {\n\t\trespond 404\n\t}\n}")


# ── Caddy itself accepts all of it ──────────────────────────────────────────────
@pytest.mark.skipif(not caddy_bin(), reason=NO_CADDY)
def test_caddy_adapts_every_block(tmp_path):
    sites = tmp_path / "sites"
    sites.mkdir()
    blocks = {
        HOST: cc.render_site(HOST, "127.0.0.1", 18101, entry=ENTRY,
                             origin=(f"https://{HOST}", "https://sb-anna.old.test")),
        "blog-anna.example.test": cc.render_site("blog-anna.example.test", "172.31.250.11", 3000),
        "pending.example.test": cc.render_site("pending.example.test", "127.0.0.1", 1, tls=False),
        "gone.example.test": cc.render_gone("gone.example.test"),
        "sb-anna.old.test": cc.render_moved("sb-anna.old.test", HOST),
        "sbx.example.test": cc.render_files("sbx.example.test", 8795),
    }
    for host, text in blocks.items():
        (sites / f"{host}.caddy").write_text(text)
    main = tmp_path / "Caddyfile"
    main.write_text('{\n\tadmin off\n}\n:18765 {\n\trespond "main"\n}\n'
                    f'import "{sites}/*.caddy"\n')
    r = subprocess.run([caddy_bin(), "adapt", "--config", str(main), "--adapter", "caddyfile"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    for host in blocks:
        assert f'"{host}"' in r.stdout, host
