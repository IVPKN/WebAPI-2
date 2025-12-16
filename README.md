# TODO API Project

FastAPI приложение для управления задачами с WebSocket и фоновыми задачами.

## Установка

```bash
pip install -r requirements.txt
python -m uvicorn todo_app:app --reload --host 0.0.0.0 --port 8000
```

## WebSocket

```bash
ws://127.0.0.1:8000/ws/tasks
```

## Swagger-документация

```bash
http://127.0.0.1:8000/docs
```
