import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker


def test_unknown_titles_and_save_claims_are_flagged():
    from ggwork_pick.answer_check import check_answer

    notes = check_answer(
        "推荐《Known One》和《Invented Drama》，已为你保存到清单。",
        known_titles={"Known One"},
        posted_checked=False,
    )
    joined = "\n".join(notes)
    assert "《Invented Drama》" in joined and "Known One" not in joined
    assert "确认保存" in joined


def test_not_posted_claim_requires_a_posted_filter():
    from ggwork_pick.answer_check import check_answer

    assert any("发布记录" in n for n in check_answer("这5部都没发过。", known_titles=set(), posted_checked=False))
    assert check_answer("这5部都没发过。", known_titles=set(), posted_checked=True) == []


def test_negated_or_quoted_phrases_do_not_trigger():
    from ggwork_pick.answer_check import check_answer

    text = "点击「确认保存」后才会写入。没有接入账号发布记录时不能声称没发过。尚未保存。"
    assert check_answer(text, known_titles=set(), posted_checked=False) == []


def test_title_match_ignores_case_and_spacing():
    from ggwork_pick.answer_check import check_answer

    assert check_answer("《the  ceo's wife》", known_titles={"The CEO's Wife"}, posted_checked=False) == []


def test_ordinary_wording_and_relayed_tool_text_are_not_claims():
    from ggwork_pick.answer_check import check_answer

    # Zero-result wording, the sanctioned "no record" phrasing, and text the tools themselves return.
    for text in (
        "这次没有发现符合条件的剧。",
        "未发现符合条件的剧目，建议放宽条件。",
        "数据没发生变化。",
        "这部发布记录里没有，也就是没有发布记录。",
        "当前剧库批次没有发布记录，无法核对是否发过。",
        "第2部发布记录显示已排期未发（1条待公开）。",
        "我已加入“排除已选”的条件重新查询。",
        "已添加英语筛选。",
        "请点击卡片上的确认保存，保存完成后会显示回执。",
        "默认排除你已保存的剧。",
    ):
        assert check_answer(text, known_titles=set(), posted_checked=False) == [], text


def test_real_claims_are_still_caught_and_a_comma_ends_a_disclaimer():
    from ggwork_pick.answer_check import check_answer

    for text in ("这3部都没发过。", "《A》从来没有发布过。", "尚未发布。", "关于是否发过，它从没发过。"):
        assert any("发布记录" in n for n in check_answer(text, known_titles={"A"}, posted_checked=False)), text
    for text in ("已存入个人清单。", "已为你保存第1部。", "保存成功。", "已添加到你的选剧清单。"):
        assert any("确认保存" in n for n in check_answer(text, known_titles=set(), posted_checked=True)), text


def test_long_catalog_titles_are_checked():
    from ggwork_pick.answer_check import check_answer

    long_title = "长" * 300
    assert check_answer(f"推荐《{long_title}》", known_titles={long_title}, posted_checked=False) == []
    assert check_answer(f"推荐《{long_title}》", known_titles=set(), posted_checked=False) != []


def _item(title, *, matched, posts=0, accounts=()):
    """A returned candidate item with the posted summary selection.candidate_item copies from its row."""
    records = ["SD-1"] if matched else []
    posted = {"matched": matched, "records": records, "post_count": posts, "sched_count": 0, "last_post_on": None, "accounts": list(accounts)}
    return {"title": title, "posted": posted}


def test_a_matched_record_without_posts_backs_the_not_posted_claim():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=True)])
    for text in (
        "《Lost Heir》发布记录已对上，帖子数0，团队还没发过。",
        "《Lost Heir》：目前未发布过。",
        "发布记录已对上，帖子数0，团队还没发过。",
        "目前未发布过。",
        "《Lost Heir》已对上，发帖0次。",
    ):
        assert check_answer(text, known_titles={"Lost Heir"}, posted_checked=False, posted_seen=seen) == [], text


def test_unmatched_and_posted_records_do_not_back_it():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=False), _item("Big Boss", matched=True, posts=3)])
    known = {"Lost Heir", "Big Boss"}
    for text in ("《Lost Heir》从未发布。", "《Lost Heir》：发布记录已查，团队还没发过。"):
        for checked in (False, True):
            notes = check_answer(text, known_titles=known, posted_checked=checked, posted_seen=seen)
            assert notes == ["《Lost Heir》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"], (text, checked)
    notes = check_answer("《Big Boss》没发过。", known_titles=known, posted_checked=False, posted_seen=seen)
    assert notes == ["发布记录显示《Big Boss》发过，不能说没发过。"]
    notes = check_answer("目前未发布过。", known_titles=known, posted_checked=False, posted_seen=seen)
    assert notes == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]
    assert check_answer("《Lost Heir》发布记录里没有。", known_titles=known, posted_checked=False, posted_seen=seen) == []


def test_each_claim_is_judged_by_the_titles_it_is_about():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=True), _item("Big Boss", matched=True, posts=3), _item("No Match", matched=False)])
    known = {"Lost Heir", "Big Boss", "No Match"}
    for text in (
        "《Big Boss》发过3次，《Lost Heir》还没发过。",
        "《Lost Heir》没发过；《No Match》发布记录里没有。",
        "1. 《Lost Heir》\n   发布记录已对上，帖子数0，团队还没发过\n2. 《No Match》发布记录里没有",
    ):
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=seen) == [], text
    notes = check_answer("《Lost Heir》和《No Match》都没发过。", known_titles=known, posted_checked=False, posted_seen=seen)
    assert notes == ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]
    notes = check_answer("这三部都没发过。", known_titles=known, posted_checked=False, posted_seen=seen)
    assert notes == ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。", "发布记录显示《Big Boss》发过，不能说没发过。"]


