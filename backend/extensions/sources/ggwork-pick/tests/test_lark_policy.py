"""The argument policy of the read-only lark_cli tool (docs/pick-workbench/lark-personal-auth.md section 3)."""

import pytest

from ggwork_pick.lark_policy import DOMAINS, Command, LarkRefused, check_args, risk_from_help

FETCH_HELP = """Fetch Lark document content

Risk: read

Usage:
  lark-cli docs +fetch [flags]

Flags:
      --doc string   document URL or token
"""
SEND_HELP = FETCH_HELP.replace("Risk: read", "Risk: write").replace("docs +fetch", "im +messages-send")
GROUP_HELP = """Document and content operations

Usage:
  lark-cli docs [flags]
  lark-cli docs [command]
"""


def test_a_read_command_passes_and_keeps_its_arguments():
    command = check_args(["docs", "+fetch", "--doc", "https://example.feishu.cn/docx/AbC123", "--doc-format", "markdown"])

    assert command == Command(
        args=("docs", "+fetch", "--doc", "https://example.feishu.cn/docx/AbC123", "--doc-format", "markdown"),
        path=("docs", "+fetch"),
        guide=False,
    )


def test_a_leading_program_name_is_dropped():
    assert check_args(["lark-cli", "wiki", "--help"]).args == ("wiki", "--help")


def test_the_chosen_domains_are_documents_and_messages():
    assert DOMAINS == {"docs", "wiki", "drive", "sheets", "base", "markdown", "slides", "mindnotes", "whiteboard", "im"}


@pytest.mark.parametrize(
    "group",
    ["config", "auth", "profile", "update", "event", "doctor", "apps", "application", "api", "calendar", "mail", "whoami", "completion"],
)
def test_other_command_groups_are_refused(group):
    with pytest.raises(LarkRefused, match="不开放"):
        check_args([group, "show"])


@pytest.mark.parametrize(
    "args",
    [
        ["skills", "read", "lark-doc"],
        ["skills", "list"],
        ["schema", "im.chats.list"],
        ["help", "docs"],
        ["docs", "--help"],
        ["docs", "+fetch", "-h"],
    ],
)
def test_guides_need_no_risk_lookup(args):
    assert check_args(args).guide


def test_skills_only_lists_and_reads():
    with pytest.raises(LarkRefused, match="skills"):
        check_args(["skills", "install", "lark-doc"])


@pytest.mark.parametrize("flag", ["--yes", "--profile", "--dry-run", "--yes=true", "--profile=other"])
def test_confirmation_profile_and_dry_run_flags_are_refused(flag):
    with pytest.raises(LarkRefused, match="参数"):
        check_args(["docs", "+fetch", "--doc", "AbC123", flag])


@pytest.mark.parametrize(
    "value",
    [
        "@/proc/1/environ",
        "@body.json",
        "-",
        "/etc/passwd",
        "~/.ssh/id_rsa",
        "\\\\server\\share",
        "../../data/users/other",
        "a/../../b",
        "..",
        "file:///etc/passwd",
        "FILE:/etc/passwd",
    ],
)
def test_local_file_references_are_refused(value):
    with pytest.raises(LarkRefused, match="本地文件"):
        check_args(["docs", "+fetch", "--doc", value])


@pytest.mark.parametrize("value", ["@/proc/1/environ", "/etc/passwd", "../x"])
def test_local_file_references_inside_an_equals_flag_are_refused(value):
    with pytest.raises(LarkRefused, match="本地文件"):
        check_args(["docs", "+update", f"--content={value}"])


def test_a_positional_file_reference_is_refused():
    with pytest.raises(LarkRefused, match="本地文件"):
        check_args(["drive", "+upload", "/data/config.yaml"])


@pytest.mark.parametrize("expression", [".data.items[] | @csv", "@json", ".a / .b", '.x | test("..")'])
def test_an_attached_jq_expression_is_not_a_file_reference(expression):
    for flag in ("--jq", "-q"):
        assert check_args(["im", "+chat-search", "--query", "ops", f"{flag}={expression}"]).path == ("im", "+chat-search")


@pytest.mark.parametrize("expression", [".data.items[] | @csv", ".a / .b", '.x | test("..")'])
def test_a_separate_jq_expression_passes_the_ordinary_value_checks(expression):
    for flag in ("--jq", "-q"):
        assert check_args(["im", "+chat-search", "--query", "ops", flag, expression]).path == ("im", "+chat-search")


def test_a_separate_jq_expression_that_looks_like_a_file_is_checked_and_told_to_attach():
    # lark-cli may have taken `-q` as the previous flag's value, and then this token is not a jq expression at all.
    for flag in ("--jq", "-q"):
        with pytest.raises(LarkRefused, match="--jq=") as refused:
            check_args(["im", "+chat-search", "--query", "ops", flag, "@json"])
        assert "本地文件" in str(refused.value)


