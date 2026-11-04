import pytest

from spatial_id.errors import ValidationError
from spatial_id.trajectory import astar_route, build_trajectory, haversine_m


def record(value, stamp, lat, lon, altitude=0):
    return {"id":f"{value:032x}","captured_at_ms":stamp,"latitude":lat,"longitude":lon,
            "altitude_m":altitude,"speed_mps":10}


def test_haversine_and_3d_connected_route():
    assert 111_000 < haversine_m(0,0,1,0) < 112_000
    trajectory=build_trajectory([
        record(1,0,0,0,0),record(2,10_000,0,.0005,3),record(3,20_000,0,.001,6)
    ],max_speed_mps=20,max_distance_m=100,max_gap_ms=20_000)
    assert trajectory["summary"]["connected_segment_count"]==2
    assert trajectory["summary"]["break_count"]==0
    route=astar_route(trajectory)
    assert route["found"]
    assert route["ids"]==[f"{n:032x}" for n in (1,2,3)]
    assert route["elapsed_ms"]==20_000
    assert route["distance_3d_m"]>100


@pytest.mark.parametrize("records,reason",[
    ([record(1,0,0,0),record(2,0,0,.001)],"non_positive_time"),
    ([record(1,0,0,0),record(2,20_001,0,.001)],"time_gap"),
    ([record(1,0,0,0),record(2,10_000,0,1)],"distance_gap"),
    ([record(1,0,0,0),record(2,1_000,0,.001)],"speed_violation"),
])
def test_break_reasons(records,reason):
    trajectory=build_trajectory(records,max_gap_ms=20_000,max_distance_m=1000,max_speed_mps=100)
    assert trajectory["segments"][0]["reason"]==reason
    assert not astar_route(trajectory)["found"]


def test_capture_order_limits_and_route_validation():
    trajectory=build_trajectory([record(3,3000,0,.0002),record(1,1000,0,0),record(2,2000,0,.0001)],
                                max_speed_mps=100)
    assert [point["id"] for point in trajectory["points"]]==[f"{n:032x}" for n in (1,2,3)]
    with pytest.raises(ValidationError): astar_route(trajectory,"f"*32,None)
    with pytest.raises(ValidationError): build_trajectory([],limit=1)
