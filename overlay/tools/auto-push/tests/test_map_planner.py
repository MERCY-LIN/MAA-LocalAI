from pathlib import Path

from autopush.map_planner import StageMap


REPOSITORY = Path(__file__).resolve().parents[3]
TL8_MAP = (
    REPOSITORY
    / "resource"
    / "Arknights-Tile-Pos"
    / "act55side_08-activities-act55side-level_act55side_08.json"
)


def test_tl8_route_analysis():
    stage_map = StageMap.load(TL8_MAP)

    assert stage_map.stage_code == "TL-8"
    assert stage_map.stage_id == "act55side_08"
    assert len(stage_map.starts) >= 1
    assert len(stage_map.ends) >= 1
    assert len(stage_map.route_tiles) >= 20
    assert stage_map.choke_points
    assert stage_map.candidates

    location_types = {candidate.location_type for candidate in stage_map.candidates}
    assert "melee" in location_types
    assert "ranged" in location_types
    for location_type in ("melee", "ranged"):
        scores = [
            candidate.score
            for candidate in stage_map.candidates
            if candidate.location_type == location_type
        ]
        assert scores == sorted(scores, reverse=True)
