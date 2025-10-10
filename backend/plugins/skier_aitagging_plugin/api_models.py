
from typing import Any, Dict, List
import pydantic


class ImageResult(pydantic.BaseModel):
    result: List[Dict[str, Any]] = pydantic.Field(..., min_items=1)