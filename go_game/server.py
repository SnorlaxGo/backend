from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import os

# Import your routers
from .api_router import router as api_router
from .websocket_router import router as ws_router
from .websocket_manager import redis_manager

app = FastAPI(title="Go Game Server")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include both routers
app.include_router(api_router)
app.include_router(ws_router)

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    # Initialize Redis
    await redis_manager.connect()

@app.on_event("shutdown")
async def shutdown_event():
    # Clean up resources
    if hasattr(redis_manager, 'listener_task') and redis_manager.listener_task:
        redis_manager.listener_task.cancel()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("go_game.server:app", host="0.0.0.0", port=port) 