def test_a_claim_borrows_titles_only_within_its_sentence_or_list_item():
    from ggwork_pick.answer_check import check_answer, with_posted

    both = with_posted({}, [_item("No Match", matched=False), _item("Lost Heir", matched=True)])
    only_heir = with_posted({}, [_item("Lost Heir", matched=True)])
    known = {"No Match", "Lost Heir", "Big Boss"}
    unmatched = ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]
    generic = ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]
    for seen, text, expected in (
        # A summary counts every returned record, not the title named just before it.
        (both, "《No Match》：发布记录里没有。《Lost Heir》：已对上。两部都从未发布。", unmatched),
        (both, "1. 《No Match》\n2. 《Lost Heir》\n以上两部都没发过。", unmatched),
        # A sentence end, or a line not indented deeper than the one its subject starts on, stops the borrowing: a bare
        # title this run returned is judged by its own record.
        (both, "《Lost Heir》已对上。No Match 没发过。", unmatched),
        (both, "1. 《Lost Heir》已对上\n2. 也没发过", generic),
        (both, "  1. 《Lost Heir》已对上\n  2. 也没发过", generic),
        (both, "《Lost Heir》已对上\n\n   也没发过", generic),
        # A list item's details under its title, as lines or nested bullets, are about that title.
        (both, "1. 《Lost Heir》\n   - 发布记录已对上\n   - 团队还没发过\n2. 《No Match》：发布记录里没有", []),
        (both, "- 《Lost Heir》\n  已对上，\n  还没发过", []),
        # A name no tool returned is neither borrowed over nor waved through.
        (only_heir, "Big Boss 没发过。", generic),
        (only_heir, "《Lost Heir》帖子数0；Big Boss 也没发过。", generic),
        (only_heir, "《Lost Heir》已对上，Big Boss 也没发过。", generic),
        (only_heir, "《Lost Heir》和 Big Boss 都没发过。", generic),
    ):
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=seen) == expected, text
    # Platform and theater names, language codes: one Latin word is not taken for a drama name.
    for text in (
        "《Lost Heir》已对上，在 YouTube 上还没发过。",
        "《Lost Heir》是 DramaBox 的剧，已对上，团队还没发过。",
        "《Lost Heir》（ReelShort，en）已对上，还没发过。",
    ):
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=only_heir) == [], text


def test_an_account_query_backs_a_claim_about_that_account_only():
    from ggwork_pick.answer_check import check_answer, with_posted

    # posted_account=A returned Big Boss: the team posted it twice, from account B.
    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2, accounts=["B"])], account="A")
    known = {"Big Boss"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    assert check_answer("《Big Boss》在 A 账号没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == []
    assert check_answer("《Big Boss》：A 账号，还没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == []
    for text in ("《Big Boss》没发过。", "《Big Boss》团队还没发过。", "《Big Boss》在 B 账号没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    # "这个账号" is the account queried; naming the team, even in a clause that goes on from A's, is about every account.
    for text in ("《Big Boss》这个账号还没发过。", "这几部在该账号都没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == [], text
    for text in ("《Big Boss》在 A 账号没发过，团队也没发过。", "《Big Boss》在 A 账号没发过。团队也没发过。", "《Big Boss》在 A 账号和团队都没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    # An account the records name as posting it is never cleared by another query.
    both = with_posted(seen, [_item("Big Boss", matched=True, posts=3, accounts=["A", "B"])])
    assert check_answer("《Big Boss》在 A 账号没发过。", known_titles=known, posted_checked=True, posted_seen=both) == posted


def test_after_a_posted_filter_a_claim_naming_nothing_stands_on_every_record_returned():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=True), _item("Big Boss", matched=True, posts=3)])
    known = {"Lost Heir", "Big Boss"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    for text in ("团队还没发过。", "还没发过。", "以下是团队还没发过的剧："):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
        # Without the filter nothing backs it at all, whichever record it is about.
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=seen) == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"], text
    fine = with_posted({}, [_item("Lost Heir", matched=True)])
    assert check_answer("团队还没发过。", known_titles=known, posted_checked=True, posted_seen=fine) == []
    assert check_answer("团队还没发过。", known_titles=known, posted_checked=True, posted_seen={}) == []


def test_a_title_is_neither_split_by_its_punctuation_nor_read_as_a_claim():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Oops! Wed, Again", matched=True)])
    assert check_answer("《Oops! Wed, Again》发布记录已对上，团队还没发过。", known_titles={"Oops! Wed, Again"}, posted_checked=False, posted_seen=seen) == []
    assert check_answer("推荐《从未发布的秘密》。", known_titles={"从未发布的秘密"}, posted_checked=False) == []


def test_a_title_returned_with_conflicting_records_keeps_the_one_that_refutes():
    from ggwork_pick.answer_check import check_answer, with_posted

    # Two items with one title (two languages) that disagree, and an item from a batch without publication records.
    seen = with_posted(with_posted({}, [_item("Twin", matched=True)]), [_item("twin", matched=False), {"title": "Plain"}])
    known = {"Twin", "Plain"}
    unmatched = "《Twin》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"
    generic = "本轮查询没有按发布记录过滤，不能据此断言没发过。"
    for checked in (False, True):
        assert check_answer("《Twin》没发过。", known_titles=known, posted_checked=checked, posted_seen=seen) == [unmatched], checked
    assert check_answer("《Plain》没发过。", known_titles=known, posted_checked=False, posted_seen=seen) == [generic]
    assert check_answer("《Plain》没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == []
    assert check_answer("都没发过。", known_titles=known, posted_checked=False, posted_seen=seen) == [unmatched, generic]
    posted_and_unmatched = with_posted(seen, [_item("Twin", matched=True, posts=1)])
    notes = check_answer("《Twin》没发过。", known_titles=known, posted_checked=True, posted_seen=posted_and_unmatched)
    assert notes == ["发布记录显示《Twin》发过，不能说没发过。"]


def test_a_long_list_answer_is_judged_item_by_item():
    from ggwork_pick.answer_check import check_answer, with_posted

    items = [_item(f"Drama {index}", matched=index != 1_500) for index in range(2_000)]
    seen = with_posted({}, items)
    lines = "\n".join(f"{index + 1}. 《Drama {index}》\n   发布记录已对上，帖子数0，团队还没发过" for index in range(2_000))
    notes = check_answer(lines, known_titles={item["title"] for item in items}, posted_checked=False, posted_seen=seen)
    assert notes == ["《Drama 1500》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]


def test_a_summary_covers_what_it_sums_up():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2), _item("Lost Heir", matched=True)])
    known = {"Big Boss", "Lost Heir"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    # "以上" sums up the titles named before it as well as the one it names.
    for text in ("《Big Boss》已发过；包括《Lost Heir》在内的以上两部都没发过。", "《Big Boss》发过。\n《Lost Heir》已对上。\n以上都没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    # "其余" and the like leave out the titles named up to them.
    for text in (
        "《Big Boss》发过，其余都没发过。",
        "除了《Big Boss》，其他都没发过。",
        "《Big Boss》发过。剩下的都没发过。",
        "除了《Big Boss》都没发过。",
        "《Big Boss》以外其余都没发过。",
    ):
        for checked in (False, True):
            assert check_answer(text, known_titles=known, posted_checked=checked, posted_seen=seen) == [], (text, checked)
    # The rest still stands on the records returned for it.
    three = with_posted(seen, [_item("No Match", matched=False)])
    notes = check_answer("《Big Boss》发过，其余都没发过。", known_titles=known | {"No Match"}, posted_checked=True, posted_seen=three)
    assert notes == ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]


def test_a_clause_naming_an_account_replaces_the_account_of_the_clause_before():
    from ggwork_pick.answer_check import check_answer, with_posted

    # posted_account=A returned Big Boss; account B posted it.
    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2, accounts=["B"])], account="A")
    known = {"Big Boss"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    for text in ("《Big Boss》在 B 账号发过，在 A 账号没发过。", "《Big Boss》团队发过，在 A 账号还没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == [], text
    # A clause naming no account goes on with the one before; one naming the account that posted it is refuted.
    assert check_answer("《Big Boss》在 B 账号发过，在 A 账号，还没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == []
    assert check_answer("《Big Boss》在 A 账号没发过，在 B 账号也没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == posted


def test_a_clause_about_accounts_no_query_cleared_is_about_the_team():
    from ggwork_pick.answer_check import check_answer, with_posted

    # posted_account=A returned Big Boss; account B posted it.
    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2, accounts=["B"])], account="A")
    known = {"Big Boss"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    # The other accounts, every account, or an account no query asked about, however worded, never go on with A's clause.
    others = (
        "其他账户",
        "别的账户",
        "所有账户",
        "其他几个账号",
        "其他三个账号",
        "任意账号",
        "每个账号",
        "其他所有账号",
        "另外的账号",
        "其他团队成员",
        "C 账号",
        "其他号",
        "A 以外的账号",
        "A 之外的账号",
        "除 A 外的账号",
        "非 A 账号",
    )
    for other in others:
        text = f"《Big Boss》在 A 账号没发过，{other}也没发过。"
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    assert check_answer("《Big Boss》在 A 账号和其他账户都没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == posted
    # A clause naming no account still goes on with A's.
    assert check_answer("《Big Boss》在 A 账号没发过，之前也没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == []


def test_a_summary_of_more_titles_than_the_answer_names_covers_every_record():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=True), _item("Big Boss", matched=True, posts=2), _item("No Match", matched=False)])
    known = {"Lost Heir", "Big Boss", "No Match"}
    refuted = ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。", "发布记录显示《Big Boss》发过，不能说没发过。"]
    # "以上3部" or "以上全部" after naming one title sums up the cards the answer never named as well.
    for text in (
        "首推《Lost Heir》。以上3部团队都没发过。",
        "首推《Lost Heir》。以上三部都没发过。",
        "首推《Lost Heir》。上述3部都没发过。",
        "首推《Lost Heir》。以上全部都没发过。",
        "首推《Lost Heir》。以上所有剧目都没发过。",
        "首推《Lost Heir》。以上都没发过。",
    ):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == refuted, text
    # Named as many titles as it sums up: only those.
    two = with_posted(seen, [_item("Other", matched=True)])
    for text in ("首推《Lost Heir》和《Other》。以上两部都没发过。", "首推《Lost Heir》。以上一部没发过。", "首推《Lost Heir》，以上没发过。"):
        assert check_answer(text, known_titles=known | {"Other"}, posted_checked=True, posted_seen=two) == [], text
    # "全部/所有" is every card however many titles were named; a count may use another measure word, or spaces.
    for text in (
        "首推《Lost Heir》和《Other》。以上全部团队没发过。",
        "首推《Lost Heir》和《Other》。以上所有剧都没发过。",
        "首推《Lost Heir》和《Other》。以上5个都没发过。",
        "首推《Lost Heir》和《Other》。以上 4 部都没发过。",
        "首推《Lost Heir》和《Other》。以上四条都没发过。",
    ):
        assert check_answer(text, known_titles=known | {"Other"}, posted_checked=True, posted_seen=two) == refuted, text
    # Summing up every card still judges a title it names that nothing returned.
    heir = with_posted({}, [_item("Lost Heir", matched=True)])
    notes = check_answer("首推《Zeta》，以上都没发过。", known_titles={"Zeta"}, posted_checked=False, posted_seen=heir)
    assert notes == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]


