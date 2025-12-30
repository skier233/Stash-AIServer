
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

# API Models

class ImageResult(BaseModel):
    result: List[Dict[str, Any]] = Field(..., min_items=1)
    models: List[Any] | None = None
    metrics: Dict[str, Any] | None = None


Scope = Literal["detail", "selected", "page", "all"] 

class AIModelInfo(BaseModel):
    name: str
    identifier: int
    version: float
    categories: List[str]
    type: str


class TagTimeFrame(BaseModel):
    start: float
    end: Optional[float] = None
    confidence: Optional[float] = None
    def __str__(self):
        return f"TagTimeFrame(start={self.start}, end={self.end}, confidence={self.confidence})"


class AIVideoResultV3(BaseModel):
    schema_version: int
    duration: float
    models: List[AIModelInfo]
    frame_interval: float
    # category -> tag -> list of timeframes
    timespans: Dict[str, Dict[str, List[TagTimeFrame]]]

    def to_json(self):
        return self.model_dump_json(exclude_none=True)

class VideoServerResponse(BaseModel):
    result: AIVideoResultV3 | None = None
    metrics: Dict[str, Any] | None = None