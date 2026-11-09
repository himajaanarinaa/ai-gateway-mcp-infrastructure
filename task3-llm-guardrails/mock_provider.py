import asyncio
import json
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI()


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)


async def generate_chunks(prompt: str) -> AsyncIterator[bytes]:
    text = f"Mock response: {prompt}"
    for start in range(0, len(text), 8):
        event = json.dumps({"text": text[start : start + 8]}, separators=(",", ":"))
        yield f"data: {event}\n\n".encode("utf-8")
        await asyncio.sleep(0.01)


@app.post("/generate")
async def generate(body: GenerateRequest):
    return StreamingResponse(generate_chunks(body.prompt), media_type="text/event-stream")
