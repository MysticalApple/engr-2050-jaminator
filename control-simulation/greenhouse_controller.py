"""
greenhouse_controller.py
------------------------
Exhaust-fan speed controller for the water-heated greenhouse.

Strategy
--------
Two complementary signals are combined:

1. **PID feedback** on the air-temperature error
       e(t) = T_air − T_setpoint
   This is the primary regulation loop.  The error is *positive* when the
   greenhouse is too warm → the fan runs harder to exhaust heat.

2. **Thermal-differential feedforward** from the water loop
       ΔT_water = T_inlet − T_outlet
   A large ΔT means the water circuit is delivering a large heat load;
   the fan should respond proactively before the air temperature rises.

Output
------
    fan_signal ∈ [0, 1]   (clamped; with optional rate limiting)

Anti-windup
-----------
Conditional integration: the integrator is frozen whenever the output is
saturated AND fresh error would deepen the saturation (standard clamping).

Derivative filter
-----------------
A first-order low-pass filter with time constant tau_filter suppresses
high-frequency noise amplification in the D path.

Usage
-----
    from greenhouse_controller import GreenhouseController, ControllerParams
    ctrl = GreenhouseController(ControllerParams(setpoint=20.0), dt=0.5)
    fan  = ctrl.update(T_inlet, T_outlet, T_air)   # → float in [0, 1]
"""

from dataclasses import dataclass
import numpy as np


@dataclass
class ControllerParams:
    """All tunable parameters for the greenhouse fan controller."""

    setpoint: float    = 20.0     # target air temperature [°C]

    # PID gains
    Kp: float          = 0.08     # proportional gain
    Ki: float          = 0.003    # integral gain        [1/s]
    Kd: float          = 2.0      # derivative gain      [s]

    # Feedforward: each °C of water-loop ΔT adds Kff to fan demand
    Kff: float         = 0.015

    # Output limits
    fan_min: float     = 0.0
    fan_max: float     = 1.0

    # Derivative filter time constant [s] — attenuates sensor noise in D path
    tau_filter: float  = 5.0

    # Rate limiter: maximum change in fan_signal per second (0 = disabled)
    rate_limit: float  = 0.08


class GreenhouseController:
    """
    PID + feedforward fan controller for greenhouse temperature regulation.

    Parameters
    ----------
    params : ControllerParams
        All tunable gains and limits.
    dt : float
        Control-loop period [s].  Must match the model step used in main.py.
    """

    def __init__(self, params: ControllerParams = None, dt: float = 0.5):
        self.p  = params or ControllerParams()
        self.dt = dt

        # Controller state
        self._integral    = 0.0
        self._prev_error  = 0.0
        self._deriv_filt  = 0.0
        self._prev_fan    = 0.0

        # Last individual term values — exposed for diagnostics / plotting
        self.term_P  = 0.0
        self.term_I  = 0.0
        self.term_D  = 0.0
        self.term_FF = 0.0
        self.raw_out = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def update(self,
               T_inlet:  float,
               T_outlet: float,
               T_air:    float) -> float:
        """
        Compute the fan signal from the three temperature measurements.

        Parameters
        ----------
        T_inlet  : float   Water inlet temperature       [°C]
        T_outlet : float   Water outlet temperature      [°C]
        T_air    : float   Greenhouse air temperature    [°C]

        Returns
        -------
        fan_signal : float ∈ [0, 1]
        """
        p, dt = self.p, self.dt

        # Error: positive → too warm → increase fan
        error = T_air - p.setpoint

        # Filtered derivative (low-pass)
        alpha            = dt / (p.tau_filter + dt)
        raw_deriv        = (error - self._prev_error) / dt
        self._deriv_filt = (1.0 - alpha)*self._deriv_filt + alpha*raw_deriv

        # PID terms
        P = p.Kp * error
        I = p.Ki * self._integral
        D = p.Kd * self._deriv_filt

        # Feedforward: proxy for instantaneous heat delivery rate
        delta_T_water = max(T_inlet - T_outlet, 0.0)
        FF = p.Kff * delta_T_water

        # Unclamped output
        raw = P + I + D + FF
        self.raw_out = raw

        # Clamp to [fan_min, fan_max]
        fan = float(np.clip(raw, p.fan_min, p.fan_max))

        # Anti-windup: freeze integrator when saturated
        sat_hi = (raw >= p.fan_max) and (error > 0)
        sat_lo = (raw <= p.fan_min) and (error < 0)
        if not (sat_hi or sat_lo):
            self._integral += error * dt

        # Rate limiter
        if p.rate_limit > 0:
            delta_max = p.rate_limit * dt
            fan = float(np.clip(fan,
                                self._prev_fan - delta_max,
                                self._prev_fan + delta_max))

        # Update state
        self._prev_error = error
        self._prev_fan   = fan

        # Cache terms for plotting
        self.term_P  = P
        self.term_I  = I
        self.term_D  = D
        self.term_FF = FF

        return fan

    def set_setpoint(self, setpoint: float) -> None:
        """Change the target temperature and reset the integrator."""
        self.p.setpoint = float(setpoint)
        self._integral  = 0.0

    def reset(self) -> None:
        """Full controller reset (call after mode changes or faults)."""
        self._integral   = 0.0
        self._prev_error = 0.0
        self._deriv_filt = 0.0
        self._prev_fan   = 0.0

    @property
    def setpoint(self) -> float:
        return self.p.setpoint

    def diagnostics(self) -> dict:
        """Return a snapshot dict of the last controller state."""
        return {
            "P":   self.term_P,
            "I":   self.term_I,
            "D":   self.term_D,
            "FF":  self.term_FF,
            "raw": self.raw_out,
        }
