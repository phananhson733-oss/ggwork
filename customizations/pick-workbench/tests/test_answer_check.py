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
