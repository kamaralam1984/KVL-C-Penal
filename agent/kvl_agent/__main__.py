"""CLI: `python -m kvl_agent enroll|run|check|version`."""
import argparse
import json
import socket
import sys

from kvl_agent import __version__
from kvl_agent.client import ControlPlaneClient, ControlPlaneError
from kvl_agent.config import AgentConfig, ConfigError, load_config, save_credentials
from kvl_agent.jsonlog import setup_logging
from kvl_agent.metrics import MetricsCollector, os_info
from kvl_agent.services import check_services


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kvl-agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll", help="exchange a one-time enrollment token for agent credentials")
    e.add_argument("--api-url", required=True)
    e.add_argument("--token", required=True)
    e.add_argument("--data-dir")
    e.add_argument("--allow-insecure-http", action="store_true", help="development only")
    sub.add_parser("run", help="run the agent")
    sub.add_parser("check", help="collect metrics and service status once, print them, do not contact the control plane")
    sub.add_parser("version")
    a = p.parse_args(argv)
    log = setup_logging()
    try:
        if a.cmd == "version":
            print(__version__)
            return 0
        if a.cmd == "check":
            c = MetricsCollector()
            print(json.dumps({"metrics": c.collect(), "services": check_services(), "os": os_info()}, indent=2))
            return 0
        if a.cmd == "enroll":
            env = {"KVL_AGENT_API_URL": a.api_url}
            if a.data_dir:
                env["KVL_AGENT_DATA_DIR"] = a.data_dir
            if a.allow_insecure_http:
                env["KVL_AGENT_ALLOW_INSECURE_HTTP"] = "1"
            cfg = load_config(env=env)
            cfg.validate(require_credentials=False)
            if cfg.credentials_path.exists():
                print("already enrolled; refusing to overwrite credentials (delete the file to re-enroll)", file=sys.stderr)
                return 2
            res = ControlPlaneClient(cfg).enroll(a.token, socket.gethostname(), __version__, os_info())
            save_credentials(cfg, res["key_id"], res["secret"])
            print(f"enrolled as server {res['server_id']}; credentials stored at {cfg.credentials_path} (mode 600)")
            return 0
        cfg = load_config()
        cfg.validate()
        from kvl_agent.service import Agent

        setup_logging(cfg.log_level)
        Agent(cfg).run_forever()
        return 0
    except ConfigError as ex:
        print(f"configuration error: {ex}", file=sys.stderr)
        return 2
    except ControlPlaneError as ex:
        print(f"control plane rejected the request: {ex.status} {ex.code}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
