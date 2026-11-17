# Guest sandbox image = the AgentKennel image of the same version, nothing added: its
# entrypoint already runs the services keeper (services_keeper.py, /work/.services) next
# to the dashboard. A separate name keeps the guests' containers apart from your own
# AgentKennel container and lets the guests module add to the image later.
# guests/install.sh builds both:
#   docker build -t agentkennel:<version> .
#   docker build -t agentkennel-guest:<version> --build-arg BASE=agentkennel:<version> guests/image
ARG BASE=agentkennel:latest
FROM ${BASE}
# Claude Code reads a managed-settings.json created mid-session only if its folder existed
# when the session started (measured 17.11.2026): a guest's rules lock (rules_gate.py) must
# reach terminals that are already open
RUN mkdir -p /etc/claude-code
