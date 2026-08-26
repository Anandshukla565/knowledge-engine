from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from knowledge_engine.domain.findings import ProvisionalCandidateNote
from knowledge_engine.domain.floor_plan import FloorPlanSchema

TARGET_RULE_IDS = {
    "kitchen": "RULE_VASTU_CORE_KITCHEN_PLACEMENT_SE_AGNI_STRONG_PREFERENCE_001",
    "master_bedroom": "RULE_VASTU_CORE_MASTER_BEDROOM_SW_PRITHVI_STRONG_PREFERENCE_001",
    "pooja": "RULE_VASTU_CORE_POOJA_ROOM_NE_JALA_OPENNESS_STRONG_PREFERENCE_001",
    "toilet_bathroom": "RULE_VASTU_CORE_TOILET_BATHROOM_NE_BRAHMASTHAN_CONFLICT_REVIEW_001",
    "parking": "RULE_VASTU_CORE_PARKING_ROAD_SIDE_ACCESS_REVIEW_001",
}

FALLBACK_BASIS = {
    "kitchen": {
        "label": "Southeast/Agni kitchen preference",
        "message_mode": "strong_preference",
    },
    "master_bedroom": {
        "label": "Southwest/Prithvi master-bedroom preference",
        "message_mode": "strong_preference",
    },
    "pooja": {
        "label": "Northeast/Jala openness pooja preference",
        "message_mode": "strong_preference",
    },
    "toilet_bathroom": {
        "label": "NE/Brahmasthan wet-zone conflict review routing",
        "message_mode": "review_only",
    },
    "parking": {
        "label": "parking access planning review",
        "message_mode": "practical_review",
    },
}


def _repo_root(workspace_root: Path | None = None) -> Path:
    if workspace_root is not None:
        return workspace_root
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=8)
def _load_graph_backed_candidate_nodes(workspace_root_str: str) -> dict[str, dict[str, Any]]:
    workspace_root = Path(workspace_root_str)
    graph_path = workspace_root / "dependency_graph" / "rule_dependency_graph.json"
    if not graph_path.exists():
        return {}
    graph_payload = json.loads(graph_path.read_text(encoding="utf-8-sig"))
    candidate_nodes: dict[str, dict[str, Any]] = {}
    for node in graph_payload.get("nodes", []):
        if not isinstance(node, dict):
            continue
        rule_id = node.get("rule_id")
        if not rule_id:
            continue
        candidate_nodes[str(rule_id)] = node
    return candidate_nodes


