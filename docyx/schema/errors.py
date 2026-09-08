from pydantic import BaseModel


class PageIssue(BaseModel):
    """A structured, page-scoped problem — used for both errors and warnings.

    Errors mean the page could not produce a valid v1 result. Warnings mean it
    did, but something about it is suspect (§18). Both carry a stable ``code``
    so consumers branch on that rather than on message text.
    """

    code: str
    stage: str
    message: str
