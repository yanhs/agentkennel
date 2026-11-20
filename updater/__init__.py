"""Updates of an AgentKennel install: is there a newer release, and installing it.

    versions.py   release numbers and the CHANGELOG.md excerpt between two of them
    release.py    the latest release (GitHub's API, or `git ls-remote` for another remote)
    state.py      the files under <install>/.sessions/update/
    install.py    what kind of install this is; the git steps (preflight, move, roll back)
    backup.py     a copy of the code and the settings an update may change
    services.py   which services are the panel's, restarting them, the health check
    notify.py     the result in Telegram
    apply.py      the update itself, run as its own process: python3 -m updater.apply

The dashboard's side (the API, the daily check, the night window) is backend/updates.py."""
