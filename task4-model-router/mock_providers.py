import asyncio
import os
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

primary_app = FastAPI()
backup_app = FastAPI()


class CompletionRequest(BaseModel):
    prompt: str = Field(min_length=1)


async def completion(prompt: str, model: str, delay: float = 0) -> Dict[str, Any]:
    if delay:
        await asyncio.sleep(delay)
    return {"model": model, "completion": f"{model} response to: {prompt}"}


@primary_app.post("/complete")
async def primary_complete(body: CompletionRequest):
    if os.getenv("PRIMARY_MODE") == "429":
        return JSONResponse({"error": "busy"}, status_code=429)
    if os.getenv("PRIMARY_MODE") == "slow":
        return await completion(body.prompt, "primary", 3.5)
    return await completion(body.prompt, "primary")


@backup_app.post("/complete")
async def backup_complete(body: CompletionRequest):
    return await completion(body.prompt, "backup")
