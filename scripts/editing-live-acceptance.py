"""Opt-in synthetic acceptance through real session, device, planner and media APIs.

Requires an already paired foreground native worker and a private JSON login file
containing email/password. This creates one task (and may spend one model call).
Use only an isolated Gateway and synthetic grant; see docs/editing/acceptance.md.
"""

import argparse
import hashlib
import json
import ssl
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--grant", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument(
        "--task-id",
        help="Observe an existing task without creating/model planning again",
    )
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    url = urlsplit(args.gateway)
    if (
        url.scheme != "https"
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        parser.error(
            "Use an isolated loopback HTTPS Gateway with a trusted certificate"
        )
    credentials = json.loads(args.credentials.read_text(encoding="utf-8"))
    args.results.mkdir(parents=True, exist_ok=True)
    context = ssl.create_default_context(cafile=str(args.ca) if args.ca else None)
    with httpx.Client(
        base_url=args.gateway, verify=context, trust_env=False, timeout=60
    ) as client:
        response = client.post(
            "/api/v1/auth/login/local",
            data={
                "username": credentials["email"],
                "password": credentials["password"],
            },
        )
        response.raise_for_status()
        client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
        task_id = args.task_id
        if not task_id:
            response = client.post(
                "/api/editing/tasks",
                json={
                    "request_id": args.request_id,
                    "title": "Synthetic live acceptance",
                    "requirements": {
                        "profile": "hook",
                        "instructions": "Select a coherent original-dialogue exchange beginning with a compelling conflict.",
                        "output_count": 1,
                        "duration_seconds": 8,
                        "aspect_ratio": "9:16",
                        "language": "en",
                    },
                    "device_id": args.device,
                    "source_directory": {"grant_id": args.grant, "relative_path": "."},
                },
            )
            response.raise_for_status()
            task_id = response.json()["id"]
        # Unique runs preserve earlier verified files and never reuse stale evidence.
        run_directory = args.results / uuid4().hex
        run_directory.mkdir(mode=0o700)
        evidence = {"task_id": task_id, "states": [], "outputs": [], "passed": False}
        partial = None
        try:
            deadline = time.monotonic() + args.timeout
            while True:
                response = client.get(f"/api/editing/tasks/{task_id}")
                response.raise_for_status()
                task = response.json()
                state = {"status": task["status"], "stage": task["stage"]}
                if not evidence["states"] or state != evidence["states"][-1]:
                    evidence["states"].append(state)
                    print(json.dumps({"task_id": task_id, **state}), flush=True)
                if (
                    task["status"] in {"completed", "failed", "partial", "stopped"}
                    or time.monotonic() >= deadline
                ):
                    break
                time.sleep(1)
            if task["status"] == "completed":
                for index, output in enumerate(task["outputs"]):
                    result = output["result"]
                    url = f"/api/editing/tasks/{task_id}/outputs/{output['id']}/content"
                    response = client.get(url, headers={"Range": "bytes=0-1023"})
                    assert response.status_code == 206 and len(response.content) == 1024
                    assert (
                        response.headers.get("Content-Range")
                        == f"bytes 0-1023/{result['size_bytes']}"
                    )
                    destination = run_directory / f"output-{index + 1}.mp4"
                    partial = destination.with_suffix(".partial")
                    digest = hashlib.sha256()
                    size = 0
                    with (
                        client.stream("GET", url) as response,
                        partial.open("xb") as stream,
                    ):
                        response.raise_for_status()
                        for chunk in response.iter_bytes(1024 * 1024):
                            digest.update(chunk)
                            size += len(chunk)
                            stream.write(chunk)
                    assert (
                        digest.hexdigest() == result["sha256"]
                        and size == result["size_bytes"]
                    )
                    subprocess.run(
                        [
                            "ffmpeg",
                            "-v",
                            "error",
                            "-xerror",
                            "-i",
                            str(partial),
                            "-f",
                            "null",
                            "-",
                        ],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    partial.replace(destination)
                    partial = None
                    evidence["outputs"].append(
                        {
                            "output_id": output["id"],
                            "sha256": digest.hexdigest(),
                            "size_bytes": size,
                            "range_206": True,
                            "full_decode": True,
                            "file": destination.name,
                        }
                    )
                evidence["passed"] = bool(evidence["outputs"])
        except (
            httpx.HTTPError,
            OSError,
            ValueError,
            KeyError,
            AssertionError,
            subprocess.CalledProcessError,
        ) as error:
            evidence["failure"] = type(error).__name__
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)
            evidence_path = run_directory / "evidence.json"
            evidence_path.write_text(
                json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
            )
            print(f"Evidence: {evidence_path}", flush=True)
        return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        httpx.HTTPError,
        OSError,
        ValueError,
        AssertionError,
        subprocess.CalledProcessError,
    ) as error:
        # Provider errors and response bodies may contain secrets. Keep logs safe.
        print(
            f"Acceptance failed: {type(error).__name__}; inspect isolated local evidence."
        )
        raise SystemExit(1) from None
