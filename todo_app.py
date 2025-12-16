# uvicorn todo_app:app --reload
# ws://127.0.0.1:8000/ws/tasks
# http://127.0.0.1:8000/docs


import asyncio
from typing import Optional, List

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    Depends, HTTPException, Response
)
from pydantic import BaseModel
import httpx

from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String, Boolean, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


# БАЗА ДАННЫХ

Base = declarative_base()

DB_URL = "sqlite+aiosqlite:///./tasks.db"
engine = create_async_engine(DB_URL, future=True)
DBSession = sessionmaker(bind=engine, class_=AsyncSession, autoflush=False, autocommit=False)


class TaskModel(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    completed = Column(Boolean, default=False)


async def get_db():
    async with DBSession() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# Pydantic

class TaskCreate(BaseModel):
    title: str


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    completed: Optional[bool] = None


# WebSocket Manager

class WSManager:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    async def remove(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)
        try:
            await ws.close()
        except:
            pass

    async def send_all(self, text: str):
        """Отправить текст всем подписчикам."""
        for ws in list(self.connections):
            try:
                await ws.send_text(text)
            except:
                await self.remove(ws)


manager = WSManager()


# FastAPI App

app = FastAPI(
    title="TODO API",
    version="1.0",
    description="TODO list + WebSocket + Background worker"
)

BACKGROUND_TRIGGER = asyncio.Event()
EXTERNAL_API = "https://jsonplaceholder.typicode.com/todos"


@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(periodic_loader())


# REST API

@app.get("/tasks")
async def list_tasks(db: AsyncSession = Depends(get_db)):
    q = await db.execute(select(TaskModel))
    tasks = q.scalars().all()
    return {"message": tasks}


@app.get("/tasks/{task_id}")
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)):
    task = await db.get(TaskModel, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return {"message": task}


@app.post("/tasks", status_code=201)
async def create_task(data: TaskCreate, db: AsyncSession = Depends(get_db)):
    task = TaskModel(title=data.title)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {"message": task}


@app.patch("/tasks/{task_id}")
async def patch_task(task_id: int, data: TaskUpdate, db: AsyncSession = Depends(get_db)):
    task = await db.get(TaskModel, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    if data.title is not None:
        task.title = data.title
    if data.completed is not None:
        task.completed = data.completed

    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {"message": task}


@app.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    task = await db.get(TaskModel, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    await db.delete(task)
    await db.commit()
    return Response(status_code=204)


@app.post("/task-generator/run")
async def run_loader():
    BACKGROUND_TRIGGER.set()
    return {"message": "Фоновая задача, запущенная вручную"}


# WebSocket

@app.websocket("/ws/tasks")
async def ws_tasks(ws: WebSocket):
    await manager.connect(ws)

    async def ticker():
        while True:
            await asyncio.sleep(10)
            try:
                await ws.send_text("tick")
            except:
                break

    tick_task = asyncio.create_task(ticker())

    try:
        while True:
            msg = await ws.receive_text()

            if msg == "close":
                await manager.remove(ws)
                break

            if msg == "ping":
                await ws.send_text("ping!!!!")
            else:
                await ws.send_text(f"echo: {msg}")

    except WebSocketDisconnect:
        await manager.remove(ws)
    finally:
        tick_task.cancel()


# Background Worker

async def periodic_loader():
    """
    Работает всегда.
    Каждые 10 секунд забирает задачу с JSONPlaceholder.
    Можно принудительно запустить через POST /task-generator/run.
    """
    current_id = 1
    max_id = 200

    async with httpx.AsyncClient() as client:
        while True:

            # ждем либо 10 секунд, либо ручной запуск
            try:
                await asyncio.wait_for(BACKGROUND_TRIGGER.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

            BACKGROUND_TRIGGER.clear()

            # загрузка задачи
            try:
                resp = await client.get(f"{EXTERNAL_API}/{current_id}")
                data = resp.json()
            except Exception as e:
                print("Ошибка загрузки:", e)
                continue

            title = data.get("title")
            completed = bool(data.get("completed"))

            async with DBSession() as db:

                exists = await db.execute(
                    select(TaskModel).where(TaskModel.title == title)
                )
                if exists.scalar_one_or_none() is None:
                    new_task = TaskModel(title=title, completed=completed)
                    db.add(new_task)
                    await db.commit()
                    await db.refresh(new_task)

                    print("Загружена внешняя задача:", new_task.title)

            current_id += 1
            if current_id > max_id:
                current_id = 1
