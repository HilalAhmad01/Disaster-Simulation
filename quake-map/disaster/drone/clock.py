"""The drone's simulation clock. There is only one demo clock in the app (live.py's `hub.speed`, wall seconds x speed), so the drone
follows it instead of keeping a second one: sim time advances by wall time x the demo speed, and pauses when asked."""


class SimClock:
    def __init__(self, speed_fn=lambda: 1.0, max_step_s: float = 60.0):
        self.t = 0.0
        self.paused = False
        self._speed_fn = speed_fn
        self.max_step_s = max_step_s            # a stalled event loop must not make the drone jump minutes in one tick
        self.cap: float | None = None           # e.g. 2.0 while a pilot flies manually

    @property
    def speed(self) -> float:
        s = float(self._speed_fn())
        return min(s, self.cap) if self.cap else s

    def advance(self, wall_dt: float) -> float:
        """Advance by a wall-clock step and return the simulated seconds that passed."""
        if self.paused or wall_dt <= 0:
            return 0.0
        dt = min(wall_dt * self.speed, self.max_step_s)
        self.t += dt
        return dt

    def reset(self) -> None:
        self.t = 0.0
        self.cap = None
