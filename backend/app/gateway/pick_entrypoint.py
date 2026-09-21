"""Start the personal pick deployment with its database on a persistent volume."""

import json
import os
from pathlib import Path

import uvicorn
import yaml


def prepare_config(home: Path, template: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = yaml.safe_load(template.read_text())
    config["database"]["sqlite_dir"] = str(home / "data")
    path = home / "pick-runtime.yaml"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    temporary.chmod(0o600)
    temporary.replace(path)
    extensions = home / "extensions_config.json"
    if not extensions.exists():
        with extensions.open("x") as stream:
            json.dump({"mcpServers": {}, "skills": {}}, stream)
        extensions.chmod(0o600)
    return path


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    home = Path(os.environ.get("DEER_FLOW_HOME", "/data")).resolve()
    config = prepare_config(home, root / "config.pick.example.yaml")
    os.environ["DEER_FLOW_PROJECT_ROOT"] = str(root)
    os.environ["DEER_FLOW_HOME"] = str(home)
    os.environ["DEER_FLOW_CONFIG_PATH"] = str(config)
    os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = str(home / "extensions_config.json")
    os.environ.setdefault("PICK_RUN_TIMEOUT_SECONDS", "120")
    uvicorn.run("app.gateway.app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8001")), workers=1)


if __name__ == "__main__":
    main()
