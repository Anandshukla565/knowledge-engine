"""Compiled constraint classifier and planner/solver adapter.

Receives a list of ``CompiledConstraint`` objects (from the constraint compiler
in ``scripts/ke_api/constraints.py``), assigns each one to exactly one
classification bucket, and exposes helper functions that translate each bucket
into a form consumable by the spatial planner and geometry solver.

Classifications
---------------
PLANNER_SUPPORTED
    Constraints the spatial planner can act on during room placement
    (MIN_AREA, ADJACENT_TO, CONTAINED_WITHIN, …).
SOLVER_SUPPORTED
    Constraints the geometry solver checks after placement (MIN_CLEARANCE,
    DOES_NOT_INTERSECT, REQUIRES_WINDOW, …).
VALIDATOR_ONLY
    Constraints that are checked after generation and can block a plan but
    cannot influence placement.
ADVISORY_ONLY
    Soft preferences that inform the prompt but do not block.
SPECIALIST_ONLY
    Constraints requiring human or specialist review.
MAPPING_PENDING
    Constraints whose target element or applicable engine is not yet known.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class ConstraintClassification(str, Enum):
    PLANNER_SUPPORTED = "PLANNER_SUPPORTED"
    SOLVER_SUPPORTED = "SOLVER_SUPPORTED"
    VALIDATOR_ONLY = "VALIDATOR_ONLY"
    ADVISORY_ONLY = "ADVISORY_ONLY"
    SPECIALIST_ONLY = "SPECIALIST_ONLY"
    MAPPING_PENDING = "MAPPING_PENDING"


@dataclass
class ClassifiedConstraint:
    """A single compiled constraint with its classification."""

    constraint_id: str
    rule_id: str
    constraint_type: str
    classification: ConstraintClassification
    target_element_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    severity: str = "major"


@dataclass
class AdapterResult:
    """Complete classification result for a set of compiled constraints."""

    planner_supported: list[ClassifiedConstraint] = field(default_factory=list)
    solver_supported: list[ClassifiedConstraint] = field(default_factory=list)
    validator_only: list[ClassifiedConstraint] = field(default_factory=list)
    advisory_only: list[ClassifiedConstraint] = field(default_factory=list)
    specialist_only: list[ClassifiedConstraint] = field(default_factory=list)
    mapping_pending: list[ClassifiedConstraint] = field(default_factory=list)
    unclassified: list[dict[str, Any]] = field(default_factory=list)

    def all_classified(self) -> list[ClassifiedConstraint]:
        return (
            self.planner_supported
            + self.solver_supported
            + self.validator_only
            + self.advisory_only
            + self.specialist_only
            + self.mapping_pending
        )

    def total_constraints(self) -> int:
        return len(self.all_classified()) + len(self.unclassified)


# ---------------------------------------------------------------------------
# Classification mapping tables
# ---------------------------------------------------------------------------

#: Constraint types that the spatial planner can act on during placement.
PLANNER_CONSTRAINT_TYPES: set[str] = {
    "min_area", "max_area",
    "min_width", "max_width", "min_depth", "max_depth",
    "adjacent_to", "near", "separated_from",
    "contained_within", "preferred_zone",
}

#: Constraint types the geometry solver evaluates after placement.
SOLVER_CONSTRAINT_TYPES: set[str] = {
    "clearance",              # MIN_CLEARANCE
    "does_not_intersect",     # DOES_NOT_INTERSECT (fire safety)
    "requires_exterior_wall", # REQUIRES_EXTERIOR_WALL (door placement)
    "requires_window",        # REQUIRES_WINDOW (ventilation)
}

#: Constraint types that are post-generation validators (can block but not
#: influence placement).
VALIDATOR_CONSTRAINT_TYPES: set[str] = {
    "parking_required",
    "setback_compliance",
    "height_limit",
    "setback_min",
    "accessibility_compliance",
    "parking_count",
}

#: Constraint types that are soft preferences.
ADVISORY_CONSTRAINT_TYPES: set[str] = {
    "orientation", "aspect_ratio", "vista",
    "natural_light", "cross_ventilation",
}

#: Constraint types that need specialist or human review.
SPECIALIST_CONSTRAINT_TYPES: set[str] = {
    "heritage_restriction", "flood_zone", "earthquake_zone",
    "structural_limitation",
    "soil_contamination", "radioactive_zone", "contaminated_land",
}

# Target element types that map to the planner.
PLANNER_TARGET_TYPES: set[str] = {
    "room", "plot", "building", "wing", "floor", "general",
}

# Target element types that map to the solver.
SOLVER_TARGET_TYPES: set[str] = {
    "room", "room_pair", "general",
}


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------

def _infer_target_element_type(constraint: dict[str, Any]) -> str:
    return str(constraint.get("target_element_type", "general")).lower()


def classify_constraint(
    constraint: dict[str, Any],
) -> ClassifiedConstraint:
    """Classify a single compiled constraint into exactly one bucket.

    Priority:
    1. MAPPING_PENDING if no recognizable constraint_type or no target.
    2. Engine-level routing based on constraint_type membership tables.
    3. If ambiguous (type appears in both tables), default to SOLVER_SUPPORTED.
    """
    cid = constraint.get("constraint_id", "UNKNOWN")
    rid = constraint.get("rule_id", cid)
    ctype = str(constraint.get("constraint_type", "")).lower().strip()
    target = _infer_target_element_type(constraint)
    params = constraint.get("parameters", {}) or {}
    severity = str(constraint.get("severity", "major"))

    # No recognizable type → mapping pending
    if not ctype:
        logger.warning("Constraint %s has no constraint_type; marking MAPPING_PENDING.", cid)
        return ClassifiedConstraint(
            constraint_id=cid, rule_id=rid, constraint_type=ctype or "unknown",
            classification=ConstraintClassification.MAPPING_PENDING,
            target_element_type=target, parameters=params, severity=severity,
        )

    # No recognizable target → mapping pending
    if target not in (PLANNER_TARGET_TYPES | SOLVER_TARGET_TYPES | {"plot", "site"}):
        logger.warning(
            "Constraint %s has unrecognized target '%s'; marking MAPPING_PENDING.",
            cid, target,
        )
        return ClassifiedConstraint(
            constraint_id=cid, rule_id=rid, constraint_type=ctype,
            classification=ConstraintClassification.MAPPING_PENDING,
            target_element_type=target, parameters=params, severity=severity,
        )

    in_planner = ctype in PLANNER_CONSTRAINT_TYPES or target in PLANNER_TARGET_TYPES
    in_solver = ctype in SOLVER_CONSTRAINT_TYPES or target in SOLVER_TARGET_TYPES

    if in_planner and not in_solver:
        classification = ConstraintClassification.PLANNER_SUPPORTED
    elif in_solver and not in_planner:
        classification = ConstraintClassification.SOLVER_SUPPORTED
    elif in_planner and in_solver:
        # Ambiguous: prefer solver for geometry-critical constraints
        if ctype in SOLVER_CONSTRAINT_TYPES:
            classification = ConstraintClassification.SOLVER_SUPPORTED
        else:
            classification = ConstraintClassification.PLANNER_SUPPORTED
    elif ctype in VALIDATOR_CONSTRAINT_TYPES:
        classification = ConstraintClassification.VALIDATOR_ONLY
    elif ctype in ADVISORY_CONSTRAINT_TYPES:
        classification = ConstraintClassification.ADVISORY_ONLY
    elif ctype in SPECIALIST_CONSTRAINT_TYPES:
        classification = ConstraintClassification.SPECIALIST_ONLY
    else:
        classification = ConstraintClassification.MAPPING_PENDING

    return ClassifiedConstraint(
        constraint_id=cid, rule_id=rid, constraint_type=ctype,
        classification=classification,
        target_element_type=target, parameters=params, severity=severity,
    )


def classify_constraints(
    compiled_constraints: list[dict[str, Any]],
) -> AdapterResult:
    """Classify all compiled constraints and return an AdapterResult.

    Every constraint receives exactly one classification.  None are silently
    dropped.
    """
    result = AdapterResult()
    for c in compiled_constraints:
        cc = classify_constraint(c)
        if cc.classification == ConstraintClassification.PLANNER_SUPPORTED:
            result.planner_supported.append(cc)
        elif cc.classification == ConstraintClassification.SOLVER_SUPPORTED:
            result.solver_supported.append(cc)
        elif cc.classification == ConstraintClassification.VALIDATOR_ONLY:
            result.validator_only.append(cc)
        elif cc.classification == ConstraintClassification.ADVISORY_ONLY:
            result.advisory_only.append(cc)
        elif cc.classification == ConstraintClassification.SPECIALIST_ONLY:
            result.specialist_only.append(cc)
        elif cc.classification == ConstraintClassification.MAPPING_PENDING:
            result.mapping_pending.append(cc)
        else:
            result.unclassified.append(c)
    _log_summary(result)
    return result


# ---------------------------------------------------------------------------
# Classification report
# ---------------------------------------------------------------------------

def build_classification_report(result: AdapterResult) -> str:
    """Return a human-readable summary of the classification result."""
    lines = [
        f"Constraint classification report ({result.total_constraints()} total):",
        f"  PLANNER_SUPPORTED : {len(result.planner_supported)}",
        f"  SOLVER_SUPPORTED  : {len(result.solver_supported)}",
        f"  VALIDATOR_ONLY    : {len(result.validator_only)}",
        f"  ADVISORY_ONLY     : {len(result.advisory_only)}",
        f"  SPECIALIST_ONLY   : {len(result.specialist_only)}",
        f"  MAPPING_PENDING   : {len(result.mapping_pending)}",
    ]
    if result.unclassified:
        lines.append(f"  UNCLASSIFIED      : {len(result.unclassified)}")
    for bucket_name, bucket in [
        ("PLANNER_SUPPORTED", result.planner_supported),
        ("SOLVER_SUPPORTED", result.solver_supported),
        ("MAPPING_PENDING", result.mapping_pending),
    ]:
        for cc in bucket:
            lines.append(f"    [{bucket_name}] {cc.constraint_id} ({cc.constraint_type})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Translation helpers: planner rules
# ---------------------------------------------------------------------------

def planner_constraints_as_rules(
    planner_constraints: list[ClassifiedConstraint],
) -> list[dict[str, Any]]:
    """Convert PLANNER_SUPPORTED constraints to dicts consumable by the planner.

    Each dict contains the constraint_id, rule_id, constraint_type, and the
    relevant size / adjacency / zone parameters.
    """
    rules: list[dict[str, Any]] = []
    for cc in planner_constraints:
        rule: dict[str, Any] = {
            "constraint_id": cc.constraint_id,
            "rule_id": cc.rule_id,
            "constraint_type": cc.constraint_type,
            "target_element_type": cc.target_element_type,
            "severity": cc.severity,
            "parameters": deepcopy(cc.parameters),
        }
        # Normalise size parameters
        if cc.constraint_type in ("min_area", "max_area"):
            val = cc.parameters.get("minimum_area_sqft",
                                     cc.parameters.get("maximum_area_sqft", 0))
            rule["area_sqft"] = val
            if cc.constraint_type == "min_area":
                rule["min_area_sqft"] = val
            elif cc.constraint_type == "max_area":
                rule["max_area_sqft"] = val
        if cc.constraint_type in ("min_width", "max_width"):
            val = cc.parameters.get("minimum_width_ft",
                                     cc.parameters.get("maximum_width_ft", 0))
            rule["width_ft"] = val
        if cc.constraint_type in ("min_depth", "max_depth"):
            val = cc.parameters.get("minimum_depth_ft",
                                     cc.parameters.get("maximum_depth_ft", 0))
            rule["depth_ft"] = val
        # Normalise adjacency parameters
        if cc.constraint_type == "adjacent_to":
            rule["adjacent_to_room_type"] = cc.parameters.get(
                "adjacent_to_room_type", ""
            ).lower()
        if cc.constraint_type == "near":
            rule["near_room_type"] = cc.parameters.get("near_room_type", "").lower()
            rule["max_distance_ft"] = cc.parameters.get("max_distance_ft", 0)
        if cc.constraint_type == "separated_from":
            rule["separated_from_room_type"] = cc.parameters.get(
                "separated_from_room_type", ""
            ).lower()
            rule["min_distance_ft"] = cc.parameters.get("min_distance_ft", 0)
        if cc.constraint_type == "contained_within":
            rule["boundary_polygon"] = cc.parameters.get("boundary_polygon", [])
        if cc.constraint_type == "preferred_zone":
            rule["zone"] = cc.parameters.get("zone", "")
        rules.append(rule)
    return rules


# ---------------------------------------------------------------------------
# Translation helpers: solver rules
# ---------------------------------------------------------------------------

def solver_constraints_as_rules(
    solver_constraints: list[ClassifiedConstraint],
) -> list[dict[str, Any]]:
    """Convert SOLVER_SUPPORTED constraints to dicts for the geometry solver."""
    rules: list[dict[str, Any]] = []
    for cc in solver_constraints:
        rule: dict[str, Any] = {
            "constraint_id": cc.constraint_id,
            "rule_id": cc.rule_id,
            "constraint_type": cc.constraint_type,
            "target_element_type": cc.target_element_type,
            "severity": cc.severity,
            "parameters": deepcopy(cc.parameters),
        }
        # Normalise clearance parameters
        if cc.constraint_type == "clearance":
            rule["minimum_distance_ft"] = cc.parameters.get(
                "minimum_distance_ft",
                cc.parameters.get("min_clearance_ft", 4.0),
            )
        # Normalise fire-safety / intersection parameters
        if cc.constraint_type == "does_not_intersect":
            rule["room_types"] = [
                t.lower() for t in cc.parameters.get("room_types", [])
            ]
        # Normalise door/window placement parameters
        if cc.constraint_type == "requires_exterior_wall":
            rule["room_types"] = [
                t.lower() for t in cc.parameters.get("room_types", [])
            ]
        if cc.constraint_type == "requires_window":
            rule["room_types"] = [
                t.lower() for t in cc.parameters.get("room_types", [])
            ]
        rules.append(rule)
    return rules


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _log_summary(result: AdapterResult) -> None:
    logger.info(
        "Classified %d constraints: %d planner, %d solver, %d validator, "
        "%d advisory, %d specialist, %d pending, %d unclassified.",
        result.total_constraints(),
        len(result.planner_supported),
        len(result.solver_supported),
        len(result.validator_only),
        len(result.advisory_only),
        len(result.specialist_only),
        len(result.mapping_pending),
        len(result.unclassified),
    )
