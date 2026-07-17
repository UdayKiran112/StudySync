"""Shared configuration for API request models."""

from pydantic import BaseModel, ConfigDict


class RequestModel(BaseModel):
    """Reject misspelled request fields and normalize surrounding whitespace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
