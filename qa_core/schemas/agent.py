"""Strict public request and finite read-tool arguments."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

EntityID = Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_.:-]+$')]
Query = Annotated[str, Field(min_length=1, max_length=500)]
ToolName = Literal['get_alarm_context', 'get_device_context', 'search_evidence', 'read_evidence']

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)

class AgentRequest(StrictModel):
    query: Query
    alarm_id: EntityID | None = None
    device_id: EntityID | None = None

class AlarmArgs(StrictModel):
    alarm_id: EntityID

class DeviceArgs(StrictModel):
    device_id: EntityID

class SearchArgs(StrictModel):
    query: Query
    top_n: Annotated[int, Field(ge=1, le=3)] = 3

class ReadArgs(StrictModel):
    evidence_id: Annotated[str, Field(min_length=1, max_length=2048)]
