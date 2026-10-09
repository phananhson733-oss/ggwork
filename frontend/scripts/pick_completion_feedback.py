"""Publish synthetic post observations through the production private repository."""

import json
from pathlib import Path


async def publish_review_fixture(
    database_url: str, output: Path, owner: str, row: dict
):
    from engines import host_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from feedback.fakes import operating_rows, snapshot_from_rows
    from ggwork_pick.feedback.repository import FeedbackRepository

    source = operating_rows()
    drama = source["dramas"][0]
    drama["平台"] = [row["theater"]]
    drama["语言"] = [row["language"]]
    drama["选剧台剧集ID"] = row["identity"]
    drama["选剧台对应状态"] = "已确认"
    for index, post in enumerate(source["posts"]):
        post["视频链接"] = f"https://www.youtube.com/watch?v=post-{index}"
    snapshot = snapshot_from_rows(source, transform_version="feedback-v2")
    url = (
        make_url(database_url)
        .set(drivername="postgresql+asyncpg")
        .render_as_string(hide_password=False)
    )
    engine = host_engine(url)
    try:
        repo = FeedbackRepository(
            async_sessionmaker(engine, expire_on_commit=False), owner
        )
        run = await repo.claim("manual")
        assert run is not None
        version = await repo.publish(run["id"], snapshot)
        (output / "feedback-source.synthetic.json").write_text(
            json.dumps(
                {"origin": "synthetic_fixture", "source": source, "version": version},
                ensure_ascii=False,
                default=str,
                indent=2,
            ),
            encoding="utf-8",
        )
        return version
    finally:
        await engine.dispose()
