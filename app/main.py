import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db, mqtt_client, scheduler
from app.routes import commands, logs, schedule

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="LED strip controller")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(commands.router)
app.include_router(logs.router)
app.include_router(schedule.router)


@app.on_event("startup")
async def startup():
    db.init_db()
    loop = asyncio.get_running_loop()
    mqtt_client.start(loop)
    scheduler.start()


@app.on_event("shutdown")
async def shutdown():
    mqtt_client.stop()
    scheduler.stop()


@app.get("/api/status")
def status():
    """Polled by the frontend to show a connectivity indicator."""
    return {"mqtt_connected": mqtt_client.is_connected()}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
