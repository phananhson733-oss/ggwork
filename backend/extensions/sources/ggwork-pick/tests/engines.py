"""Engines that serialize JSON the way the host's engine does (deerflow.persistence.engine).

In production the extension writes through the host's engine, whose serializer keeps non-ASCII text verbatim
(ensure_ascii=False). SQLAlchemy's default escapes it, which also turns a lone surrogate into a harmless
"\\ud800" escape: a test engine with the default stores text that production cannot. The conftest guard fails
any statement sent through an engine that serializes differently.
"""

from deerflow.persistence.engine import _json_serializer as HOST_JSON_SERIALIZER
from sqlalchemy.ext.asyncio import create_async_engine


def host_engine(url: str, **kwargs):
    return create_async_engine(url, json_serializer=HOST_JSON_SERIALIZER, **kwargs)
