"""Shared rules for all response contracts."""

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    # Do not turn strings into numbers and hide a breaking API change.
    # Keep unknown fields so the diff can report additions later.
    model_config = ConfigDict(strict=True, extra="allow", allow_inf_nan=False)


class PaginatedResponse(ContractModel):
    total: int = Field(ge=0)
    skip: int = Field(ge=0)
    limit: int = Field(ge=0)
