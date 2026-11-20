"""POST / on :3011: the numbered terminals' overrides (the dashboard before the library):
their order, a hard reset, /compact, and project / task / model / effort labels.

Part of status_server (the facade re-exports every name here)."""

import subprocess

from backend.terminals import load_agents, reset_agent, save_agents, SESSIONS


def slot_post(body):
    """(HTTP status, JSON reply) for one POST / body."""
    # Order sync
    if "_order" in body:
        agents = load_agents()
        agents["_order"] = body["_order"]
        save_agents(agents)
        return 200, {"ok": True}

    agent_id = str(body.get("id", ""))
    valid_ids = {t["id"]: t["session"] for t in SESSIONS}
    if agent_id not in valid_ids:
        return 400, {"error": "invalid id"}

    # Hard reset: wipe tmux session + conversation JSONL + override entry.
    if body.get("reset"):
        info = reset_agent(agent_id, valid_ids[agent_id])
        return 200, {"ok": True, **info}

    # Fire-and-forget slash commands ("compact" so far). Pure runtime —
    # only sends if the tmux session is alive; nothing is persisted.
    VALID_ACTIONS = {"compact": "/compact"}
    if "action" in body:
        action = body["action"]
        if action not in VALID_ACTIONS:
            return 400, {"error": f"unknown action {action!r}"}
        session_name = valid_ids[agent_id]
        r = subprocess.run(   # exact targets: never another slot (see reset_agent)
            ["tmux", "has-session", "-t", "=" + session_name],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            subprocess.run(
                ["tmux", "send-keys", "-t", f"={session_name}:",
                 VALID_ACTIONS[action], "Enter"],
                capture_output=True, text=True,
            )
        return 200, {"ok": True, "sent": r.returncode == 0}

    # Effort must be one of these — validated BEFORE we touch the file
    # so a bad value never overwrites anything on disk.
    VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max", "auto"}
    if "effort" in body and body["effort"] not in ({""} | VALID_EFFORTS):
        return 400, {"error": f"invalid effort {body['effort']!r}"}

    agents = load_agents()
    if agent_id not in agents:
        agents[agent_id] = {}
    if "project" in body:
        agents[agent_id]["project"] = body["project"]
        agents[agent_id]["locked"] = True
    if "task" in body:
        agents[agent_id]["task"] = body["task"]
    if "unlock" in body and body["unlock"]:
        agents[agent_id]["locked"] = False
    # Per-agent model override. Empty string clears the override so the
    # next launch falls back to the Claude subscription default.
    slash_cmds: list[str] = []
    if "model" in body:
        val = body["model"]
        if isinstance(val, str) and val:
            agents[agent_id]["model"] = val
            slash_cmds.append(f"/model {val}")
        else:
            agents[agent_id].pop("model", None)
    # Per-agent effort override. Empty string clears → next launch uses "auto".
    if "effort" in body:
        val = body["effort"]
        if isinstance(val, str) and val:
            agents[agent_id]["effort"] = val
            slash_cmds.append(f"/effort {val}")
        else:
            agents[agent_id].pop("effort", None)
    save_agents(agents)

    # Live-apply: if the agent's tmux session is alive, inject the slash
    # command(s) so the change takes effect immediately. Without this,
    # POSTing only persists to disk and the user wouldn't see anything
    # change in the terminal until the next launch.
    if slash_cmds:
        session_name = valid_ids[agent_id]
        r = subprocess.run(   # exact targets: never another slot (see reset_agent)
            ["tmux", "has-session", "-t", "=" + session_name],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            for cmd in slash_cmds:
                subprocess.run(
                    ["tmux", "send-keys", "-t", f"={session_name}:", cmd, "Enter"],
                    capture_output=True, text=True,
                )

    return 200, {"ok": True}
