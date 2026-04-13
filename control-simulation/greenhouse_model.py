"""
greenhouse_model.py
--------------------
Physics-based thermal model of a water-heated greenhouse.

State variable  : T_air  [°C]  — greenhouse air temperature
Derived output  : T_outlet [°C] — water outlet temperature

Energy balance on the greenhouse air volume:

    m_air · cp_air · dT_air/dt  =  Q_water  −  Q_envelope  −  Q_fan

Where
    Q_water    = heat transferred from the water loop to the air     [W]
    Q_envelope = conductive/convective loss through walls & roof      [W]
    Q_fan      = sensible heat exhausted by the fan                  [W]

Heat-exchanger model (NTU-effectiveness):
    NTU = UA_hx / C_water,   ε = 1 − exp(−NTU)
    Q_water  = ε · C_water · max(T_inlet − T_air, 0)
    T_outlet = T_inlet − Q_water / C_water

Envelope loss / gain:
    Q_envelope = UA_envelope · (T_air − T_ambient)

Fan exhaust (warm air replaced by ambient):
    Q_fan = fan_signal · fan_max_flow_m3s · ρ_air · cp_air · max(T_air − T_ambient, 0)

Default parameters (30 m³ greenhouse):
    At steady state with T_air = 20 °C, T_inlet = 50 °C, T_ambient = −5 °C
    → steady-state fan ≈ 42 %   (good control authority in both directions)
    → open-loop τ (fan = 0)     ≈ 109 s
    → closed-loop τ (fan = 42%) ≈  89 s
"""

import numpy as np
from dataclasses import dataclass, field


# ── Physical constants ────────────────────────────────────────────────────────
RHO_WATER = 997.0     # kg/m³
CP_WATER  = 4182.0    # J/(kg·K)
RHO_AIR   = 1.225     # kg/m³
CP_AIR    = 1006.0    # J/(kg·K)


@dataclass
class GreenhouseParams:
    """
    Fixed parameters of the greenhouse system.
    Defaults: 30 m³ polycarbonate greenhouse, small hot-water boiler.
    """
    # Geometry
    volume_m3: float        = 30.0    # internal air volume            [m³]

    # Water loop
    flow_rate_kgs: float    = 0.05    # water mass flow rate           [kg/s]
    UA_hx: float            = 500.0   # heat-exchanger overall UA      [W/K]

    # Envelope
    UA_envelope: float      = 150.0   # envelope heat-loss coefficient [W/K]

    # Fan
    fan_max_flow_m3s: float = 0.15    # volumetric flow at signal = 1  [m³/s]

    # Boundary conditions (may be changed dynamically)
    T_ambient: float        = -5.0    # outdoor air temperature        [°C]
    T_inlet: float          = 50.0    # water inlet temperature        [°C]

    # Sensor noise (std dev); set to 0 to disable
    noise_std_air: float    = 0.05    # air temperature sensor noise   [°C σ]
    noise_std_water: float  = 0.03    # water temperature sensor noise [°C σ]

    # ── Derived quantities (auto-computed) ────────────────────────────────────
    mass_air_kg: float      = field(init=False)
    C_water: float          = field(init=False)   # flow heat-capacity rate [W/K]
    C_fan_air: float        = field(init=False)   # fan air heat-cap rate   [W/K]
    NTU: float              = field(init=False)
    effectiveness: float    = field(init=False)

    def __post_init__(self):
        self.mass_air_kg  = self.volume_m3 * RHO_AIR
        self.C_water      = self.flow_rate_kgs * CP_WATER
        self.C_fan_air    = self.fan_max_flow_m3s * RHO_AIR * CP_AIR
        self.NTU          = self.UA_hx / self.C_water
        self.effectiveness = 1.0 - np.exp(-self.NTU)

    def steady_state_fan(self, T_air: float) -> float:
        """
        Compute the fan signal required to hold T_air at thermal equilibrium.
        Returns a value in [0, 1] (clamped), or NaN if infeasible.
        """
        dT = T_air - self.T_ambient
        if dT <= 0:
            return float("nan")
        q_w   = self.effectiveness * self.C_water * max(self.T_inlet - T_air, 0.0)
        q_env = self.UA_envelope * dT
        fan   = (q_w - q_env) / (self.C_fan_air * dT)
        return float(np.clip(fan, 0.0, 1.0))

    def no_fan_equilibrium(self) -> float:
        """Steady-state air temperature when the fan is fully off."""
        # ε·C_w·(T_in − T_eq) = UA_env·(T_eq − T_amb)
        a = self.effectiveness * self.C_water
        b = self.UA_envelope
        return (a * self.T_inlet + b * self.T_ambient) / (a + b)


