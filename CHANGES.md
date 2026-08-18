# Changes in v3

## New Content Manager

- Added `manager/stmina_content_manager.py`.
- Cross-platform Tkinter GUI for Windows, macOS, and Linux.
- Added standalone build scripts and a GitHub Actions multi-OS build workflow.
- Administrator username/password is required before remote content can be loaded or published.
- Passwords are never saved by the manager.
- Short-lived signed publishing sessions are stored in memory only.

## Content storage

- Normal hymn content is no longer edited through Excel after the v3 migration.
- Existing `content/site.xlsx` is automatically migrated once to `/app/data/site-content.json`.
- Live content now resides in the persistent `stminahs_data` volume.
- Content publishes use atomic file replacement.
- Previous content is automatically backed up before each publish.
- The website reloads content when the JSON file changes, without a container restart.

## Secure publishing API

Added authenticated endpoints under `/api/content`:

- `POST /api/content/login`
- `GET /api/content/current`
- `POST /api/content/validate`
- `POST /api/content/publish`
- `GET /api/content/status`
- `POST /api/content/redeploy`

Only active Administrator accounts may use these endpoints.

## Optional GitHub backup

- The server can back up `site-content.json` to GitHub after a successful publish.
- GitHub credentials remain server-side in Portainer environment variables.
- The Content Manager never receives the GitHub token.

## Optional Portainer redeploy

- Added an authenticated Content Manager button that can trigger a configured Portainer stack webhook.
- The webhook URL remains server-side.
- Content publishes do not automatically redeploy the stack.

## Public website

- Removed the front-page portal button from the hero.
- Updated empty-state text so it refers to the Content Manager instead of Excel sheets.
- Existing SoundCloud, multilingual lyrics, login/roles, attendance, submissions, classes, and developer comments remain.

## Additional fix

- Restored the missing administrator Activate/Deactivate route used by the existing Users template.
- Prevents deactivating the current administrator or the last active administrator.
