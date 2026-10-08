"""Explicit plural input; the legacy single batch-reference contract is unchanged."""

from typing import Literal

from pydantic import Field, model_validator

from ggwork_pick.completion_contracts import ResultReference
from ggwork_pick.contracts import StrictInput


class PickReferences(StrictInput):
    version: Literal["pick-references-v1"]
    references: list[ResultReference] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def unique_references(self):
        ids = [reference.result_id for reference in self.references]
        if len(set(ids)) != len(ids) or any(len(set(reference.item_ids)) != len(reference.item_ids) for reference in self.references):
            raise ValueError("候选引用不能重复")
        return self
