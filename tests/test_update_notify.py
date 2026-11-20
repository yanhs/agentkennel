"""The update's result goes to Telegram when the install has a bot: success and failure
alike, with what changed or why it failed (silence must never mean "probably fine")."""
from updater import notify as N


def test_the_bot_from_the_telegram_page_its_notifications_bot_first(tmp_path):
    conf = tmp_path / "telegram.json"
    conf.write_text('{"token": "1:main", "owner": "42", "notify_token": "2:notes"}')
    assert N.target(str(tmp_path), env={"AGENTKENNEL_TG_CONF": str(conf)}) == ("2:notes", "42")
    conf.write_text('{"token": "1:main", "owner": "42"}')
    assert N.target(str(tmp_path), env={"AGENTKENNEL_TG_CONF": str(conf)}) == ("1:main", "42")


def test_else_the_bridge_service_settings_in_the_installs_env_file(tmp_path):
    (tmp_path / ".env").write_text("# c\nTG_BRIDGE_TOKEN=3:svc\nTG_BRIDGE_OWNER='77'\n")
    env = {"AGENTKENNEL_TG_CONF": str(tmp_path / "none.json")}
    assert N.target(str(tmp_path), env=env) == ("3:svc", "77")


def test_no_bot_no_target(tmp_path):
    assert N.target(str(tmp_path), env={"AGENTKENNEL_TG_CONF": str(tmp_path / "x.json")}) is None


def test_messages_say_what_happened():
    ok = N.message({"state": "done", "from": "2.7.2", "to": "2.8.0", "auto": True,
                    "notes": [{"version": "2.8.0", "title": "updates", "body": "- **One** click.\n- Two."}]})
    assert "v2.7.2" in ok and "v2.8.0" in ok and "updated" in ok.lower()
    assert "One click." in ok and "**" not in ok
    bad = N.message({"state": "rolled-back", "from": "2.7.2", "to": "2.8.0",
                     "error": "the panel is not healthy after 90 s", "step": "health check"})
    assert "failed" in bad.lower() and "v2.7.2" in bad and "health check" in bad
    assert "back" in bad.lower()
    worse = N.message({"state": "rollback-failed", "from": "2.7.2", "to": "2.8.0",
                       "error": "x", "rollback_error": "git: y"})
    assert "by hand" in worse.lower() and "git: y" in worse


def test_send_reports_telegrams_answer():
    sent = []
    def post(url, data):
        sent.append((url, data))
        return {"ok": True}
    assert N.send("1:t", "42", "hi", post=post) == (True, "")
    assert sent == [("https://api.telegram.org/bot1:t/sendMessage", {"chat_id": "42", "text": "hi"})]
    def broken(url, data):
        raise OSError("down")
    ok, why = N.send("1:t", "42", "hi", post=broken)
    assert not ok and "down" in why