@lru_cache(maxsize=8)
def _load_selected_candidate_metadata(workspace_root_str: str) -> dict[str, dict[str, Any]]:
    workspace_root = Path(workspace_root_str)
    graph_nodes = _load_graph_backed_candidate_nodes(workspace_root_str)
    metadata: dict[str, dict[str, Any]] = {}
    for basis_key, rule_id in TARGET_RULE_IDS.items():
        fallback = {
            "rule_id": rule_id,
            "file": None,
            "what_layer": None,
            "source_status": "provisional_fallback",
            "source_type": "candidate_note_fallback",
            "review_quote": None,
            "tags": ["candidate_only_provisional", "fallback_note_logic"],
            "metadata_mode": "fallback",
        }
        node = graph_nodes.get(rule_id)
        if node is None:
            metadata[basis_key] = fallback
            continue

        candidate_file = None
        file_path = node.get("file")
        if file_path:
            resolved = workspace_root / Path(str(file_path).replace("\\", "/"))
            if resolved.exists():
                candidate_file = resolved

        merged = {
            **fallback,
            "file": str(candidate_file) if candidate_file is not None else file_path,
            "source_status": node.get("source_status") or fallback["source_status"],
            "source_type": node.get("source_type") or fallback["source_type"],
            "tags": node.get("revalidation_tags") or fallback["tags"],
            "metadata_mode": "graph_only" if candidate_file is None else "graph_and_file",
        }

        if candidate_file is None:
            metadata[basis_key] = merged
            continue

        try:
            payload = json.loads(candidate_file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            metadata[basis_key] = merged
            continue

        merged.update(
            {
                "what_layer": (payload.get("what_layer") or {}).get("content"),
                "source_status": payload.get("source_status") or merged["source_status"],
                "source_type": (payload.get("source_refs") or {}).get("source_type") or merged["source_type"],
                "review_quote": (payload.get("review") or {}).get("source_quote"),
                "tags": payload.get("tags") or merged["tags"],
            }
        )
        metadata[basis_key] = merged
    return metadata


def _build_note(
    *,
    note_id: str,
    severity: str,
    message: str,
    room_id: str | None = None,
    room_type: str | None = None,
    zone: str | None = None,
) -> ProvisionalCandidateNote:
    return ProvisionalCandidateNote(
        note_id=note_id,
        severity=severity,
        room_id=room_id,
        room_type=room_type,
        zone=zone,
        message=message,
        candidate_basis="candidate_only_provisional",
        official_scoring_used=False,
        source_verification_status="provisional",
    )


def _basis_context(selected_metadata: dict[str, dict[str, Any]], basis_key: str) -> str:
    info = selected_metadata.get(basis_key) or {}
    mode = info.get("metadata_mode")
    if mode == "graph_and_file":
        return "Active graph-backed candidate metadata"
    if mode == "graph_only":
        return "Graph-linked candidate note fallback metadata"
    return "Deterministic candidate-note fallback logic"


def extract_provisional_candidate_notes(
    plan: FloorPlanSchema,
    geometry_data: dict[str, Any],
    workspace_root: Path | None = None,
) -> list[ProvisionalCandidateNote]:
    root = _repo_root(workspace_root)
    selected_metadata = _load_selected_candidate_metadata(str(root))
    room_zones: dict[str, str] = geometry_data.get("room_zones") or {}
    brahmasthan_observation = geometry_data.get("brahmasthan_observation") or {}
    brahmasthan_room_ids = {
        overlap.get("entity_id")
        for overlap in (brahmasthan_observation.get("overlaps") or [])
        if overlap.get("entity_type") == "room"
    }

    notes: list[ProvisionalCandidateNote] = []

    for room in plan.rooms:
        zone = room_zones.get(room.id)

        if room.type == "kitchen" and zone != "south_east":
            context = _basis_context(selected_metadata, "kitchen")
            notes.append(
                _build_note(
                    note_id=f"candidate_note_kitchen_zone_{room.id}",
                    severity="warning",
                    room_id=room.id,
                    room_type=room.type,
                    zone=zone,
                    message=(
                        f"Kitchen '{room.name}' is tagged to zone '{zone}'. {context} treats Southeast/Agni as a strong-preference reference only; this is not official scoring."
                    ),
                )
            )

        if room.type == "master_bedroom" and zone != "south_west":
            context = _basis_context(selected_metadata, "master_bedroom")
            notes.append(
                _build_note(
                    note_id=f"candidate_note_master_bedroom_zone_{room.id}",
                    severity="warning",
                    room_id=room.id,
                    room_type=room.type,
                    zone=zone,
                    message=(
                        f"Master bedroom '{room.name}' is tagged to zone '{zone}'. {context} treats Southwest/Prithvi as a strong-preference reference only; this is not official scoring."
                    ),
                )
            )

        if room.type == "pooja" and zone != "north_east":
            context = _basis_context(selected_metadata, "pooja")
            notes.append(
                _build_note(
                    note_id=f"candidate_note_pooja_zone_{room.id}",
                    severity="warning",
                    room_id=room.id,
                    room_type=room.type,
                    zone=zone,
                    message=(
                        f"Pooja room '{room.name}' is tagged to zone '{zone}'. {context} treats Northeast/Jala/openness as a preference reference only; this is not official scoring."
                    ),
                )
            )

        if room.type in {"toilet", "bathroom"}:
            overlaps_brahmasthan = room.id in brahmasthan_room_ids
            is_northeast = zone == "north_east"
            if overlaps_brahmasthan or is_northeast:
                context = _basis_context(selected_metadata, "toilet_bathroom")
                if overlaps_brahmasthan and is_northeast:
                    conflict_text = "overlaps the provisional Brahmasthan center zone and is also tagged to the northeast zone"
                elif overlaps_brahmasthan:
                    conflict_text = "overlaps the provisional Brahmasthan center zone"
                else:
                    conflict_text = "is tagged to the northeast zone"
                notes.append(
                    _build_note(
                        note_id=f"candidate_note_wet_zone_review_{room.id}",
                        severity="review",
                        room_id=room.id,
                        room_type=room.type,
                        zone=zone,
                        message=(
                            f"Wet-service room '{room.name}' {conflict_text}. {context} routes NE/Brahmasthan wet-zone conflicts to review only; this is not an official penalty or scoring input."
                        ),
                    )
                )

    if plan.parking:
        context = _basis_context(selected_metadata, "parking")
        notes.append(
            _build_note(
                note_id="candidate_note_parking_practical_presence",
                severity="info",
                message=(
                    f"Parking is present ({len(plan.parking)} bay(s)). {context} currently treats parking guidance as practical planning support only; it is not source-verified Vastu and is not used for official scoring."
                ),
            )
        )

    return notes