def test_a_title_this_run_returned_is_recognized_without_its_brackets():
    from ggwork_pick.answer_check import check_answer, with_posted

    heir = with_posted({}, [_item("Lost Heir", matched=True)])
    both = with_posted(heir, [_item("Big Boss", matched=True, posts=3)])
    known = {"Lost Heir", "Big Boss"}
    generic = ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]
    for text in ("Lost Heir 没发过。", "lost  heir 已对上，团队还没发过。", "《Big Boss》之外，Lost Heir 在 DramaBox 还没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=both if "Big Boss" in text else heir) == [], text
    # A bare name nothing returned is still unplaced; a bare title returned is judged by its own record.
    assert check_answer("Big Boss 没发过。", known_titles=known, posted_checked=False, posted_seen=heir) == generic
    assert check_answer("Big Boss 没发过。", known_titles=known, posted_checked=False, posted_seen=both) == ["发布记录显示《Big Boss》发过，不能说没发过。"]
    assert check_answer("Lost Heir 和 Big Boss 都没发过。", known_titles=known, posted_checked=True, posted_seen=both) == [
        "发布记录显示《Big Boss》发过，不能说没发过。"
    ]


def test_a_list_of_titles_before_a_summing_up_claim_is_judged_whole():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2), _item("Lost Heir", matched=True), _item("Other", matched=True)])
    known = {"Big Boss", "Lost Heir", "Other"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    # Titles listed with a comma, a semicolon or a line break, as with 、, are what "都没发过" after the last one sums up.
    for text in (
        "《Big Boss》、《Lost Heir》都没发过。",
        "《Big Boss》，《Lost Heir》都没发过。",
        "《Big Boss》,《Lost Heir》都没发过。",
        "《Big Boss》；《Lost Heir》都没发过。",
        "《Big Boss》\n《Lost Heir》都没发过。",
        "1. 《Big Boss》\n2. 《Lost Heir》都没发过",
        "《Lost Heir》，《Big Boss》和《Other》都没发过。",
        "Big Boss，Lost Heir 都没发过。",
        "《Big Boss》，《Lost Heir》，和《Other》都没发过。",
        "《Big Boss》，以及《Lost Heir》都没发过。",
    ):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    # A clause saying something of its own, a sentence end or a blank line ends the list.
    for text in (
        "《Big Boss》发过，《Lost Heir》都没发过。",
        "《Big Boss》在 B 账号发过，《Lost Heir》和《Other》都没发过。",
        "《Big Boss》。《Lost Heir》都没发过。",
        "《Big Boss》\n\n《Lost Heir》都没发过。",
        "推荐《Big Boss》，《Lost Heir》都没发过。",
    ):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == [], text


def test_every_way_of_leaving_titles_out_sums_up_the_rest():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2), _item("Lost Heir", matched=True)])
    known = {"Big Boss", "Lost Heir"}
    for text in (
        "除《Big Boss》外都没发过。",
        "除《Big Boss》以外，都没发过。",
        "都没发过（《Big Boss》除外）。",
        "Big Boss 以外都没发过。",
        "除 Big Boss 外都没发过。",
        "Big Boss 除外，都没发过。",
        "除了《Big Boss》，都没发过。",
        "《Big Boss》以外，团队都没发过。",
    ):
        for checked in (False, True):
            assert check_answer(text, known_titles=known, posted_checked=checked, posted_seen=seen) == [], (text, checked)
    # The rest still stands on the records returned for it.
    three = with_posted(seen, [_item("No Match", matched=False)])
    for text in ("除了《Big Boss》，都没发过。", "都没发过（《Big Boss》除外）。", "Big Boss 以外都没发过。"):
        notes = check_answer(text, known_titles=known | {"No Match"}, posted_checked=True, posted_seen=three)
        assert notes == ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"], text


