"""Trajectory construction and A* routing over immutable spatial event records."""
from __future__ import annotations

from collections import defaultdict
import heapq
import math

from .errors import ValidationError

EARTH_RADIUS_M = 6_371_008.8


def _positive_number(name: str, value: object, maximum: float) -> float:
    if type(value) not in (int, float):
        raise ValidationError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= maximum:
        raise ValidationError(f"{name} must be greater than 0 and at most {maximum:g}")
    return number


def trajectory_limits(*, limit=1000, max_gap_ms=300_000,
                      max_speed_mps=100.0, max_distance_m=50_000.0):
    if type(limit) is not int or not 2 <= limit <= 1000:
        raise ValidationError("limit must be between 2 and 1000")
    if type(max_gap_ms) is not int or not 1 <= max_gap_ms <= 86_400_000:
        raise ValidationError("max_gap_ms must be between 1 and 86400000")
    return {
        "limit": limit,
        "max_gap_ms": max_gap_ms,
        "max_speed_mps": _positive_number("max_speed_mps", max_speed_mps, 1_000),
        "max_distance_m": _positive_number("max_distance_m", max_distance_m, 1_000_000),
    }


def haversine_m(latitude_a: float, longitude_a: float,
                latitude_b: float, longitude_b: float) -> float:
    """Great-circle ground distance on a mean-radius sphere."""
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    term = math.sin(delta_lat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(term)))


def distance_3d_m(a: dict, b: dict) -> tuple[float, float, float]:
    ground = haversine_m(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
    vertical = abs(float(b["altitude_m"]) - float(a["altitude_m"]))
    return ground, vertical, math.hypot(ground, vertical)


def build_trajectory(records: list[dict], *, limit=1000, max_gap_ms=300_000,
                     max_speed_mps=100.0, max_distance_m=50_000.0) -> dict:
    """Connect consecutive capture-time records and explain every rejected edge."""
    config = trajectory_limits(limit=limit, max_gap_ms=max_gap_ms,
                               max_speed_mps=max_speed_mps, max_distance_m=max_distance_m)
    ordered = sorted(records, key=lambda record: (record["captured_at_ms"], record["id"]))[: config["limit"]]
    points = [
        {
            "id": record["id"],
            "captured_at_ms": record["captured_at_ms"],
            "latitude": record["latitude"],
            "longitude": record["longitude"],
            "altitude_m": record["altitude_m"],
            "speed_mps": record["speed_mps"],
        }
        for record in ordered
    ]
    segments = []
    connected_distance = 0.0
    for source, target in zip(points, points[1:]):
        elapsed_ms = target["captured_at_ms"] - source["captured_at_ms"]
        ground, vertical, distance = distance_3d_m(source, target)
        implied_speed = distance / (elapsed_ms / 1000) if elapsed_ms > 0 else None
        if elapsed_ms <= 0:
            reason = "non_positive_time"
        elif elapsed_ms > config["max_gap_ms"]:
            reason = "time_gap"
        elif distance > config["max_distance_m"]:
            reason = "distance_gap"
        elif implied_speed is not None and implied_speed > config["max_speed_mps"]:
            reason = "speed_violation"
        else:
            reason = "connected"
            connected_distance += distance
        segments.append({
            "from_id": source["id"],
            "to_id": target["id"],
            "from_captured_at_ms": source["captured_at_ms"],
            "to_captured_at_ms": target["captured_at_ms"],
            "elapsed_ms": elapsed_ms,
            "ground_distance_m": round(ground, 3),
            "vertical_distance_m": round(vertical, 3),
            "distance_3d_m": round(distance, 3),
            "implied_speed_mps": round(implied_speed, 3) if implied_speed is not None else None,
            "connected": reason == "connected",
            "reason": reason,
        })
    first_time = points[0]["captured_at_ms"] if points else None
    last_time = points[-1]["captured_at_ms"] if points else None
    return {
        "points": points,
        "segments": segments,
        "summary": {
            "point_count": len(points),
            "segment_count": len(segments),
            "connected_segment_count": sum(segment["connected"] for segment in segments),
            "break_count": sum(not segment["connected"] for segment in segments),
            "connected_distance_m": round(connected_distance, 3),
            "capture_duration_ms": last_time - first_time if first_time is not None else 0,
            "truncated": len(records) > config["limit"],
        },
        "limits": config,
    }


def astar_route(trajectory: dict, start_id: str | None = None,
                end_id: str | None = None) -> dict:
    """Find the minimum observed travel-time path across accepted directed edges."""
    points = trajectory["points"]
    if not points:
        return {"found": False, "reason": "no_points", "ids": [], "segments": [],
                "distance_3d_m": 0.0, "elapsed_ms": 0, "visited_nodes": 0}
    by_id = {point["id"]: point for point in points}
    start_id = start_id or points[0]["id"]
    end_id = end_id or points[-1]["id"]
    if start_id not in by_id or end_id not in by_id:
        raise ValidationError("start_id and end_id must belong to this trajectory window")
    if start_id == end_id:
        return {"found": True, "reason": "same_point", "ids": [start_id], "segments": [],
                "distance_3d_m": 0.0, "elapsed_ms": 0, "visited_nodes": 1}

    graph: dict[str, list[dict]] = defaultdict(list)
    for segment in trajectory["segments"]:
        if segment["connected"]:
            graph[segment["from_id"]].append(segment)
    max_speed = trajectory["limits"]["max_speed_mps"]

    def heuristic(node_id: str) -> float:
        return distance_3d_m(by_id[node_id], by_id[end_id])[2] / max_speed

    frontier = [(heuristic(start_id), 0.0, start_id)]
    best = {start_id: 0.0}
    previous: dict[str, tuple[str, dict]] = {}
    visited = 0
    while frontier:
        _, cost, node = heapq.heappop(frontier)
        if cost != best.get(node):
            continue
        visited += 1
        if node == end_id:
            break
        for segment in graph.get(node, []):
            target = segment["to_id"]
            next_cost = cost + segment["elapsed_ms"] / 1000
            if next_cost < best.get(target, math.inf):
                best[target] = next_cost
                previous[target] = (node, segment)
                heapq.heappush(frontier, (next_cost + heuristic(target), next_cost, target))

    if end_id not in best:
        return {"found": False, "reason": "disconnected", "ids": [], "segments": [],
                "distance_3d_m": 0.0, "elapsed_ms": 0, "visited_nodes": visited}
    route_segments = []
    node = end_id
    while node != start_id:
        prior, segment = previous[node]
        route_segments.append(segment)
        node = prior
    route_segments.reverse()
    ids = [start_id] + [segment["to_id"] for segment in route_segments]
    return {
        "found": True,
        "reason": "connected",
        "ids": ids,
        "segments": route_segments,
        "distance_3d_m": round(sum(segment["distance_3d_m"] for segment in route_segments), 3),
        "elapsed_ms": sum(segment["elapsed_ms"] for segment in route_segments),
        "visited_nodes": visited,
    }
