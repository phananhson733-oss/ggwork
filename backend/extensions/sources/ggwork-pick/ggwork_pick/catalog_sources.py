"""Operator-owned read-only source allowlist, matching the imported catalog collector."""

SHEETS = {
    "RRBAszuhOhNM8StMqRVcGknSnyf": ("7ba1a7", "9WGFv2", "weGTx6", "OrwcFd", "GhBujK"),
    "HnxRsUam8hCH7ltOsTSceXplnic": ("471SFU", "icA5Xf", "JIOH6H", "BqgaO4", "RkkUgO"),
    "Kg0osz7hAhRw3ltzSzrcw1SMnkh": ("5f20e7", "c1Fgzk", "VOxpIN"),
    "HYvBsZBbWhKgTxt5OxDcGg6Ynwg": ("baaa17",),
    "Q4NmsixaThGYEZtBUR1cbmnFnAh": ("d49b4e", "z6DfWg"),
    "MRdRsef0jhRLTXtG6vHcVEuInch": ("omjCKZ",),
}
BASES = {
    "FIlebFMQta8mQEsQoV3cxWaunlg": ("投放榜单剧🔥", "每日推荐必看", "AI力荐新剧🎬"),
    "ZpUub25STaYxdisfenkc1iVpnVb": ("达人分销剧单", "FlickReels爆款剧单", "FlickReels下架剧单"),
    "F1Jabe1VsaELajsIg8YcgCNXnfb": ("ai剧专区", "英语剧单", "小语种剧单"),
    "OtnsbnRnwaLmnVsJByscTkFMntd": ("tbl4efRfwhJRqryA", "tbl5Kzrhuz9B7LTE", "tblHeWrgRPNshRdE"),
}


def export_arguments(user_id: str, kind: str, token: str, table: str, offset: int = 0) -> tuple[str, ...]:
    if not user_id or user_id in {"default", "system:shared"}:
        raise ValueError("A configured source owner is required")
    if type(offset) is not int or not 0 <= offset <= 100_000:
        raise ValueError("Invalid source offset")
    if kind == "sheet" and table in SHEETS.get(token, ()) and offset == 0:
        return ("sheets", "+csv-get", "--spreadsheet-token", token, "--sheet-id", table, "--as", "user", "--output-path", "source.json")
    if kind == "base" and table in BASES.get(token, ()):
        return (
            "base",
            "+record-list",
            "--base-token",
            token,
            "--table-id",
            table,
            "--as",
            "user",
            "--format",
            "ndjson",
            "--limit",
            "2000",
            "--offset",
            str(offset),
            "--output",
            "source.ndjson",
            "--overwrite",
        )
    raise ValueError("Unknown catalog source")