class GreenhouseModel:
    """
    Continuous-time thermal model of the greenhouse.

    Example
    -------
        model = GreenhouseModel(GreenhouseParams(), T_air_init=15.0)
        for _ in range(steps):
            fan = ctrl.update(model.T_inlet_measured,
                              model.T_outlet_measured,
                              model.T_air_measured)
            model.step(fan, dt=0.5)
    """

    def __init__(self, params: GreenhouseParams, T_air_init: float = 15.0):
        self.p        = params
        self.T_air    = float(T_air_init)
        self.T_outlet = float(params.T_inlet)
        self.time     = 0.0

        # Last heat fluxes — exposed for plotting / diagnostics
        self.Q_water    = 0.0
        self.Q_envelope = 0.0
        self.Q_fan      = 0.0

        self._update_outlet()

    # ── Public API ────────────────────────────────────────────────────────────

    def step(self, fan_signal: float, dt: float) -> None:
        """
        Advance the model by dt seconds using 4th-order Runge-Kutta.

        Parameters
        ----------
        fan_signal : float  Normalised fan speed  0 (off) … 1 (full speed).
        dt         : float  Integration step  [s].
        """
        fan_signal = float(np.clip(fan_signal, 0.0, 1.0))
        k1 = self._dTair_dt(self.T_air,              fan_signal)
        k2 = self._dTair_dt(self.T_air + 0.5*dt*k1, fan_signal)
        k3 = self._dTair_dt(self.T_air + 0.5*dt*k2, fan_signal)
        k4 = self._dTair_dt(self.T_air +     dt*k3,  fan_signal)
        self.T_air += (dt / 6.0) * (k1 + 2*k2 + 2*k3 + k4)
        self.time  += dt
        self._update_outlet()

    # Sensor measurements (with additive Gaussian noise)
    @property
    def T_air_measured(self) -> float:
        return self.T_air + np.random.normal(0.0, self.p.noise_std_air)

    @property
    def T_outlet_measured(self) -> float:
        return self.T_outlet + np.random.normal(0.0, self.p.noise_std_water)

    @property
    def T_inlet_measured(self) -> float:
        return self.p.T_inlet + np.random.normal(0.0, self.p.noise_std_water)

    # Dynamic boundary-condition setters
    def set_T_inlet(self, value: float) -> None:
        self.p.T_inlet = float(value)
        self.p.__post_init__()

    def set_T_ambient(self, value: float) -> None:
        self.p.T_ambient = float(value)
        self.p.__post_init__()

    # ── Internal ODE ─────────────────────────────────────────────────────────

    def _dTair_dt(self, T_air: float, fan_signal: float) -> float:
        p      = self.p
        dT_amb = T_air - p.T_ambient

        Q_water    = p.effectiveness * p.C_water   * max(p.T_inlet - T_air, 0.0)
        Q_envelope = p.UA_envelope                 * dT_amb
        Q_fan      = fan_signal * p.C_fan_air      * max(dT_amb, 0.0)

        # Cache for diagnostics (uses current T_air, not RK sub-steps)
        self.Q_water    = Q_water
        self.Q_envelope = Q_envelope
        self.Q_fan      = Q_fan

        return (Q_water - Q_envelope - Q_fan) / (p.mass_air_kg * CP_AIR)

    def _update_outlet(self) -> None:
        p             = self.p
        Q_w           = p.effectiveness * p.C_water * max(p.T_inlet - self.T_air, 0.0)
        self.T_outlet = p.T_inlet - Q_w / p.C_water
