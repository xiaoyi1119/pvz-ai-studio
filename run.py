import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import uvicorn

from pvz_agent_plugin.service import app, settings


if __name__ == "__main__":
    uvicorn.run(app, host=settings.plugin_host, port=settings.plugin_port, log_level="info")