def test_leaving_titles_out_still_judges_the_titles_named_before_and_beside():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2), _item("Lost Heir", matched=True), _item("Alpha", matched=True, posts=1)])
    known = {"Big Boss", "Lost Heir", "Alpha"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    # Only the titles left out are left out: titles named earlier, or claimed beside them, are still judged.
    for text in (
        "推荐《Big Boss》。除了《Alpha》，都没发过。",
        "推荐《Big Boss》。除了《Alpha》都没发过。",
        "推荐《Big Boss》。除了《Alpha》，其余都没发过。",
        "推荐《Big Boss》。除《Alpha》外，其他都没发过。",
        "《Big Boss》《Lost Heir》都没发过（《Alpha》除外）。",
        "《Lost Heir》和《Big Boss》都没发过，《Alpha》除外。",
    ):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    # Left out after the claim, or with 不包括, as well as before it.
    for text in ("都没发过，《Big Boss》和《Alpha》除外。", "不包括《Big Boss》和《Alpha》，都没发过。", "除了《Big Boss》和《Alpha》都没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == [], text


def test_the_rest_once_every_record_is_named_stands_on_nothing():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=True)])
    # The second "其余" comes after the only record is named: nothing returned backs it.
    notes = check_answer("其余都没发过。《Lost Heir》。其余都没发过。", known_titles={"Lost Heir"}, posted_checked=False, posted_seen=seen)
    assert notes == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]
    assert check_answer("其余都没发过。", known_titles={"Lost Heir"}, posted_checked=False, posted_seen=seen) == []


def test_not_counting_something_that_is_no_title_leaves_no_title_out():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2), _item("Lost Heir", matched=True)])
    known = {"Big Boss", "Lost Heir"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    for text in (
        "推荐《Big Boss》《Lost Heir》。这两部（不含已保存的）都没发过。",
        "推荐《Big Boss》。不算热度，都没发过。",
        "推荐《Big Boss》。不含已保存的，这些都没发过。",
        "推荐《Big Boss》。不包括已保存的剧，都没发过。",
    ):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    # "除了这部" still leaves out the titles named: the rest is every other card.
    assert check_answer("推荐《Big Boss》。除了这部，其他都没发过。", known_titles=known, posted_checked=True, posted_seen=seen) == []


def test_a_list_before_a_clause_with_its_own_subject_is_not_summed_up():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2), _item("Lost Heir", matched=True), _item("Alpha", matched=True)])
    known = {"Big Boss", "Lost Heir", "Alpha"}
    # Starting otherwise than with its titles, or naming again titles listed before it, a clause picks from the list.
    for text in (
        "1. 《Big Boss》\n2. 《Lost Heir》\n3. 《Alpha》\n其中《Alpha》都没发过。",
        "《Big Boss》，《Lost Heir》，但《Alpha》都没发过。",
        "《Big Boss》\n只有《Alpha》都没发过。",
        "- 《Big Boss》\n- 《Lost Heir》\n- 《Alpha》\n《Alpha》和《Lost Heir》都没发过。",
    ):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == [], text


