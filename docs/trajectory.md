# Trajectory construction and A* routing

Trajectory support is a derived view over immutable vehicle records. It does not
change the 128-bit identifier, store previous/next pointers, or rewrite late
events. Every request rebuilds a bounded path window ordered by capture time.

## Pipeline

```text
vehicle records
    ↓ order by (captured_at_ms, id)
consecutive candidate pairs
    ↓ calculate elapsed time + ground/vertical/3D distance
threshold classification
    ↓ accepted directed edges and explained discontinuities
A* over accepted edges
    ↓ minimum observed travel-time route, or disconnected
```

## Distance model

Ground distance uses the Haversine great-circle formula with mean Earth radius
`6,371,008.8 m`:

```text
Δφ = φ₂ - φ₁
Δλ = λ₂ - λ₁
a  = sin²(Δφ/2) + cos(φ₁) cos(φ₂) sin²(Δλ/2)
ground = 2 R asin(min(1, √a))
```

Angles are converted to radians. Vertical distance is the absolute altitude
difference. The segment distance is:

```text
distance_3d = hypot(ground_distance, vertical_distance)
implied_speed = distance_3d / elapsed_seconds
```

This is appropriate for short event-to-event validation. It is not an airspace,
terrain, road, wind, or obstacle model. For long geodesic routes or safety work,
use a higher-fidelity geodesic/navigation service.

## Connection rules

The service examines consecutive points and assigns exactly one result:

| Decision | Condition |
|---|---|
| `non_positive_time` | Elapsed capture time is zero or negative |
| `time_gap` | Elapsed time exceeds `max_gap_ms` |
| `distance_gap` | 3D distance exceeds `max_distance_m` |
| `speed_violation` | Implied speed exceeds `max_speed_mps` |
| `connected` | All checks pass |

Checks appear in that order, so one segment has one deterministic reason even if
it violates multiple limits.

Defaults and hard API bounds:

| Parameter | Default | Allowed |
|---|---:|---:|
| `limit` | 1,000 | 2–1,000 points |
| `max_gap_ms` | 300,000 | 1–86,400,000 ms |
| `max_speed_mps` | 100 | `(0, 1,000]` m/s |
| `max_distance_m` | 50,000 | `(0, 1,000,000]` m |

These are validation policies, not universal physical truths. Configure them for
the vehicle class and sampling interval. A path result should record the limits
that produced it, which the API includes in every response.

## A* route

Each accepted segment becomes a directed edge from the earlier ordered record to
the later record. Edge cost is observed elapsed seconds. A* minimizes total
observed travel time.

The heuristic is:

```text
h(node) = 3D straight-line distance(node, destination) / max_speed_mps
```

Because every accepted edge's implied speed is no greater than the configured
maximum, this is an admissible lower bound under the same distance model. It does
not overestimate the remaining valid travel time.

The current graph links only consecutive events, so each point has at most one
forward edge. A* is therefore linear in this V1 trajectory graph and returns the
same route a forward scan would find. Keeping the route finder explicit provides
a tested boundary for future non-consecutive, road-network, or air-corridor edges
without mixing path semantics into ID generation.

## API

Build the bounded trajectory:

```http
GET /v1/vehicles/{vehicle_id}/trajectory
    ?limit=1000
    &max_gap_ms=300000
    &max_speed_mps=100
    &max_distance_m=50000
    &from_ms=<optional-inclusive-time>
    &to_ms=<optional-inclusive-time>
```

Find a route across accepted segments:

```http
GET /v1/vehicles/{vehicle_id}/route
    ?start_id=<optional-id-in-window>
    &end_id=<optional-id-in-window>
    &limit=1000
    &max_gap_ms=300000
    &max_speed_mps=100
    &max_distance_m=50000
```

When IDs are omitted, route search starts at the first point and targets the last
point in the selected window. Provided IDs must belong to that window. A broken
segment yields `found: false` with reason `disconnected`; absence of points yields
`no_points`. A one-point start/end route succeeds with reason `same_point`.

## Complexity and bounds

For `P` points and accepted edges `E`:

- sorting is `O(P log P)` in the pure builder;
- segment calculation is `O(P)`;
- A* is `O((P + E) log P)` with the heap implementation;
- response memory is `O(P + E)`.

The API caps the window at 1,000 points. `has_more_records` tells the caller that
additional history exists beyond this bounded window. Clients should use capture
filters to analyze another window rather than requesting an unbounded path.

## Late events and reproducibility

A late record can change adjacency in a future trajectory response because it is
inserted according to capture time. Existing IDs and records remain unchanged.
If a trajectory must be auditable as an artifact, persist its query window,
thresholds, creation time, point IDs, and algorithm version in a separate domain
service. V1 returns a derived live view and does not persist that artifact.

## Why paths are not encoded in IDs

Previous/next links would make IDs depend on arrival order and would break when
a late event belongs between two existing records. Paths are mutable business
interpretations; identifiers are immutable allocation results. Keeping them
separate allows multiple policies to analyze the same records safely.
