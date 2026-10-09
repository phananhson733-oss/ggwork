"""Test-only model. A real host run executes real tools, with no provider transport."""

import json
import os
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk


class CompletionScriptedModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "qa-pick-completion-scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        raise AssertionError("QA accepts async scripted calls only")

    async def _astream(self, messages, **kwargs):
        if os.environ.get("PICK_COMPLETION_QA") != "loopback-synthetic-only":
            raise AssertionError("QA model requires isolated harness")
        # Only this turn's tool evidence may drive the deterministic answer.
        start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        replies = [m for m in messages[start:] if isinstance(m, ToolMessage)]
        with Path(os.environ["PICK_COMPLETION_LEDGER"]).open(
            "a", encoding="utf-8"
        ) as log:
            log.write(
                json.dumps({"model": self._llm_type, "tool_replies": len(replies)})
                + "\n"
            )
        if (
            isinstance(messages[-1], HumanMessage)
            and isinstance(messages[-1].content, str)
            and messages[-1].content.startswith("请仅从以下已核对事实")
        ):
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=messages[-1].content.split("\n", 1)[1])
            )
        elif not replies:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "id": "qa-candidates",
                            "name": "pick_query_candidates",
                            "args": {
                                "filters": {"limit": 3, "exclude_selected": False}
                            },
                        }
                    ],
                )
            )
        else:
            result = json.loads(replies[-1].content)
            assert len(result["items"]) == 3
            lines = [
                f"《{item['title']}》的上架日期为{item['listed_at']} [result:{result['id']}:{item['item_id']}]"
                for item in result["items"]
            ]
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="。\n".join(lines))
            )