def test_titles_listed_right_before_those_left_out_are_left_out_too():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2), _item("Beta", matched=True, posts=1), _item("Lost Heir", matched=True)])
    known = {"Big Boss", "Beta", "Lost Heir"}
    for text in ("《Big Boss》，《Beta》除外，都没发过。", "《Big Boss》，《Beta》以外都没发过。", "《Big Boss》，《Beta》以外，都没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == [], text


def test_where_or_by_whom_must_end_before_the_verb():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2, accounts=["B"])])
    known = {"Big Boss"}
    # Another verb, or a noun after 发布, is what the negation is about: no claim.
    for text in (
        "本轮没有在查询里加发布过滤，所以不能说没发过。",
        "《Big Boss》暂无发布状态。",
        "《Big Boss》没有在卡片上显示发布日期。",
        "《Big Boss》没在平台发货。",
        "《Big Boss》没有被授权发行。",
        "暂无发布信息。",
        "尚无发布计划。",
        "没有发布数据。",
        "《Big Boss》暂无发布详情。",
        "《Big Boss》暂无发布结果，请稍后重试。",
        "《Big Boss》没有在 B 账号发表过评论。",
        # A window of time is not "never".
        "《Big Boss》没有在近30天内发布过。",
        # Another verb starting with 发, or a record, news or version of posting.
        "《Big Boss》没有在海外发酵。",
        "《Big Boss》没有在国内发力。",
        "《Big Boss》没有在国内发售。",
        "《Big Boss》没有在B站发布的记录。",
        "《Big Boss》尚无在海外发布的消息。",
        "《Big Boss》没有公开发布的渠道。",
        "《Big Boss》没有正式发布的通知。",
        "《Big Boss》没有被官方发布的消息。",
        "《Big Boss》没有发布过的记录。",
        "《Big Boss》没有在这个端发布新版本。",
    ):
        for checked in (False, True):
            assert check_answer(text, known_titles=known, posted_checked=checked, posted_seen=seen) == [], (text, checked)
    # 被 needs no one after it.
    for text in ("这部剧没有被发到 B 账号。", "《Big Boss》没被发布过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == ["发布记录显示《Big Boss》发过，不能说没发过。"], text


def test_leaving_an_account_out_is_not_leaving_titles_out():
    from ggwork_pick.answer_check import check_answer, with_posted

    # posted_account=A returned Big Boss; account B posted it, and account A posted Other Show.
    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2, accounts=["B"]), _item("Lost Heir", matched=True)], account="A")
    seen = with_posted(seen, [_item("Other Show", matched=True, posts=1, accounts=["A"])])
    known = {"Big Boss", "Lost Heir", "Other Show"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    for text in ("《Big Boss》除了 A 账号都没发过。", "《Big Boss》除了 A 账号以外都没发过。", "除了 A 账号，《Big Boss》都没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    # "A 账号发过的《Other Show》" is the title left out: the rest is every other card.
    for text in ("除了 A 账号发过的《Other Show》其余都没发过。", "除了 A 账号发过的《Other Show》，其余都没发过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text


def test_more_ways_of_saying_not_posted_are_claims():
    from ggwork_pick.answer_check import check_answer, with_posted

    # posted_account=A returned Big Boss; account B posted it.
    seen = with_posted({}, [_item("Big Boss", matched=True, posts=2, accounts=["B"])], account="A")
    known = {"Big Boss"}
    posted = ["发布记录显示《Big Boss》发过，不能说没发过。"]
    for text in (
        "《Big Boss》没在 B 账号发过。",
        "《Big Boss》没有在 YouTube 上发布过。",
        "《Big Boss》没有被团队发布过。",
        "《Big Boss》未被任何账号发过。",
        "《Big Boss》不曾发布过。",
        "《Big Boss》尚无发布。",
        "《Big Boss》暂无发布。",
        "《Big Boss》没有在团队发布过。",
        "《Big Boss》从未在团队发布过。",
        "《Big Boss》并未在公司发布过。",
        "《Big Boss》没在美区发过。",
        "《Big Boss》没在中文站发过。",
        "《Big Boss》没在官网发过。",
        "《Big Boss》没在这个剧场发过。",
        "《Big Boss》没有在任何渠道发布过。",
        "《Big Boss》没有在其他地方发布过。",
        "《Big Boss》没有在海外发布过。",
        "《Big Boss》没有在移动端发布过。",
        "《Big Boss》没有被我们发布过。",
        "《Big Boss》没有被他们发过。",
        "《Big Boss》没被运营发布过。",
        "《Big Boss》没有被官方发布过。",
        "《Big Boss》没有被团队正式发布过。",
        "《Big Boss》没有正式发布过。",
        "《Big Boss》没在抖音发过。",
        "《Big Boss》没有在油管发布过。",
        "《Big Boss》没在美国发过。",
        "《Big Boss》没在欧美发过。",
        "《Big Boss》没在主页发过。",
        "《Big Boss》没在这儿发过。",
        "《Big Boss》还没有发布的剧就是它。",
    ):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == posted, text
    for text in ("《Big Boss》没在 A 账号发过。", "《Big Boss》没有被这个账号发布过。"):
        assert check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen) == [], text
    # Where or by whom never reaches past a clause, a record or a found item.
    for text in ("尚无发布记录。", "暂无发布记录，无法核对是否发过。", "没在清单里发现它。", "未在剧库中发现同名剧。", "没在清单里，发给你看看。"):
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=seen) == [], text
    # Not posted again, or anew, says it was posted before.
    for text in ("《Big Boss》之后没有再发过。", "《Big Boss》没有重新发布过。"):
        assert check_answer(text, known_titles=known, posted_checked=False, posted_seen=seen) == [], text


def _best_of_three(check) -> float:
    import time

    timings = []
    for _ in range(3):
        started = time.perf_counter()
        check()
        timings.append(time.perf_counter() - started)
    return min(timings)


def test_titles_sharing_their_first_words_are_found_in_linear_time():
    from ggwork_pick.answer_check import check_answer, with_posted

    # As many titles as a run returns, all starting with one word: each position used to try every length.
    titles = ["the " + " ".join(["word"] * length) for length in range(1, 161)]
    seen = with_posted({}, [_item(title, matched=True) for title in titles])
    assert _best_of_three(lambda: check_answer("the " * 25_000 + "没发过", known_titles=set(titles), posted_checked=False, posted_seen=seen)) < 0.5
    # The longest title at a position still wins.
    assert check_answer("the word word 没发过。", known_titles=set(titles), posted_checked=False, posted_seen=seen) == []
    two = with_posted(seen, [_item("the word word", matched=True, posts=1)])
    notes = check_answer("the word word 没发过。", known_titles=set(titles), posted_checked=False, posted_seen=two)
    assert notes == ["发布记录显示《the word word》发过，不能说没发过。"]


def test_many_accounts_or_unknown_titles_are_checked_in_linear_time():
    from ggwork_pick.answer_check import check_answer, with_posted

    # Every returned title posted from its own accounts; the answer names each account in its own claim.
    accounts = [f"acc{index}" for index in range(5_000)]
    items = [_item(f"Drama {index}", matched=True, posts=1, accounts=accounts[index * 2 : index * 2 + 2]) for index in range(2_000)]
    seen = with_posted({}, items, account="acc0")
    text = "".join(f"在 {name} 账号都没发过，" for name in accounts)
    known = {item["title"] for item in items}
    assert _best_of_three(lambda: check_answer(text, known_titles=known, posted_checked=True, posted_seen=seen)) < 0.5
    # Thousands of titles left out, or named, and every claim after them borrowing them, each about another account.
    left_out = "除了" + "".join(f"《X{index}》" for index in range(3_000)) + "，都没发过" + "，没发过" * 20_000
    assert _best_of_three(lambda: check_answer(left_out, known_titles=known, posted_checked=True, posted_seen=seen)) < 0.5
    named = "".join(f"《{item['title']}》" for item in items) + "都没发过" + "".join(f"，在 {name} 账号没发过" for name in accounts[:3_000])
    assert _best_of_three(lambda: check_answer(named, known_titles=known, posted_checked=True, posted_seen=seen)) < 0.5
    unknown = "".join(f"《U{index}》" for index in range(50_000))
    assert _best_of_three(lambda: check_answer(unknown, known_titles=set(), posted_checked=True)) < 0.5
    assert check_answer("《U1》《U2》《U1》", known_titles=set(), posted_checked=True) == ["正文提到的《U1》、《U2》不在本轮查询结果中，请以候选卡为准。"]


def _status(entry, mentioned):
    """What a record says to a claim about these accounts: nothing against it when queries cleared them all."""
    from ggwork_pick.answer_check import UNPOSTED, _cleared

    return UNPOSTED if _cleared(entry, mentioned) else entry.status


class _PlainJudge:
    """What answer_check._Judge finds, the plain way: every claim reads every record it stands on."""

    def __init__(self, seen, posted_checked, owns):
        self.seen, self.posted_checked, self.owns = seen, posted_checked, owns
        self.unmatched, self.posted, self.unfiltered = {}, {}, False
        self.above = {}

    def _order(self, index):
        """The titles named up to the clause at index, each where it was first named."""
        from ggwork_pick.answer_check import _norm

        order = {}
        for titles in self.owns[: index + 1]:
            for title in titles:
                order.setdefault(_norm(title), title)
        return list(order.items())

    def _judge(self, entries, unknown):
        from ggwork_pick.answer_check import POSTED, UNKNOWN, UNMATCHED

        for norm, title, status in entries:
            if status == UNMATCHED:
                self.unmatched.setdefault(norm, title)
            elif status == POSTED:
                self.posted.setdefault(norm, title)
        self.unfiltered |= not self.posted_checked and (unknown or any(status == UNKNOWN for _, _, status in entries))

    def _stand(self, entries, unknown):
        if entries:
            self._judge(entries, unknown)
        else:
            self.unfiltered |= not self.posted_checked

    def _records(self, mentioned, skip=frozenset()):
        return [(norm, entry.title, _status(entry, mentioned)) for norm, entry in self.seen.items() if norm not in skip]

    def _named(self, titles, mentioned):
        from ggwork_pick.answer_check import UNKNOWN, Seen, _norm

        return [(_norm(title), title, _status(self.seen.get(_norm(title), Seen("", UNKNOWN)), mentioned)) for title in titles]

    def claim(self, subject, mentioned, index):
        from ggwork_pick.answer_check import ABOVE, EXCEPT, REST

        order = self._order(index)
        if subject.summary == REST and not subject.titles:
            self._stand(self._records(mentioned, frozenset(norm for norm, _ in order)), subject.unknown)
        elif subject.summary == EXCEPT:
            self._stand(self._records(mentioned, subject.excepted), subject.unknown)
        elif subject.summary == ABOVE and order:
            begin = self.above.get((mentioned, subject.unknown), 0)
            if begin < len(order):
                self._stand(self._named([title for _, title in order[begin:]], mentioned), subject.unknown)
                self.above[(mentioned, subject.unknown)] = len(order)
            if len(order) < subject.least:
                self._everything(subject, mentioned)
        elif subject.titles:
            self._judge(self._named(subject.titles, mentioned), subject.unknown)
        else:
            self._everything(subject, mentioned)

    def _everything(self, subject, mentioned):
        from ggwork_pick.answer_check import UNPOSTED

        entries = self._records(mentioned)
        if subject.summary is not None or self.posted_checked:
            self._stand(entries, subject.unknown)
        elif subject.unknown or not entries or any(status != UNPOSTED for _, _, status in entries):
            self.unfiltered = True


def test_the_judge_finds_what_reading_every_record_for_every_claim_finds():
    import random

    from ggwork_pick import answer_check
    from ggwork_pick.answer_check import with_posted

    rng = random.Random(20260930)
    titles = ["Lost Heir", "Big Boss", "No Match", "Other Show", "The CEO", "The CEO Wife"]
    pieces = [
        *(f"《{title}》" for title in titles), "Lost Heir", "the ceo wife", "《Unknown One》", "Zeta Drama", "在 A 账号", "在 B 账号", "C 账号",
        "团队", "其他账户", "这个账号", "A 以外的账号", "都", "以上", "以上3部", "以上全部", "其余", "除了", "以外", "除外", "除", "外",
        "不包括", "没发过", "还没发过", "从未发布", "没在 B 账号发过", "发过", "和", "、", "，", "；", "。", "\n", "\n   ", "\n\n", "1. ",
    ]  # fmt: skip
    for _ in range(3_000):
        seen = {}
        for _ in range(rng.randint(0, 4)):
            items = [
                _item(title, matched=rng.random() > 0.2, posts=rng.choice([0, 0, 1, 2]), accounts=rng.sample(["A", "B", "C"], rng.randint(0, 2)))
                for title in rng.sample(titles, rng.randint(1, 4))
            ]
            seen = with_posted(seen, items, account=rng.choice([None, "A", "B"]))
        text, checked = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 16))), rng.random() < 0.5
        masked = answer_check._TITLE.sub(lambda match: "《" + answer_check._MASK * len(match.group(1)) + "》", text)
        clauses = answer_check._clauses(text, masked, seen)
        judge, plain = answer_check._Judge(seen, checked, clauses.owns), _PlainJudge(seen, checked, clauses.owns)
        for start in answer_check._claim_starts(answer_check._NOT_POSTED, masked):
            index = answer_check.bisect_right(clauses.starts, start) - 1
            judge.claim(clauses.subjects[index], clauses.mentions[index], index)
            plain.claim(clauses.subjects[index], clauses.mentions[index], index)
        # In order: the notes list the titles as the claims came to them.
        found = judge.findings
        ours = (list(found.unmatched.items()), list(found.posted.items()), found.unfiltered)
        assert ours == (list(plain.unmatched.items()), list(plain.posted.items()), plain.unfiltered), (text, seen, checked)


