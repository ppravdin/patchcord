# OAuth Web Clients

Canonical guide: <https://patchcord.dev/docs/oauth>

Source of truth for the published page:

- `patchcord-site repo

Use this repo note only for implementation pointers:

- OAuth provider and registration flow: `patchcord/server/oauth.py`
- Known-client detection and env parsing: `patchcord/server/config.py`
- Discovery and OAuth-related routes: `patchcord/server/app.py`
- OAuth storage helpers and cleanup: `patchcord/server/helpers.py`

When OAuth behavior changes, update the website doc first. Keep this file as an index, not a duplicate copy of the public guide.
