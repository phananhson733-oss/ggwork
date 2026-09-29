"""The observe operator commands: `python -m ggwork_pick.observe.admin <command> [args...]` (plan D35).

Each command is its own module here, cmd_<name>.py with underscores for the command's hyphens, exporting NAME (the
command name, e.g. "reset-disable" in cmd_reset_disable.py) and main(argv) -> int. A task adds its module and edits
nothing shared; __main__ finds the modules by file name and imports only the one it runs. The modules that exist today
are listed in __main__'s docstring; cmd_import_legacy.py (`import-legacy`) is planned in TR-22 and not built.
"""
