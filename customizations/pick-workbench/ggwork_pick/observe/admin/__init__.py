"""The observe operator commands: `python -m ggwork_pick.observe.admin <command> [args...]` (plan D35).

Each command is its own module here, cmd_<name>.py with underscores for the command's hyphens, exporting NAME (the
command name, e.g. "import-legacy" in cmd_import_legacy.py) and main(argv) -> int. A task adds its module and edits
nothing shared; __main__ finds the modules by file name and imports only the one it runs.
"""
