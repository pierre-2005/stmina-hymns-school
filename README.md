# St. Mina Hymns School — v3

FastAPI + Jinja website for St. Mina Coptic Orthodox Church in Calgary, plus the new cross-platform **St. Mina Hymns School Content Manager**.

## What changed in v3

The website no longer requires Excel → GitHub → Portainer redeploys for normal curriculum edits.

On the first v3 startup, the existing `content/site.xlsx` workbook is migrated into:

```text
/app/data/site-content.json
```

That path lives inside the existing persistent `stminahs_data` Docker volume.

After migration, the desktop Content Manager edits and publishes that JSON through an authenticated HTTPS API. The website detects the file change automatically, so new levels, years, hymns, recordings, languages, and lyrics appear without restarting Docker.

The old workbook remains in the repository as a one-time migration/fallback source, but it is no longer the normal editing workflow.

## Content Manager

Source:

```text
manager/stmina_content_manager.py
```

It provides a GUI for:

- levels
- years
- hymns
- published/hidden state
- ordering
- SoundCloud recordings
- timestamped multilingual lyrics
- languages and default visibility
- site title/subtitle/footer
- validation
- instant publishing
- optional GitHub JSON backup
- optional Portainer redeploy trigger for code changes
- local JSON draft save/open

### Authentication

The Content Manager does not contain a master password and does not store administrator passwords.

To publish, the operator must enter a valid **active Administrator account** from the website. The website verifies the existing scrypt password hash and issues a short-lived signed Content Manager token. The token is held only in memory by the manager.

Student and Teacher accounts cannot use the publishing API.

## Required Portainer variables for v3

Keep all of the existing variables, then add:

```text
CONTENT_API_SECRET=<new long random secret>
CONTENT_API_TOKEN_TTL=7200
```

Generate `CONTENT_API_SECRET` separately from `SESSION_SECRET`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Do not reuse your administrator password as `CONTENT_API_SECRET`.

### Optional GitHub backup

If you want every publish to be backed up to GitHub, configure:

```text
GITHUB_TOKEN=<fine-grained GitHub token>
GITHUB_REPO=pierre-2005/stmina-hymns-school
GITHUB_BRANCH=main
GITHUB_CONTENT_PATH=content/site-content.json
```

The token should only have the repository permissions needed to write repository contents. Keep it in Portainer; the desktop manager never receives it.

### Optional Portainer redeploy button

Enable a stack webhook for the Git-backed Hymns School stack and set:

```text
PORTAINER_WEBHOOK_URL=<Portainer stack webhook URL>
```

Keep the webhook URL in Portainer. The desktop manager calls the Hymns School API, and the server triggers the webhook on its behalf.

Use the redeploy button only for code/template/CSS/JavaScript/Docker changes that have already been pushed to GitHub. Normal hymn content changes do not need a redeploy.

## Existing website features retained

- Levels → Years → Hymns public curriculum
- responsive SoundCloud player
- multilingual synchronized lyrics
- Coptic Avva Shenouda font
- student / teacher / administrator accounts
- private student recordings
- teacher review and feedback
- attendance
- classes and enrolment
- developer comments
- administrator recovery
- permanent user deletion safeguards
- responsive mobile design

## Persistent data

The existing volumes remain:

```text
stminahs_data
stminahs_uploads
```

`stminahs_data` now contains:

```text
stminahs.db
site-content.json
content-backups/
```

Do not delete this volume during ordinary redeployments.

## First v3 deployment

1. Back up the `stminahs_data` and `stminahs_uploads` volumes.
2. Push the v3 repository files to GitHub.
3. Add `CONTENT_API_SECRET` in Portainer.
4. Optionally configure GitHub backup and the Portainer webhook variables.
5. Pull and redeploy the Git-backed stack with a rebuild.
6. Open `/health` and confirm `ok: true`.
7. Start the Content Manager.
8. Sign in using a website Administrator username/password.
9. Confirm the existing workbook content appears in the manager.
10. Make a small test edit, validate, and publish it.

The initial v3 container startup automatically creates `/app/data/site-content.json` from the existing workbook if the JSON file does not already exist.

## Content backups

Before every successful publish, the server copies the previous JSON into:

```text
/app/data/content-backups/
```

The newest 50 automatic backups are retained.

GitHub backup is additional and optional.

## Running the manager from source

```bash
cd manager
python stmina_content_manager.py
```

The source app uses only Python's standard library and Tkinter.

## Standalone Windows/macOS/Linux builds

See `manager/README.md`.

The included GitHub Actions workflow builds separate artifacts for Windows, macOS, and Linux:

```text
.github/workflows/build-content-manager.yml
```

A standalone executable does not require Python on the end user's computer. Builds are OS-specific, so Windows, macOS, and Linux each receive their own build.