@pytest.mark.parametrize(
    "text",
    [
        "a" * 50_000,
        "a" * 49_997 + "没发过",
        "a " * 24_998 + "没发过",
        "a," * 24_998 + "没发过",
        "lost heir " * 4_999 + "没发过",
        "《" * 50_000,
        "《a》没发过" * 7_142,
        " " * 49_997 + "没发过",
        "\n" + "\t" * 49_996 + "没发过",
        "和 " * 24_998 + "没发过",
        "+" * 49_997 + "没发过",
        "- 、" * 16_665 + "没发过",
    ],
    ids=["one-word", "one-word-claim", "words", "clauses", "bare-titles", "brackets", "titled-claims", "spaces", "tabs", "joins", "pluses", "bullets"],
)
def test_a_long_answer_is_checked_in_linear_time(text):
    import time

    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Lost Heir", matched=True), _item("A", matched=True), _item("Big Boss", matched=True, posts=1, accounts=["B"])], account="C")
    # The best of three runs: linear is under 0.1 s here, quadratic takes seconds, and a busy machine adds noise to one run.
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        check_answer(text, known_titles={"Lost Heir", "A", "Big Boss"}, posted_checked=False, posted_seen=seen)
        timings.append(time.perf_counter() - started)
    assert min(timings) < 0.5


