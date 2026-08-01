# St. Mina Hymns School

A FastAPI + Jinja website for St. Mina Coptic Orthodox Church in Calgary.

## Features

- Levels → years → hymns, managed through `content/site.xlsx`
- Responsive SoundCloud players using normal public SoundCloud track links
- English, Coptic, and Coptic-English columns with spreadsheet-controlled defaults
- SoundCloud lyric seeking/highlighting through the SoundCloud Widget API
- Student, teacher, and administrator login roles
- Private student audio submissions stored in Docker volumes
- Teacher review and feedback for student recordings
- Attendance for assigned classes
- Developer comments and admin status tracking
- Admin user and class management
- Responsive burgundy/cream design

## Coptic font

Keep your existing `Avva_Shenouda.ttf` file at:

```text
app/static/fonts/Avva_Shenouda.ttf
```

The downloadable project does not include the font file, so copy your existing licensed font into that path before building.

## Important content workflow

`content/site.xlsx` is copied into the Docker image during the build. Do **not** mount a named volume over `/app/content`, because that hides the newest workbook from the image.

To update hymn content:

1. Edit `content/site.xlsx`.
2. Commit and push it to GitHub.
3. In Portainer, update/redeploy the Git stack and make sure the image is rebuilt.

The SQLite database and student audio files remain persistent because only `/app/data` and `/app/uploads` use named volumes.

## SoundCloud links

In the `recordings` sheet, paste the normal public track URL in the `url` column, for example:

```text
https://soundcloud.com/account-name/track-name
```

Do not manually build an iframe URL. The app converts the public link into a responsive SoundCloud widget URL.

## Portainer environment variables

Set these in the stack environment:

- `SESSION_SECRET`: a long random value
- `ADMIN_USERNAME`: initial administrator username
- `ADMIN_DISPLAY_NAME`: name shown in the top-right corner
- `ADMIN_PASSWORD`: initial administrator password

Generate a session secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The first administrator is only created when the database has no users. After signing in, use **Portal → Users** to create teacher and student accounts.

## Deploy

```bash
docker compose up --build -d
```

Local origin:

```text
http://127.0.0.1:8090
```

Cloudflare Tunnel public hostname:

```text
stminahs.overvault.ca
```

If cloudflared is using host networking, route it to:

```text
http://localhost:8090
```

## Persistent data

- `stminahs_data`: SQLite users, classes, attendance, comments, and upload metadata
- `stminahs_uploads`: private student audio files

Back up both volumes before migrating the site.

## Student recording privacy

Uploaded recordings are not mounted as public static files. The app serves each file through an authenticated route and checks that the requester is:

- the student who uploaded it,
- the teacher assigned to its class, or
- an administrator.

## Workbook sheets

- `Instructions`: plain-language editing guide
- `meta`: site title, subtitle, footer
- `languages`: language name/order/default visibility
- `levels`: homepage level cards
- `years`: years inside each level
- `hymns`: hymns inside each year
- `recordings`: SoundCloud track URLs
- `segments`: timestamped multilingual lyric rows
