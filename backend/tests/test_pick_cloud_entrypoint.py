import json
from pathlib import Path

import yaml


def test_cloud_config_uses_persistent_home_and_preserves_extensions(tmp_path):
    from app.gateway.pick_entrypoint import prepare_config

    home = tmp_path / "data"
    home.mkdir()
    extensions = home / "extensions_config.json"
    extensions.write_text('{"mcpServers":{},"skills":{"synthetic":{}}}')
    template = Path(__file__).resolve().parents[2] / "config.pick.example.yaml"
    path = prepare_config(home, template)
    config = yaml.safe_load(path.read_text())
    assert config["database"]["sqlite_dir"] == str(home / "data")
    assert config["models"][0]["name"] == "azure-pick"
    assert config["models"][0]["api_key"] == "$AZURE_OPENAI_API_KEY"
    assert "synthetic" in json.loads(extensions.read_text())["skills"]
