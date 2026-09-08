from docyx.core.geometry import BoundingBox, Point, Geometry, Polygon


def test_geometry_creation_bbox_only():
    bbox = BoundingBox(x=10.0, y=20.0, width=100.0, height=50.0)
    geom = Geometry(bbox=bbox)
    assert geom.bbox.x == 10.0
    assert geom.bbox.y == 20.0
    assert geom.bbox.width == 100.0
    assert geom.bbox.height == 50.0
    assert geom.polygon is None
    assert geom.rotation is None


def test_geometry_creation_full():
    bbox = BoundingBox(x=0.0, y=0.0, width=50.0, height=50.0)
    points: Polygon = [Point(x=0.0, y=0.0), Point(x=50.0, y=0.0), Point(x=50.0, y=50.0)]
    geom = Geometry(bbox=bbox, polygon=points, rotation=45.0)
    assert geom.bbox == bbox
    assert geom.polygon == points
    assert geom.rotation == 45.0
