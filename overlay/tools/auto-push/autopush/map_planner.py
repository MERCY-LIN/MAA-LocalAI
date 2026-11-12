"""Deterministic route and deployment analysis from MAA tile data."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .metrics import RoundMetrics

ROAD_KEYS = {"tile_road", "tile_start", "tile_end"}
MELEE_BUILDABLE = {1, 3}
RANGED_BUILDABLE = {2, 3}


@dataclass(frozen=True)
class DeploymentCandidate:
    """A rule-derived candidate tile for an opening deployment."""

    row: int
    column: int
    location_type: str
    tile_key: str
    direction: str
    score: float
    route_coverage: int
    choke_coverage: int
    spawn_distance: int | None


@dataclass
class StageMap:
    """Parsed MAA map and deterministic route analysis."""

    stage_id: str
    stage_code: str
    name: str
    width: int
    height: int
    tiles: list[list[dict[str, Any]]]
    starts: list[tuple[int, int]]
    ends: list[tuple[int, int]]
    route_tiles: list[tuple[int, int]]
    choke_points: list[tuple[int, int]]
    candidates: list[DeploymentCandidate]

    @classmethod
    def load(cls, path: str | Path, metrics: RoundMetrics | None = None) -> StageMap:
        if metrics is not None:
            metrics.record_tool_call()
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls.from_dict(payload, metrics=metrics)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        metrics: RoundMetrics | None = None,
    ) -> StageMap:
        return analyze_stage_map(payload, metrics=metrics)


def _road_neighbors(
    row: int,
    column: int,
    tiles: list[list[dict[str, Any]]],
) -> list[tuple[int, int]]:
    height = len(tiles)
    width = len(tiles[0]) if height else 0
    result: list[tuple[int, int]] = []
    for delta_row, delta_column in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        next_row = row + delta_row
        next_column = column + delta_column
        if not (0 <= next_row < height and 0 <= next_column < width):
            continue
        if tiles[next_row][next_column].get("tileKey") in ROAD_KEYS:
            result.append((next_row, next_column))
    return result


def _multi_source_distances(
    starts: Iterable[tuple[int, int]],
    tiles: list[list[dict[str, Any]]],
) -> dict[tuple[int, int], int]:
    queue = deque((position, 0) for position in starts)
    distances: dict[tuple[int, int], int] = {}
    while queue:
        position, distance = queue.popleft()
        if position in distances:
            continue
        distances[position] = distance
        for neighbor in _road_neighbors(position[0], position[1], tiles):
            if neighbor not in distances:
                queue.append((neighbor, distance + 1))
    return distances


def _road_directions(
    row: int,
    column: int,
    tiles: list[list[dict[str, Any]]],
) -> set[tuple[int, int]]:
    return {
        (next_row - row, next_column - column)
        for next_row, next_column in _road_neighbors(row, column, tiles)
    }


def _is_route_control_point(
    position: tuple[int, int],
    tiles: list[list[dict[str, Any]]],
) -> bool:
    row, column = position
    directions = _road_directions(row, column, tiles)
    if len(directions) != 2:
        return True
    first, second = tuple(directions)
    is_straight = first[0] == -second[0] and first[1] == -second[1]
    if not is_straight:
        return True

    adjacent_buildable = 0
    for delta_row, delta_column in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        next_row = row + delta_row
        next_column = column + delta_column
        if not (0 <= next_row < len(tiles) and 0 <= next_column < len(tiles[0])):
            continue
        if int(tiles[next_row][next_column].get("buildableType", 0)) != 0:
            adjacent_buildable += 1
    return adjacent_buildable <= 1


def _direction_towards(row: int, column: int, target: tuple[int, int]) -> str:
    delta_row = target[0] - row
    delta_column = target[1] - column
    if abs(delta_column) >= abs(delta_row):
        return "Right" if delta_column > 0 else "Left"
    return "Down" if delta_row > 0 else "Up"


def _tiles_in_range(
    row: int,
    column: int,
    radius: int,
    positions: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for target_row, target_column in positions:
        if abs(target_row - row) + abs(target_column - column) <= radius:
            result.append((target_row, target_column))
    return result


def _interleave_candidates(
    candidates: list[DeploymentCandidate],
) -> list[DeploymentCandidate]:
    melee = [item for item in candidates if item.location_type == "melee"]
    ranged = [item for item in candidates if item.location_type == "ranged"]
    mixed: list[DeploymentCandidate] = []
    for index in range(max(len(melee), len(ranged))):
        if index < len(melee):
            mixed.append(melee[index])
        if index < len(ranged):
            mixed.append(ranged[index])
    return mixed


def analyze_stage_map(
    payload: dict[str, Any],
    metrics: RoundMetrics | None = None,
) -> StageMap:
    """Analyze tiles without inspecting game frames or calling a model."""

    if metrics is not None:
        metrics.record_tool_call()
    tiles = payload.get("tiles")
    if not isinstance(tiles, list) or not tiles or not isinstance(tiles[0], list):
        raise ValueError("tile map does not contain a rectangular tiles array")

    height = int(payload.get("height", len(tiles)))
    width = int(payload.get("width", len(tiles[0])))
    if len(tiles) != height or any(len(row) != width for row in tiles):
        raise ValueError("tile map dimensions do not match tile array")

    starts: list[tuple[int, int]] = []
    ends: list[tuple[int, int]] = []
    route_tiles: list[tuple[int, int]] = []
    for row, tile_row in enumerate(tiles):
        for column, tile in enumerate(tile_row):
            key = str(tile.get("tileKey", ""))
            if key in ROAD_KEYS:
                route_tiles.append((row, column))
            if tile.get("isStart") is True:
                starts.append((row, column))
            if tile.get("isEnd") is True:
                ends.append((row, column))

    if not starts:
        raise ValueError("tile map has no start tiles")
    if not ends:
        raise ValueError("tile map has no end tiles")
    if not route_tiles:
        raise ValueError("tile map has no route tiles")

    spawn_distances = _multi_source_distances(starts, tiles)
    end_distances = _multi_source_distances(ends, tiles)
    choke_points = [
        position
        for position in route_tiles
        if position not in starts
        and position not in ends
        and _is_route_control_point(position, tiles)
    ]
    choke_set = set(choke_points)
    route_set = set(route_tiles)
    candidates: list[DeploymentCandidate] = []

    for row, tile_row in enumerate(tiles):
        for column, tile in enumerate(tile_row):
            buildable = int(tile.get("buildableType", 0))
            if buildable not in MELEE_BUILDABLE | RANGED_BUILDABLE:
                continue

            row_candidates: list[tuple[str, int]] = []
            if buildable in MELEE_BUILDABLE:
                row_candidates.append(("melee", 1))
            if buildable in RANGED_BUILDABLE:
                row_candidates.append(("ranged", 3))

            for location_type, radius in row_candidates:
                covered = _tiles_in_range(row, column, radius, route_set)
                if not covered:
                    continue
                direction_targets = [
                    position
                    for position in covered
                    if position != (row, column)
                ]
                nearest_route = min(
                    direction_targets or covered,
                    key=lambda position: (
                        abs(position[0] - row) + abs(position[1] - column),
                        end_distances.get(position, 10_000),
                    ),
                )
                choke_coverage = sum(position in choke_set for position in covered)
                reachable_spawn_distances = [
                    spawn_distances[position]
                    for position in covered
                    if position in spawn_distances
                ]
                spawn_distance = (
                    min(reachable_spawn_distances)
                    if reachable_spawn_distances
                    else None
                )
                if (row, column) in route_set:
                    road_neighbors = _road_neighbors(row, column, tiles)
                    if road_neighbors:
                        nearest_route = min(
                            road_neighbors,
                            key=lambda position: end_distances.get(
                                position, 10_000
                            ),
                        )
                spawn_score = 0.0 if spawn_distance is None else 1.0 / (1 + spawn_distance)
                score = (
                    len(covered)
                    + choke_coverage * 2.5
                    + spawn_score
                    + (0.75 if location_type == "melee" and choke_coverage else 0.0)
                )
                candidates.append(
                    DeploymentCandidate(
                        row=row,
                        column=column,
                        location_type=location_type,
                        tile_key=str(tile.get("tileKey", "")),
                        direction=_direction_towards(row, column, nearest_route),
                        score=round(score, 3),
                        route_coverage=len(covered),
                        choke_coverage=choke_coverage,
                        spawn_distance=spawn_distance,
                    )
                )

    candidates.sort(
        key=lambda item: (
            -item.score,
            item.location_type,
            item.row,
            item.column,
        )
    )
    candidates = _interleave_candidates(candidates)
    if metrics is not None:
        metrics.add_note(
            f"map={payload.get('stageId', '')} route_tiles={len(route_tiles)} "
            f"chokes={len(choke_points)} candidates={len(candidates)}"
        )

    return StageMap(
        stage_id=str(payload.get("stageId", "")),
        stage_code=str(payload.get("code", "")),
        name=str(payload.get("name", "")),
        width=width,
        height=height,
        tiles=tiles,
        starts=starts,
        ends=ends,
        route_tiles=route_tiles,
        choke_points=choke_points,
        candidates=candidates,
    )
