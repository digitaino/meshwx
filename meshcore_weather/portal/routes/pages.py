"""The single HTML page of the admin portal. Everything else is /api."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def app_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "app.html", {})