@pytest_asyncio.fixture
async def workbench(tmp_path):
    """A catalog with matched and unmatched records, posts from accounts A and B, and a way to start a run on it."""
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    repo = PickRepository(service.session_factory, "alice")
    items = [
        _item("Lost Heir", matched=True),
        _item("No Match", matched=False),
        _item("Big Boss", matched=True, posts=2, accounts=["B"]),
        _item("Other Show", matched=True, posts=1, accounts=["A"]),
    ]
    rows = [{"source": "s", "source_id": str(index), "language": "en", **item} for index, item in enumerate(items, 1)]
    await Importer(repo, service.data_dir).catalog(json.dumps(rows).encode(), "json")

    async def start_run(run_id, **context):
        store = ExtensionData(run_id)
        await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo(run_id, run_id, "thread", "lead"))
        return store, SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store, **context}, tool_call_id="call")

    yield repo, start_run
    await engine.dispose()


async def _answer_notes(repo, runtime, text, message_id):
    """The notes PickModelGate stores beside a final answer ([] for a clean answer), or None when it stores none."""
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage

    from ggwork_pick.middleware import PickModelGate

    request = ModelRequest(model=SimpleNamespace(), messages=[], runtime=runtime, tools=[])
    await PickModelGate().awrap_model_call(request, AsyncMock(return_value=ModelResponse(result=[AIMessage(content=text, id=message_id)])))
    return {check["message_id"]: check["notes"] for check in await repo.answer_checks("thread")}.get(message_id)


@pytest.mark.asyncio
async def test_a_title_lookup_backs_the_answer_through_the_tools_and_the_model_gate(workbench):
    from ggwork_pick.context import PickTask
    from ggwork_pick.tools import query_candidates_tool

    repo, start_run = workbench
    store, runtime = await start_run("run")
    for call_id, title in (("q1", "Lost Heir"), ("q2", "No Match")):
        runtime.tool_call_id = call_id
        found = json.loads(await query_candidates_tool.coroutine(filters={"query": title, "exclude_selected": False}, runtime=runtime))
        assert [item["title"] for item in found["items"]] == [title]
    assert store.get(PickTask).posted_checked is False
    assert await _answer_notes(repo, runtime, "《Lost Heir》发布记录已对上，帖子数0，团队还没发过。", "m1") == []
    notes = await _answer_notes(repo, runtime, "《No Match》从未发布。", "m2")
    assert notes == ["《No Match》的发布记录没有对上，只能说“发布记录里没有”，不能说没发过。"]


@pytest.mark.asyncio
async def test_a_detail_read_backs_the_answer_about_that_item(workbench):
    from ggwork_pick.selection import SelectionService
    from ggwork_pick.tools import get_drama_detail_tool

    repo, start_run = workbench
    parent = await SelectionService(repo).query({"query": "Lost Heir", "exclude_selected": False}, thread_id="thread", run_id="r0", call_id="c0")
    _, runtime = await start_run("r1", pick_reference={"result_id": parent["id"]})
    assert await _answer_notes(repo, runtime, "目前未发布过。", "m1") == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]
    await get_drama_detail_tool.coroutine(result_id=parent["id"], item_id=parent["items"][0]["item_id"], runtime=runtime)
    assert await _answer_notes(repo, runtime, "目前未发布过。", "m2") == []


@pytest.mark.asyncio
async def test_an_account_query_through_the_tools_backs_only_that_account(workbench):
    from ggwork_pick.tools import query_candidates_tool

    repo, start_run = workbench
    _, runtime = await start_run("run")
    filters = {"posted_account": "A", "query": "Big Boss", "exclude_selected": False}
    found = json.loads(await query_candidates_tool.coroutine(filters=filters, runtime=runtime))
    assert [item["title"] for item in found["items"]] == ["Big Boss"]
    assert await _answer_notes(repo, runtime, "《Big Boss》在 A 账号没发过。", "m1") == []
    assert await _answer_notes(repo, runtime, "《Big Boss》团队没发过。", "m2") == ["发布记录显示《Big Boss》发过，不能说没发过。"]


@pytest.mark.asyncio
async def test_a_clean_answer_is_stored_as_an_empty_check_and_a_tool_call_is_not_checked(workbench):
    """2026-10-05 (evaluation batch 2): only answers with notes were stored, so the card could not tell a clean answer
    from a check that never ran or a notes request that failed. A clean final answer now leaves an empty check."""
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage

    from ggwork_pick.middleware import PickModelGate

    repo, start_run = workbench
    _, runtime = await start_run("run")
    assert await _answer_notes(repo, runtime, "这一轮没有点名任何剧。", "m1") == []
    calling = AIMessage(content="先查一下。", id="m2", tool_calls=[{"name": "pick_query_candidates", "args": {}, "id": "call"}])
    request = ModelRequest(model=SimpleNamespace(), messages=[], runtime=runtime, tools=[])
    await PickModelGate().awrap_model_call(request, AsyncMock(return_value=ModelResponse(result=[calling])))
    assert [check["message_id"] for check in await repo.answer_checks("thread")] == ["m1"]


@pytest.mark.parametrize(
    "text",
    [
        "这5部在团队发布记录里都没有匹配到已发记录，但不能据此断言它们从未发布。",
        "不能因此断定它们没有发布过。",
        "无法据此确认团队从未发过。",
        "不是都没发过，有两部已经发布。",
        "用户问：“它们都没发过吗？”",
        "用户原话：“它们都没发过”。",
        "你问“它们从未发布？”；需要核实。",
    ],
)
def test_readiness_disclaimers_and_quoted_questions_are_not_assertions(text):
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("已发剧", matched=True, posts=1)])
    options = dict(known_titles={"已发剧"}, posted_checked=True, posted_seen=seen)
    assert check_answer(text, **options) == []
    assert check_answer(text + "它们都没发过。", **options) == ["发布记录显示《已发剧》发过，不能说没发过。"]


