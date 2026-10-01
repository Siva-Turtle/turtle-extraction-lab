"""Attributes config module: typed fields shared across agents (many-to-many)."""

from pydantic import BaseModel, Field

ATTRIBUTE_TYPES = ("string", "number", "boolean", "enum", "array", "object")

# Allowed sub-field types for type=="object" (dict properties table in the UI).
OBJECT_SUB_TYPES = ("string", "number", "boolean", "array")


class ObjectProperty(BaseModel):
    name: str
    type: str = "string"
    null_allowed: bool = True


class ArrayItems(BaseModel):
    """Item-shape config for type=="array". kind string|number keeps
    properties []; kind object requires non-empty properties."""

    kind: str = "string"
    properties: list[ObjectProperty] = Field(default_factory=list)


class AttributeCreate(BaseModel):
    agent_ids: list[str] = Field(min_length=1)
    name: str
    type: str = "string"
    description: str = ""
    group: str = ""
    enum_values: list[str] = Field(default_factory=list)
    object_properties: list[ObjectProperty] = Field(default_factory=list)
    array_items: ArrayItems | dict = Field(default_factory=lambda: ArrayItems())


class AttributeUpdate(BaseModel):
    agent_ids: list[str] | None = None
    name: str | None = None
    type: str | None = None
    description: str | None = None
    group: str | None = None
    enum_values: list[str] | None = None
    object_properties: list[ObjectProperty] | None = None
    array_items: ArrayItems | dict | None = None


class AttributeOut(BaseModel):
    id: str
    agent_ids: list[str] = Field(default_factory=list)
    name: str
    type: str = "string"
    description: str = ""
    group: str = ""
    enum_values: list[str] = Field(default_factory=list)
    object_properties: list[ObjectProperty] = Field(default_factory=list)
    array_items: ArrayItems = Field(default_factory=lambda: ArrayItems())
