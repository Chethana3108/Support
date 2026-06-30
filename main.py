"""
Biztechnosys AI Support Bot Entrypoint
======================================
This file serves as a backward-compatible wrapper that loads the refactored,
production-grade modular FastAPI chatbot from the `app` package.

Run:  uvicorn main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""

import uvicorn
from app.main import app

if __name__ == "__main__":
    uvicorn.run("main:app", host="145.223.22.6", port=8000, reload=True, access_log=False)
