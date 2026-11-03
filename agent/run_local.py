#!/usr/bin/env python
"""Run a LOCAL test agent against the dev API. Never use against a real server.

    # 1) in the panel (Admin > Servers) register a server and copy the enrollment token, then:
    python run_local.py --api-url http://127.0.0.1:8000/api/v1 --token kvl_enr_...
    # 2) later runs reuse the stored credentials:
    python run_local.py --api-url http://127.0.0.1:8000/api/v1
    # --once does a single heartbeat + job poll and exits.

State lives in ./.local-agent (credentials mode 600). The health endpoint binds 127.0.0.1:9710.
"""
import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kvl_agent import __version__  # noqa: E402
from kvl_agent.client import ControlPlaneClient  # noqa: E402
from kvl_agent.config import ConfigError, load_config, save_credentials  # noqa: E402
from kvl_agent.jsonlog import setup_logging  # noqa: E402
from kvl_agent.metrics import os_info  # noqa: E402
from kvl_agent.service import Agent  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-url", default="http://127.0.0.1:8000/api/v1")
    ap.add_argument("--token", help="one-time enrollment token (first run only)")
    ap.add_argument("--data-dir", default=str(Path(__file__).parent / ".local-agent"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--health-port", type=int, default=9710)
    a = ap.parse_args()
    setup_logging()
    cfg = load_config(env={"KVL_AGENT_API_URL": a.api_url, "KVL_AGENT_DATA_DIR": a.data_dir, "KVL_AGENT_HEALTH_PORT": str(a.health_port), "KVL_AGENT_ALLOW_INSECURE_HTTP": "1", "KVL_AGENT_HEARTBEAT_INTERVAL": "10"})
    try:
        if not cfg.enrolled:
            if not a.token:
                print("not enrolled: pass --token", file=sys.stderr)
                return 2
            cfg.validate(require_credentials=False)
            res = ControlPlaneClient(cfg).enroll(a.token, socket.gethostname(), __version__, os_info())
            save_credentials(cfg, res["key_id"], res["secret"])
            print("enrolled; credentials stored locally")
        cfg.validate()
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    agent = Agent(cfg)
    if a.once:
        print(agent.run_once())
        return 0
    agent.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
