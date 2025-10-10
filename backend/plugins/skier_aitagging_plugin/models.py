
from typing import Any, Dict, List, Literal
import pydantic

# API Models

class ImageResult(pydantic.BaseModel):
    result: List[Dict[str, Any]] = pydantic.Field(..., min_items=1)


Scope = Literal["detail", "selected", "page", "all"] 