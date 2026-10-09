"""Foreground, explicit native setup. Pairing secrets are read only with getpass."""

import argparse
import asyncio
import getpass
import json
from pathlib import Path

from .storage import WorkerError, WorkerStore


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ggwork-edit-worker")
    parser.add_argument("--home", type=Path, default=Path.home() / ".local/share/ggwork-edit")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup")
    for flag in ("gateway", "device-id", "output-root", "model", "model-sha256"):
        setup.add_argument("--" + flag, required=True)
    setup.add_argument("--model-language", choices=("en", "multilingual"), required=True)
    grant = commands.add_parser("grant")
    grant.add_argument("grant_id")
    grant.add_argument("directory", type=Path)
    grant.add_argument("--receive", action="store_true")
    commands.add_parser("doctor")
    commands.add_parser("run")
    args = parser.parse_args(argv)
    try:
        store = WorkerStore(args.home)
        if args.command == "setup":
            store.setup(
                gateway=args.gateway,
                device_id=args.device_id,
                token=getpass.getpass("Device pairing token: "),
                output_root=args.output_root,
                model=args.model,
                model_sha256=args.model_sha256,
                model_language=args.model_language,
            )
            print("Paired configuration saved. Grant a source directory, then run doctor.")
        elif args.command == "grant":
            with store.lock():
                store.grant(args.grant_id, args.directory, receive=args.receive)
            print("Directory grant saved.")
        elif args.command == "doctor":
            from .native import doctor

            result = asyncio.run(doctor(store))
            print(json.dumps(result))
            return 0 if result["ready"] else 1
        else:
            from .runtime import run

            with store.lock():
                asyncio.run(run(store))
        return 0
    except (WorkerError, OSError, ValueError) as error:
        print(str(error) if isinstance(error, WorkerError) else "local_configuration_error")
        return 1
    except KeyboardInterrupt:
        return 130
