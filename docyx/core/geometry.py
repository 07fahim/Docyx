from typing import List, Optional
from pydantic import BaseModel


class BoundingBox(BaseModel):
    x: float
    y: float
    width: float
    height: float

    @property
    def x1(self) -> float:
        return self.x + self.width

    @property
    def y1(self) -> float:
        return self.y + self.height

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x1 and self.y <= py <= self.y1


class Point(BaseModel):
    x: float
    y: float


Polygon = List[Point]


class Geometry(BaseModel):
    bbox: BoundingBox
    polygon: Optional[Polygon] = None
    rotation: Optional[float] = None
