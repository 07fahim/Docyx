from pydantic import BaseModel


class GateError(BaseModel):
    """Structured error record attached to a page when a pipeline stage fails."""

    code: str
    stage: str
    message: str