from typing import List, Optional
from pydantic import BaseModel


class BoundingBox(BaseModel):
    x: float
    y: float
    width: float
    height: float


class Point(BaseModel):
    x: float
    y: float


Polygon = List[Point]


class Geometry(BaseModel):
    bbox: BoundingBox
    polygon: Optional[Polygon] = None
    rotation: Optional[float] = None
