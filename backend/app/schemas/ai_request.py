from datetime import datetime
from pydantic import BaseModel

class AIRequestBase(BaseModel):
    prompt: str

class AIRequestCreate(AIRequestBase):
    pass

class AIRequestRead(AIRequestBase):
    id: int
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
