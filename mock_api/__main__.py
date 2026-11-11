"""Run the mock API with: python -m mock_api."""

import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from .app import create_app


def main() -> int:
    # Explicit shell/host variables take precedence over the local .env file.
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    try:
        app = create_app()
        port = int(os.environ.get("PORT", "8000"))
    except ValueError as exc:
        print(f"Invalid mock API configuration: {exc}", file=sys.stderr)
        return 2
    # Hosting platforms set PORT; set MOCK_API_HOST=0.0.0.0 there to accept traffic.
    uvicorn.run(app, host=os.environ.get("MOCK_API_HOST", "127.0.0.1"), port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
