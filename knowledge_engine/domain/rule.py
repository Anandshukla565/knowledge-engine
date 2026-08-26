from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SourceRefs(BaseModel):
    source_id: Optional[str] = None
    source_type: str
    source_name: str
    page_or_section: Optional[str] = None
    verified_by: Optional[str] = None
    source_confidence: Optional[str] = None
    source_url: Optional[str] = None
    accessed_at: Optional[str] = None
    expert_verified: bool = False


class ModelAttribute(BaseModel):
    ifc_entity: str
    attributes: List[str]
    description: Optional[str] = None


class GeometryFunction(BaseModel):
    function_name: str
    parameters: List[str]
    returns: str
    description: Optional[str] = None


class WhatLayer(BaseModel):
    content: str
    rationale: Optional[str] = None
    priority: str = "Medium"


class WhenLayer(BaseModel):
    applies_when: List[str] = Field(default_factory=list)
    does_not_apply_when: List[str] = Field(default_factory=list)
    fallback_rule_id: Optional[str] = None


class HowLayer(BaseModel):
    conflicts_with: List[str] = Field(default_factory=list)
    conflict_type: Optional[str] = None
    priority_order: Optional[Any] = None
    resolution_logic: Optional[str] = None
    compromise_explanation: Optional[str] = None
    remedy: Optional[str] = None


class DependencyGraph(BaseModel):
    depends_on: List[str] = Field(default_factory=list)
    referenced_by: List[str] = Field(default_factory=list)


class Examples(BaseModel):
    correct: List[Any] = Field(default_factory=list)
    incorrect: List[Any] = Field(default_factory=list)


class Rule(BaseModel):
    rule_id: str
    status: str = "CANDIDATE"
    domain: str
    subdomain: str
    rule_type: Optional[str] = None
    source_refs: Optional[SourceRefs] = None
    source_status: Optional[str] = None
    confidence_score: float = 0.0
    what_layer: WhatLayer
    when_layer: WhenLayer
    how_layer: HowLayer = Field(default_factory=HowLayer)
    dependency_graph: DependencyGraph = Field(default_factory=DependencyGraph)
    required_model_attributes: Optional[List[ModelAttribute]] = None
    required_geometry_functions: Optional[List[GeometryFunction]] = None
    examples: Examples = Field(default_factory=Examples)
    tags: List[str] = Field(default_factory=list)
    version: int = 1
    human_approval_required: bool = True
    previous_version_path: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_validated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    review: Dict[str, Any] = Field(default_factory=dict)

    def table_content(self) -> str:
        return self.what_layer.content

    def table_rationale(self) -> Optional[str]:
        return self.what_layer.rationale

    def table_priority(self) -> str:
        return self.what_layer.priority