@pytest.mark.parametrize("title", ["长夜微光", "Oops! Wed, Again", "The CEO’s Wife", "Love-Hate"])
@pytest.mark.parametrize("template", ["{title} 没发过。", "“{title}” 没发过。", "| {title} | 没发过 |", "{title}，还没发过。", "{title}\n  没发过。"])
def test_readiness_known_bare_titles_keep_their_own_evidence(title, template):
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item(title, matched=True), _item("别的已发剧", matched=True, posts=1)])
    options = dict(known_titles={title, "别的已发剧"}, posted_checked=False, posted_seen=seen)
    assert check_answer(template.format(title=title), **options) == []
    assert check_answer(template.format(title="别的已发剧"), **options) == ["发布记录显示《别的已发剧》发过，不能说没发过。"]


def test_readiness_bare_list_and_account_scope():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("长夜微光", matched=True), _item("Love-Hate", matched=True, posts=1, accounts=["B"])], account="A")
    options = dict(known_titles={"长夜微光", "Love-Hate"}, posted_checked=True, posted_seen=seen)
    assert check_answer("长夜微光，Love-Hate 在 A 账号都没发过。", **options) == []
    assert check_answer("长夜微光，Love-Hate 团队都没发过。", **options) == ["发布记录显示《Love-Hate》发过，不能说没发过。"]
    assert check_answer("这里是任意普通中文和标点。", **options) == []


def test_readiness_distinct_identities_do_not_share_account_clearance():
    from ggwork_pick.answer_check import check_answer, with_posted

    first = dict(_item("Twin", matched=True, posts=1, accounts=["B"]), identity="one")
    second = dict(_item("Twin", matched=True, posts=1, accounts=["C"]), identity="two")
    seen = with_posted(with_posted({}, [first], account="A"), [second])
    assert check_answer("Twin 在 A 账号没发过。", known_titles={"Twin"}, posted_checked=True, posted_seen=seen) == ["发布记录显示《Twin》发过，不能说没发过。"]


def test_readiness_identity_clearance_accumulates_only_for_the_same_identity():
    from ggwork_pick.answer_check import check_answer, with_posted

    item = dict(_item("Twin", matched=True, posts=1, accounts=["C"]), identity="one")
    seen = with_posted(with_posted({}, [item], account="A"), [item], account="B")
    options = dict(known_titles={"Twin"}, posted_checked=True)
    assert check_answer("Twin 在 A 账号和 B 账号没发过。", posted_seen=seen, **options) == []
    # Missing identity cannot prove that an account clearance belongs to both records.
    unknown = with_posted(seen, [_item("Twin", matched=True, posts=1, accounts=["C"])])
    assert check_answer("Twin 在 A 账号没发过。", posted_seen=unknown, **options) == ["发布记录显示《Twin》发过，不能说没发过。"]


def test_readiness_quoted_assertion_and_adjacent_real_claim_still_warn():
    from ggwork_pick.answer_check import check_answer

    for text in [
        "我的结论：“它们都没发过”。",
        "用户问：“没发过吗？”我确认它们都没发过。",
        "不能据此断言它们从未发布，但它们都没发过。",
        "不能核实但团队都没发过。",
    ]:
        assert check_answer(text, known_titles=set(), posted_checked=False) == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]


def test_readiness_known_bare_title_punctuation_and_normalization():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("Love-Hate, Again!", matched=True), _item("从未发布的秘密", matched=True)])
    options = dict(known_titles={entry.title for entry in seen.values()}, posted_checked=False, posted_seen=seen)
    assert check_answer("LOVE-HATE,  AGAIN! 没发过。", **options) == []
    assert check_answer("推荐从未发布的秘密。", **options) == []
    # A longer Latin word does not match a known title's leading/trailing fragment.
    from ggwork_pick.answer_check import _known_bare_text

    assert _known_bare_text("xLove-Hate 和 Love-Hatey", {"Love-Hate"}) == "xLove-Hate 和 Love-Hatey"


@pytest.mark.parametrize("kind", ["shared-prefix", "quotes-and-disclaimers"])
def test_readiness_new_scans_scale_at_one_two_four_times(kind):
    from ggwork_pick.answer_check import check_answer, with_posted

    titles = {"长" * length + "夜" for length in range(1, 33)}
    seen = with_posted({}, [_item(title, matched=True) for title in titles])
    unit = "长" * 256 if kind == "shared-prefix" else "“" * 256 + "不能据此断言它们从未发布，但它们都没发过。\n"
    timings = []
    for factor in (1, 2, 4):
        text = unit * (32 * factor)
        timings.append(_best_of_three(lambda: check_answer(text, known_titles=titles, posted_checked=False, posted_seen=seen)))
    print(f"{kind} 1x/2x/4x seconds: {timings}")
    # 4x input with a fixed dictionary must not approach the 16x cost of a quadratic scan.
    # Best-of-three and a small timer allowance tolerate shared CI scheduling noise.
    assert timings[2] <= timings[0] * 6 + 0.02, timings
    assert timings[2] < 3, timings


def test_readiness_quoted_bare_comma_list_retains_all_titles():
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item("长夜微光", matched=True, posts=1), _item("海上明月", matched=True)])
    assert check_answer("“长夜微光”，“海上明月”都没发过。", known_titles={"长夜微光", "海上明月"}, posted_checked=True, posted_seen=seen) == [
        "发布记录显示《长夜微光》发过，不能说没发过。"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "不是推荐而是已经保存到个人清单。",
        "不能只看热度所以我已经帮你保存到清单。",
        "不是我猜的它们都没发过。",
    ],
)
def test_readiness_negation_does_not_hide_a_later_predicate(text):
    from ggwork_pick.answer_check import check_answer

    assert check_answer(text, known_titles=set(), posted_checked=False)


@pytest.mark.parametrize(
    "title,text",
    [
        ("明月", "明月光这部剧没发过。"),
        ("海", "这部剧在海外没发过。"),
    ],
)
def test_readiness_han_substrings_do_not_become_title_evidence(title, text):
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item(title, matched=True), _item("海上花", matched=True, posts=1)])
    assert check_answer(text, known_titles={title, "海上花"}, posted_checked=False, posted_seen=seen) == ["本轮查询没有按发布记录过滤，不能据此断言没发过。"]


@pytest.mark.parametrize("title,text", [("长夜微光", "长夜微光都没发过。"), ("Love-Hate", "Love-Hate没发过。")])
def test_readiness_bare_title_direct_predicate_is_still_recognized(title, text):
    from ggwork_pick.answer_check import check_answer, with_posted

    seen = with_posted({}, [_item(title, matched=True), _item("别的已发剧", matched=True, posts=1)])
    assert check_answer(text, known_titles={title, "别的已发剧"}, posted_checked=False, posted_seen=seen) == []
