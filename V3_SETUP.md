# v3 Portainer Setup Checklist

## 1. Back up first

Back up both Docker volumes before deploying v3:

- `stminahs_data`
- `stminahs_uploads`

## 2. Required new environment variable

Generate a new secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Add it in Portainer as:

```text
CONTENT_API_SECRET=<generated value>
```

Optional session length:

```text
CONTENT_API_TOKEN_TTL=7200
```

7200 seconds = 2 hours.

## 3. GitHub backup (optional)

Create a fine-grained GitHub token restricted to the Hymns School repository with repository Contents write permission.

Set:

```text
GITHUB_TOKEN=<token>
GITHUB_REPO=pierre-2005/stmina-hymns-school
GITHUB_BRANCH=main
GITHUB_CONTENT_PATH=content/site-content.json
```

If `GITHUB_TOKEN` is blank, live publishing still works; only the GitHub backup option is unavailable.

## 4. Portainer redeploy button (optional)

For the existing Git-backed Hymns School stack, enable a stack webhook and copy its URL.

Set:

```text
PORTAINER_WEBHOOK_URL=<copied webhook URL>
```

If this is blank, content publishing still works; only the Redeploy Website button is unavailable.

## 5. Deploy v3

Pull the latest repository and rebuild/redeploy the stack.

Do not remove the existing persistent volumes.

## 6. Automatic migration

At first startup, if this file does not exist:

```text
/app/data/site-content.json
```

v3 reads the current:

```text
/app/content/site.xlsx
```

and creates the persistent JSON automatically.

After that point, `site-content.json` is the live curriculum source.

## 7. Use the manager

Run the platform-specific Content Manager build, or from source:

```bash
python manager/stmina_content_manager.py
```

Enter:

- Website: `https://stminahs.overvault.ca`
- an active Administrator username
- that Administrator's website password

Then use **Publish Content Now** for curriculum changes.

Use **Trigger Portainer redeploy** only after website source code has been pushed to GitHub.
