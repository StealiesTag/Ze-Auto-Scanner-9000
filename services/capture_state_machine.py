"""Replacement-aware, single-capture state machine independent of camera/DB code."""
from __future__ import annotations

from enum import StrEnum


class CaptureState(StrEnum):
    WAITING_FOR_FIRST_SHEET = "WAITING_FOR_FIRST_SHEET"
    SHEET_DETECTED = "SHEET_DETECTED"
    STABILIZING = "STABILIZING"
    CAPTURING = "CAPTURING"
    QUEUING_IMAGE = "QUEUING_IMAGE"
    LOCKED = "LOCKED"
    WAITING_FOR_BOTTOM_MARKER_DISAPPEARANCE = "WAITING_FOR_BOTTOM_MARKER_DISAPPEARANCE"
    WAITING_FOR_NEW_SHEET = "WAITING_FOR_NEW_SHEET"
    NEW_SHEET_DETECTED = "NEW_SHEET_DETECTED"
    PAUSED = "PAUSED"
    ERROR = "ERROR"


class CaptureStateMachine:
    def __init__(self, *, stable_frames: int = 4, disappearance_frames: int = 4, movement_tolerance_px: float = 3.0, min_page_change_ratio: float = 0.012):
        if stable_frames < 2 or disappearance_frames < 2 or movement_tolerance_px < 0:
            raise ValueError("Capture stability and disappearance thresholds must be positive and require multiple frames.")
        self.stable_frames = stable_frames
        self.disappearance_frames = disappearance_frames
        self.movement_tolerance_px = movement_tolerance_px
        self.min_page_change_ratio = min_page_change_ratio
        self.state = CaptureState.WAITING_FOR_FIRST_SHEET
        self.current_capture_id: str | None = None
        self.completed_capture_count = 0
        self.bottom_missing_count = 0
        self._stable_count = 0
        self._previous_coordinates: dict[str, tuple[float, float]] | None = None
        self._error: str | None = None
        self._stable_signature: list[int] | None = None
        self._captured_signature: list[int] | None = None

    @property
    def error(self) -> str | None:
        return self._error

    def pause(self) -> None:
        if self.state != CaptureState.PAUSED:
            self._state_before_pause = self.state
            self.state = CaptureState.PAUSED

    def resume(self) -> None:
        if self.state != CaptureState.PAUSED:
            return
        previous = getattr(self, "_state_before_pause", CaptureState.WAITING_FOR_FIRST_SHEET)
        if previous == CaptureState.ERROR:
            previous = CaptureState.STABILIZING
        elif previous in {CaptureState.CAPTURING, CaptureState.QUEUING_IMAGE}:
            previous = CaptureState.STABILIZING
        self.state = previous
        self._error = None
        self._stable_count = 0
        self._previous_coordinates = None

    def seed_locked(self, capture_id: str, page_signature: list[int] | None) -> None:
        """Restore the physical-sheet lock after a worker restart or manual capture."""
        self.current_capture_id = capture_id
        self._captured_signature = page_signature
        self.completed_capture_count = max(1, self.completed_capture_count)
        self.state = CaptureState.LOCKED

    def seed_waiting_for_first_sheet(self) -> None:
        self.state = CaptureState.WAITING_FOR_FIRST_SHEET

    def to_dict(self) -> dict:
        """Return JSON-safe state so API observations survive process restarts."""
        return {
            "state": self.state.value,
            "state_before_pause": getattr(self, "_state_before_pause", None).value
            if getattr(self, "_state_before_pause", None) else None,
            "current_capture_id": self.current_capture_id,
            "completed_capture_count": self.completed_capture_count,
            "bottom_missing_count": self.bottom_missing_count,
            "stable_count": self._stable_count,
            "previous_coordinates": self._previous_coordinates,
            "error": self._error,
            "stable_signature": self._stable_signature,
            "captured_signature": self._captured_signature,
        }

    def restore(self, data: dict | None) -> None:
        """Restore dynamic state while retaining the caller's configured thresholds."""
        if not data:
            return
        self.state = CaptureState(data.get("state", CaptureState.WAITING_FOR_FIRST_SHEET.value))
        before_pause = data.get("state_before_pause")
        self._state_before_pause = CaptureState(before_pause) if before_pause else None
        self.current_capture_id = data.get("current_capture_id")
        self.completed_capture_count = int(data.get("completed_capture_count", 0))
        self.bottom_missing_count = int(data.get("bottom_missing_count", 0))
        self._stable_count = int(data.get("stable_count", 0))
        coordinates = data.get("previous_coordinates")
        self._previous_coordinates = {
            key: (float(value[0]), float(value[1])) for key, value in coordinates.items()
        } if coordinates else None
        self._error = data.get("error")
        self._stable_signature = data.get("stable_signature")
        self._captured_signature = data.get("captured_signature")

    def observe(self, detection: dict) -> bool:
        """Consume one detector result. Return True exactly once when capture may begin."""
        if self.state in {CaptureState.PAUSED, CaptureState.ERROR, CaptureState.CAPTURING, CaptureState.QUEUING_IMAGE}:
            return False
        valid = bool(detection.get("valid") and detection.get("markers_visible") and detection.get("geometry_valid") and detection.get("sheet_sufficiently_visible"))
        bottom_visible = bool(detection.get("bottom_markers_visible"))

        if self.state == CaptureState.WAITING_FOR_FIRST_SHEET:
            if valid:
                self.state = CaptureState.SHEET_DETECTED
            return False

        if self.state in {CaptureState.SHEET_DETECTED, CaptureState.NEW_SHEET_DETECTED}:
            if not valid:
                self.state = CaptureState.WAITING_FOR_FIRST_SHEET if not self.completed_capture_count else CaptureState.WAITING_FOR_NEW_SHEET
                return False
            self.state = CaptureState.STABILIZING
            self._stable_count = 1
            self._previous_coordinates = self._coordinates(detection)
            self._stable_signature = detection.get("page_signature")
            return False

        if self.state == CaptureState.STABILIZING:
            if not valid:
                self._reset_stability()
                self.state = CaptureState.WAITING_FOR_FIRST_SHEET if not self.completed_capture_count else CaptureState.WAITING_FOR_NEW_SHEET
                return False
            current = self._coordinates(detection)
            if self._is_stable(current):
                self._stable_count += 1
            else:
                self._stable_count = 1
            self._previous_coordinates = current
            self._stable_signature = detection.get("page_signature")
            if self._stable_count >= self.stable_frames:
                if self.completed_capture_count and self._page_change_ratio(self._stable_signature, self._captured_signature) < self.min_page_change_ratio:
                    self.state = CaptureState.WAITING_FOR_NEW_SHEET
                    self._reset_stability()
                    return False
                self.state = CaptureState.CAPTURING
                return True
            return False

        if self.state == CaptureState.LOCKED:
            if not bottom_visible:
                self.bottom_missing_count = 1
                self.state = CaptureState.WAITING_FOR_BOTTOM_MARKER_DISAPPEARANCE
            return False

        if self.state == CaptureState.WAITING_FOR_BOTTOM_MARKER_DISAPPEARANCE:
            if bottom_visible:
                self.bottom_missing_count = 0
                self.state = CaptureState.LOCKED
            else:
                self.bottom_missing_count += 1
                if self.bottom_missing_count >= self.disappearance_frames:
                    self.state = CaptureState.WAITING_FOR_NEW_SHEET
            return False

        if self.state == CaptureState.WAITING_FOR_NEW_SHEET:
            if valid and bottom_visible:
                self.state = CaptureState.NEW_SHEET_DETECTED
            return False
        return False

    def begin_queueing(self) -> None:
        if self.state != CaptureState.CAPTURING:
            raise RuntimeError("A capture can only be queued after the state machine requests it.")
        self.state = CaptureState.QUEUING_IMAGE

    def capture_and_queue_succeeded(self, capture_id: str, page_signature: list[int] | None = None) -> None:
        if self.state not in {CaptureState.CAPTURING, CaptureState.QUEUING_IMAGE}:
            raise RuntimeError("A successful capture cannot advance the current state.")
        if not capture_id:
            raise ValueError("A queued capture must have a capture identifier.")
        self.current_capture_id = capture_id
        self._captured_signature = page_signature or self._stable_signature
        self.completed_capture_count += 1
        self.bottom_missing_count = 0
        self._reset_stability()
        self.state = CaptureState.LOCKED

    def capture_failed(self, error: str) -> None:
        if self.state not in {CaptureState.CAPTURING, CaptureState.QUEUING_IMAGE}:
            raise RuntimeError("There is no active capture to fail.")
        self._error = error[:1000]
        self.state = CaptureState.ERROR

    def _coordinates(self, detection: dict) -> dict[str, tuple[float, float]]:
        coords = detection.get("marker_coordinates") or {}
        return {key: (float(value[0]), float(value[1])) for key, value in coords.items()}

    def _is_stable(self, current: dict[str, tuple[float, float]]) -> bool:
        if not self._previous_coordinates or set(current) != set(self._previous_coordinates):
            return False
        return all(
            ((current[name][0] - self._previous_coordinates[name][0]) ** 2
             + (current[name][1] - self._previous_coordinates[name][1]) ** 2) ** 0.5 <= self.movement_tolerance_px
            for name in current
        )

    def _reset_stability(self) -> None:
        self._stable_count = 0
        self._previous_coordinates = None

    @staticmethod
    def _page_change_ratio(first: list[int] | None, second: list[int] | None) -> float:
        if not first or not second or len(first) != len(second):
            return 0.0
        return sum(left != right for left, right in zip(first, second)) / len(first)
