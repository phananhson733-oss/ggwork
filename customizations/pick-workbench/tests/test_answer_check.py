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


def test_append_notes_keeps_text_and_adds_one_block():
    from ggwork_pick.answer_check import append_notes

    assert append_notes("正文", []) == "正文"
    out = append_notes("正文", ["a", "b"])
    assert out.startswith("正文\n\n") and out.count("核对提示") == 1
