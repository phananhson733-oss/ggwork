"""The pick image bundles one verified lark-cli release and an unprivileged user to run it
(docs/pick-workbench/lark-personal-auth.md section 3, items 1–2)."""

import re
from pathlib import Path

from deerflow.integrations import lark_cli

from ggwork_pick import lark_runner

# Found from the repository root, so the managed copy's tests read the same file.
DOCKERFILE = next(
    parent / "docker/Dockerfile.pick-gateway" for parent in Path(__file__).resolve().parents if (parent / "docker/Dockerfile.pick-gateway").is_file()
)


def _lines() -> list[str]:
    return [line.strip() for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()]


def _final_stage() -> list[str]:
    lines = _lines()
    last_from = max(index for index, line in enumerate(lines) if line.startswith("FROM "))
    return [line for line in lines[last_from + 1 :] if line and not line.startswith("#")]


def _arg(name: str) -> str:
    match = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, name
    return match.group(1)


def test_the_release_and_both_digests_are_pinned():
    assert re.fullmatch(r"v\d+\.\d+\.\d+", _arg("LARK_CLI_VERSION"))
    assert re.fullmatch(r"[0-9a-f]{64}", _arg("LARK_CLI_SHA256_AMD64"))
    assert re.fullmatch(r"[0-9a-f]{64}", _arg("LARK_CLI_SHA256_ARM64"))


def test_the_download_is_checked_against_the_pinned_digest():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "AS lark-cli" in text
    assert "https://github.com/larksuite/cli/releases/download/${LARK_CLI_VERSION}/" in text
    assert "sha256sum -c" in text
    assert "latest" not in text.lower()
    assert not re.search(r"\b(npm|npx|node)\b", text)


def test_the_final_image_names_the_pin_and_the_lark_user():
    stage = _final_stage()
    assert "COPY --from=lark-cli /usr/local/bin/lark-cli /usr/local/bin/lark-cli" in stage
    assert any(line.startswith("RUN useradd ") and line.endswith(" larkrun") for line in stage)
    env = " ".join(line for line in stage if line.startswith("ENV "))
    assert f"{lark_cli.LARK_CLI_PINNED_VERSION_ENV}=${{LARK_CLI_VERSION}}" in env
    assert f"{lark_runner.RUN_AS_ENV}=larkrun" in env


def test_the_lark_user_cannot_log_in_and_owns_no_home():
    (useradd,) = [line for line in _final_stage() if line.startswith("RUN useradd ")]
    assert "--system" in useradd and "--no-create-home" in useradd and "/usr/sbin/nologin" in useradd


def test_the_entrypoint_closes_the_home_for_the_same_lark_user_variable():
    entrypoint = (DOCKERFILE.parents[1] / "backend/app/gateway/pick_entrypoint.py").read_text(encoding="utf-8")
    assert f'LARK_CLI_RUN_AS_ENV = "{lark_runner.RUN_AS_ENV}"' in entrypoint
