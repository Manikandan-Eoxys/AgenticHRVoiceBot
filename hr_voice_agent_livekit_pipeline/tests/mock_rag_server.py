"""
# Run by hand:
# uvicorn tests.mock_rag_server:app --port 9100

Mock HTTP RAG server for testing HttpRetriever contract compliance.
"""

import asyncio
import json
from fastapi import FastAPI, Request, Response

app = FastAPI(title="Mock RAG Server")

OK_DATA = {
    "results": [
        {
            "content": "Employees get 12 casual leaves per year.",
            "source": "leave-policy.pdf",
            "score": 0.82,
        },
        {
            "content": "Sick leave requires a doctor's certificate for absences over 2 days.",
            "source": "leave-policy.pdf",
            "score": 0.79,
        },
        {
            "content": "Maternity leave entitlement is 26 weeks for eligible employees.",
            "source": "maternity-policy.pdf",
            "score": 0.75,
        },
    ]
}


@app.api_route("/ok", methods=["GET", "POST"])
async def ok():
    return OK_DATA


@app.api_route("/alt", methods=["GET", "POST"])
async def alt():
    return {
        "data": {
            "matches": [
                {"text": "Remote work is permitted up to two days per week."},
                {"text": "Core working hours are 10:00 to 16:00 local time."},
            ]
        }
    }


@app.api_route("/single", methods=["GET", "POST"])
async def single():
    return {"answer": "Standard probation period is three months."}


@app.api_route("/empty", methods=["GET", "POST"])
async def empty():
    return {"results": []}


@app.api_route("/slow", methods=["GET", "POST"])
async def slow(delay: float = 3.0):
    await asyncio.sleep(delay)
    return OK_DATA


@app.api_route("/error", methods=["GET", "POST"])
async def error():
    return Response(
        content=json.dumps({"error": "Internal Server Error"}),
        status_code=500,
        media_type="application/json",
    )


@app.api_route("/badjson", methods=["GET", "POST"])
async def badjson():
    return Response(
        content="<html><body>502 Bad Gateway: not JSON</body></html>",
        status_code=200,
        media_type="text/html",
    )


@app.api_route("/auth", methods=["GET", "POST"])
async def auth(request: Request):
    auth_header = request.headers.get("Authorization")
    if auth_header != "Bearer test-token":
        return Response(
            content=json.dumps({"detail": "Unauthorized"}),
            status_code=401,
            media_type="application/json",
        )
    return OK_DATA
