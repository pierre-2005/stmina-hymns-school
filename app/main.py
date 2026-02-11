import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .content_loader import load_site, find_level, find_year, find_hymn

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def get_site() -> dict:
    content_path = os.environ.get("CONTENT_PATH", "/app/content/site.json")
    return load_site(content_path)


@app.get("/health")
async def health():
    return {"ok": True}


@app.exception_handler(404)
async def not_found(request: Request, exc):
    site = get_site()
    return templates.TemplateResponse(
        "404.html",
        {"request": request, "site": site, "title": "Not found"},
        status_code=404,
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    site = get_site()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "site": site, "title": site.get("site_title", "Hymns")},
    )


@app.get("/levels/{level_slug}", response_class=HTMLResponse)
async def level_page(request: Request, level_slug: str):
    site = get_site()
    level = find_level(site, level_slug)
    if not level:
        return await not_found(request, None)
    return templates.TemplateResponse(
        "level.html",
        {"request": request, "site": site, "level": level, "title": level.get("name", "Level")},
    )


@app.get("/levels/{level_slug}/{year_slug}", response_class=HTMLResponse)
async def year_page(request: Request, level_slug: str, year_slug: str):
    site = get_site()
    level = find_level(site, level_slug)
    if not level:
        return await not_found(request, None)
    year = find_year(level, year_slug)
    if not year:
        return await not_found(request, None)

    return templates.TemplateResponse(
        "year.html",
        {
            "request": request,
            "site": site,
            "level": level,
            "year": year,
            "title": year.get("name", "Year"),
        },
    )


@app.get("/levels/{level_slug}/{year_slug}/{hymn_slug}", response_class=HTMLResponse)
async def hymn_page(request: Request, level_slug: str, year_slug: str, hymn_slug: str):
    site = get_site()
    level = find_level(site, level_slug)
    if not level:
        return await not_found(request, None)
    year = find_year(level, year_slug)
    if not year:
        return await not_found(request, None)
    hymn = find_hymn(year, hymn_slug)
    if not hymn:
        return await not_found(request, None)

    languages = site.get("languages", [])
    segments = hymn.get("segments", [])
    recordings = hymn.get("recordings", [])

    return templates.TemplateResponse(
        "hymn.html",
        {
            "request": request,
            "site": site,
            "level": level,
            "year": year,
            "hymn": hymn,
            "languages": languages,
            "segments": segments,
            "recordings": recordings,
            "title": hymn.get("title", "Hymn"),
        },
    )