def test_only_the_jq_value_is_exempt():
    with pytest.raises(LarkRefused, match="本地文件"):
        check_args(["docs", "+fetch", "--jq=.x", "--doc", "@/etc/passwd"])


@pytest.mark.parametrize(
    "args",
    [
        # 09-29 review: lark-cli took `-q` as --base-token's value, so the next token was a real flag it read a file for.
        ["base", "+record-list", "--base-token", "-q", "--filter-json=@/abs/file"],
        ["base", "+record-list", "--base-token", "--jq", "--filter-json=@/abs/file"],
        ["docs", "+fetch", "--doc", "-q", "@/etc/passwd"],
        ["base", "+record-list", "--base-token", "-q", "--yes"],
    ],
)
def test_a_jq_flag_never_hides_the_token_after_it(args):
    with pytest.raises(LarkRefused):
        check_args(args)


@pytest.mark.parametrize("pad", [" ", "\u00a0", "\u3000", "\u200b", "\ufeff", " \u3000"])
@pytest.mark.parametrize("value", ["@/tmp/x", "/etc/passwd", "~/.ssh/id_rsa", "../x", "-"])
def test_whitespace_around_a_file_reference_does_not_hide_it(pad, value):
    """09-29 review: lark-cli trims Unicode whitespace before it treats a value as @file."""
    with pytest.raises(LarkRefused, match="本地文件"):
        check_args(["base", "+record-list", "--filter-json", pad + value])
    with pytest.raises(LarkRefused, match="本地文件"):
        check_args(["base", "+record-list", f"--filter-json={pad}{value}"])
    # A trailing blank: `- ` is dash-led, so it is refused as neither a flag name nor a number.
    with pytest.raises(LarkRefused):
        check_args(["base", "+record-list", "--filter-json", value + pad])


@pytest.mark.parametrize("value", ["https://example.feishu.cn/wiki/AbC?from=a/b", "a@b.com", '{"path": "/x"}', "-1", "10"])
def test_ordinary_values_pass(value):
    check_args(["docs", "+fetch", "--doc", value])


@pytest.mark.parametrize("args", [[], ["lark-cli"], ["--help"], ["--doc", "x"]])
def test_a_command_group_is_required(args):
    with pytest.raises(LarkRefused):
        check_args(args)


@pytest.mark.parametrize("bad", ["a\nb", "a\x00b", "a\rb", "x" * 2001, 3, None])
def test_control_characters_long_and_non_text_arguments_are_refused(bad):
    with pytest.raises(LarkRefused):
        check_args(["docs", "+fetch", "--doc", bad])


def test_too_many_arguments_are_refused():
    with pytest.raises(LarkRefused, match="参数"):
        check_args(["docs", "+fetch", *(["--doc", "x"] * 21)])


def test_a_bare_string_is_refused():
    with pytest.raises(LarkRefused, match="列表"):
        check_args("docs +fetch")


def test_help_text_yields_the_resolved_command_and_its_risk():
    assert risk_from_help(("docs", "+fetch"), FETCH_HELP) == "read"
    assert risk_from_help(("im", "+messages-send"), SEND_HELP) == "write"


def test_group_help_is_not_a_command():
    """An unknown subcommand makes lark-cli print its group's help with exit 0: that is no answer."""
    with pytest.raises(LarkRefused, match="只读"):
        risk_from_help(("docs", "+nonexistent"), GROUP_HELP)


def test_help_for_another_group_is_refused():
    with pytest.raises(LarkRefused, match="只读"):
        risk_from_help(("docs", "+fetch"), SEND_HELP)


def test_help_without_a_risk_label_is_refused():
    with pytest.raises(LarkRefused, match="只读"):
        risk_from_help(("docs", "+fetch"), FETCH_HELP.replace("Risk: read\n", ""))


@pytest.mark.parametrize("token", ["-/etc/passwd", "-@body.json", "-~/x", "-../x", "-q.data", "--doc/x", "-x y", "--do/c=x"])
def test_a_dash_led_token_must_be_a_flag_name_or_a_number(token):
    """lark-cli takes `--doc -/etc/passwd` as a value (security review 2026-09-29), so a dash is no free pass."""
    with pytest.raises(LarkRefused, match="参数"):
        check_args(["docs", "+fetch", "--doc", token])


@pytest.mark.parametrize("token", ["-1", "-10", "-1.5", "-h", "--doc-format", "--as", "--revision-id=-1"])
def test_flag_names_and_negative_numbers_pass(token):
    check_args(["docs", "+fetch", "--doc", "AbC", token])
