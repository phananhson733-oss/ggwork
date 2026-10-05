"""The host system prompt, cut to what the pick model can act on (evaluation batch 2, 2026-10-05).

The host prompt tells every agent to read_file a skill, save deliverables under /mnt/user-data and present_files
them, edit with str_replace, call tools in parallel and delegate to subagents. PickModelGate never passes those
tools to the pick model, and the pick deployment denies them outright, so each such line is an instruction the model
cannot follow. The cut drops the sections about them whole and their bullets from the shared lists; everything it
does not recognise passes through unchanged.
"""

import re

# Sections that only describe tools the pick model never gets.
SECTIONS = ("skill_system", "working_directory", "subagent_system")
# Lists whose other bullets stay: a bullet naming one of FOREIGN goes, with its indented continuation lines.
LISTS = ("thinking_style", "critical_reminders")
FOREIGN = re.compile(
    r"read_file|write_file|str_replace|present_files|list_uploaded_files|skill_manage|describe_skill|/mnt/"
    r"|parallel tool|Progressive Loading|`task`|[Dd]elegat|subagent"
)

# A section's tags stand on their own lines (LF or CRLF); the confidentiality paragraph names the same tags inline.
_SECTION = re.compile(r"(?:\r?\n)*^<(" + "|".join(SECTIONS) + r")\b[^>\r\n]*>\r?$.*?^</\1>\r?$(?:\r?\n)?", re.S | re.M)
_LIST = re.compile(r"(^<(" + "|".join(LISTS) + r")>\r?$)(.*?)(^</\2>\r?$)", re.S | re.M)


def pick_system(system: str) -> str:
    """The host prompt without the sections and bullets about tools the pick model lacks."""
    without_sections = _SECTION.sub(lambda match: "\r\n" if "\r\n" in match.group(0) else "\n", system)
    return _LIST.sub(lambda match: match.group(1) + _kept_bullets(match.group(3)) + match.group(4), without_sections)


def _kept_bullets(body: str) -> str:
    chunks: list[list[str]] = [[]]
    for line in body.splitlines(keepends=True):
        if line.startswith("- "):
            chunks.append([])
        chunks[-1].append(line)
    return "".join("".join(chunk) for chunk in chunks if not (chunk and chunk[0].startswith("- ") and FOREIGN.search("".join(chunk))))
