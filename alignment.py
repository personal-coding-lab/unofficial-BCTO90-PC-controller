"""Multi-star alt-az alignment and compact astronomy transforms."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass


def wrap_degrees(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def local_sidereal_degrees(timestamp: float, longitude_deg: float) -> float:
    jd = timestamp / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    gmst = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * t * t
        - t * t * t / 38710000.0
    )
    return (gmst + longitude_deg) % 360.0


def equatorial_to_horizontal(
    ra_hours: float,
    dec_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    timestamp: float | None = None,
) -> tuple[float, float]:
    timestamp = time.time() if timestamp is None else timestamp
    lat = math.radians(latitude_deg)
    dec = math.radians(dec_deg)
    hour_angle_deg = wrap_degrees(
        local_sidereal_degrees(timestamp, longitude_deg) - ra_hours * 15.0
    )
    hour_angle = math.radians(hour_angle_deg)
    sin_alt = math.sin(dec) * math.sin(lat) + math.cos(dec) * math.cos(lat) * math.cos(
        hour_angle
    )
    alt = math.asin(max(-1.0, min(1.0, sin_alt)))
    az = math.atan2(
        math.sin(hour_angle),
        math.cos(hour_angle) * math.sin(lat) - math.tan(dec) * math.cos(lat),
    )
    return ((math.degrees(az) + 180.0) % 360.0, math.degrees(alt))


def horizontal_to_equatorial(
    az_deg: float,
    alt_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    timestamp: float | None = None,
) -> tuple[float, float]:
    timestamp = time.time() if timestamp is None else timestamp
    az = math.radians(az_deg)
    alt = math.radians(alt_deg)
    lat = math.radians(latitude_deg)
    sin_dec = math.sin(alt) * math.sin(lat) + math.cos(alt) * math.cos(lat) * math.cos(az)
    dec = math.asin(max(-1.0, min(1.0, sin_dec)))
    hour_angle = math.atan2(
        -math.sin(az) * math.cos(alt),
        math.sin(alt) * math.cos(lat) - math.cos(alt) * math.sin(lat) * math.cos(az),
    )
    ra_deg = (local_sidereal_degrees(timestamp, longitude_deg) - math.degrees(hour_angle)) % 360.0
    return (ra_deg / 15.0, math.degrees(dec))


@dataclass(frozen=True)
class AlignmentPoint:
    az_deg: float
    alt_deg: float
    az_count: float
    alt_count: float


def _solve_3x3(matrix: list[list[float]], values: list[float]) -> tuple[float, float, float]:
    augmented = [row[:] + [value] for row, value in zip(matrix, values)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-9:
            raise ValueError("별 배치가 한쪽에 몰려 있습니다. 다른 방향의 별을 추가하세요.")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                left - factor * right
                for left, right in zip(augmented[row], augmented[column])
            ]
    return tuple(augmented[row][3] for row in range(3))


def _least_squares(features: list[tuple[float, float, float]], values: list[float]):
    normal = [[0.0] * 3 for _ in range(3)]
    target = [0.0] * 3
    for feature, value in zip(features, values):
        for row in range(3):
            target[row] += feature[row] * value
            for column in range(3):
                normal[row][column] += feature[row] * feature[column]
    return _solve_3x3(normal, target)


class MultiStarAlignment:
    """Two-point basic model; 3-8 points add tilt/cross-axis compensation."""

    MAX_POINTS = 8

    def __init__(self) -> None:
        self.points: list[AlignmentPoint] = []
        self.az_coefficients: tuple[float, float, float] | None = None
        self.alt_coefficients: tuple[float, float, float] | None = None
        self.rms_error_deg: float | None = None

    @property
    def ready(self) -> bool:
        return self.az_coefficients is not None and self.alt_coefficients is not None

    @property
    def az_counts_per_degree(self) -> float | None:
        return None if self.az_coefficients is None else self.az_coefficients[1]

    @property
    def alt_counts_per_degree(self) -> float | None:
        return None if self.alt_coefficients is None else self.alt_coefficients[2]

    def reset(self) -> None:
        self.points.clear()
        self._clear_model()

    def _clear_model(self) -> None:
        self.az_coefficients = None
        self.alt_coefficients = None
        self.rms_error_deg = None

    def add(self, point: AlignmentPoint) -> int:
        if len(self.points) >= self.MAX_POINTS:
            raise ValueError("얼라인 기준점은 최대 8개입니다. 초기화 후 다시 등록하세요.")
        for existing in self.points:
            separation = math.hypot(
                wrap_degrees(point.az_deg - existing.az_deg),
                point.alt_deg - existing.alt_deg,
            )
            if separation < 3.0:
                raise ValueError("기존 기준점과 너무 가깝습니다. 3° 이상 떨어진 별을 선택하세요.")
        previous = (self.az_coefficients, self.alt_coefficients, self.rms_error_deg)
        self.points.append(point)
        try:
            self._fit()
            if len(self.points) >= 3 and self.rms_error_deg > 3.0:
                raise ValueError(
                    f"새 기준점 적용 오차가 너무 큽니다(RMS {self.rms_error_deg:.2f}°). "
                    "별 중앙 정렬과 위치 입력을 확인하세요."
                )
        except ValueError:
            self.points.pop()
            self.az_coefficients, self.alt_coefficients, self.rms_error_deg = previous
            raise
        return len(self.points)

    def remove_last(self) -> int:
        if self.points:
            self.points.pop()
        self._fit()
        return len(self.points)

    def _fit(self) -> None:
        self._clear_model()
        if len(self.points) < 2:
            return
        origin_az = self.points[0].az_deg
        if len(self.points) == 2:
            first, second = self.points
            delta_az = wrap_degrees(second.az_deg - first.az_deg)
            delta_alt = second.alt_deg - first.alt_deg
            if abs(delta_az) < 5.0 or abs(delta_alt) < 5.0:
                self.points.pop()
                raise ValueError("두 별은 방위각과 고도가 각각 5° 이상 떨어져야 합니다.")
            az_scale = (second.az_count - first.az_count) / delta_az
            alt_scale = (second.alt_count - first.alt_count) / delta_alt
            if abs(az_scale) < 1.0 or abs(alt_scale) < 1.0:
                raise ValueError("축 위치 변화가 너무 작습니다. 두 번째 별을 다시 정렬하세요.")
            self.az_coefficients = (first.az_count, az_scale, 0.0)
            self.alt_coefficients = (
                first.alt_count - alt_scale * first.alt_deg,
                0.0,
                alt_scale,
            )
            self.rms_error_deg = 0.0
            return
        features = [
            (1.0, wrap_degrees(point.az_deg - origin_az), point.alt_deg)
            for point in self.points
        ]
        self.az_coefficients = _least_squares(features, [p.az_count for p in self.points])
        self.alt_coefficients = _least_squares(features, [p.alt_count for p in self.points])
        if abs(self._determinant()) < 1e-6:
            raise ValueError("축 보정 행렬이 불안정합니다. 더 넓게 떨어진 별을 선택하세요.")
        errors = []
        for point in self.points:
            predicted_az, predicted_alt = self.horizontal_for_counts(
                point.az_count, point.alt_count
            )
            errors.append(
                wrap_degrees(predicted_az - point.az_deg) ** 2
                + (predicted_alt - point.alt_deg) ** 2
            )
        self.rms_error_deg = math.sqrt(sum(errors) / len(errors))

    def _determinant(self) -> float:
        if not self.ready:
            return 0.0
        return (
            self.az_coefficients[1] * self.alt_coefficients[2]
            - self.az_coefficients[2] * self.alt_coefficients[1]
        )

    def counts_for(self, az_deg: float, alt_deg: float) -> tuple[float, float]:
        if not self.ready:
            raise RuntimeError("최소 2점 얼라인이 완료되지 않았습니다.")
        feature = (1.0, wrap_degrees(az_deg - self.points[0].az_deg), alt_deg)
        return (
            sum(value * coefficient for value, coefficient in zip(feature, self.az_coefficients)),
            sum(value * coefficient for value, coefficient in zip(feature, self.alt_coefficients)),
        )

    def horizontal_for_counts(self, az_count: float, alt_count: float) -> tuple[float, float]:
        if not self.ready:
            raise RuntimeError("최소 2점 얼라인이 완료되지 않았습니다.")
        determinant = self._determinant()
        az_rhs = az_count - self.az_coefficients[0]
        alt_rhs = alt_count - self.alt_coefficients[0]
        x = (
            az_rhs * self.alt_coefficients[2]
            - self.az_coefficients[2] * alt_rhs
        ) / determinant
        altitude = (
            self.az_coefficients[1] * alt_rhs
            - az_rhs * self.alt_coefficients[1]
        ) / determinant
        return ((self.points[0].az_deg + x) % 360.0, altitude)


TwoStarAlignment = MultiStarAlignment
