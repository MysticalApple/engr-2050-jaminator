# 🌱 Greenhouse Fan Control — Simulation & Dashboard

A physics-based closed-loop simulation of an exhaust-fan controller for a
water-heated greenhouse, with a live animated 5-panel dashboard.

---

## Files

| File | Role |
|---|---|
| `greenhouse_model.py` | Physics-based thermal model (ODE, RK4) |
| `greenhouse_controller.py` | PID + feedforward fan controller |
| `main.py` | Simulation loop + live matplotlib dashboard |

---

## Run

```bash
pip install numpy matplotlib
python main.py
```

The animated window opens immediately and plays the 800-second scenario
in ~32 real seconds (~25 simulation-fps).

---

## Physics (greenhouse_model.py)

Energy balance on the greenhouse air volume:

```
m_air · cp_air · dT_air/dt = Q_water − Q_envelope − Q_fan
```

| Term | Model |
|---|---|
| `Q_water` | NTU-effectiveness heat-exchanger model |
| `Q_envelope` | UA-loss through walls/roof |
| `Q_fan` | Sensible heat exhausted; fan replaces warm air with ambient |

**Heat-exchanger:**
```
NTU = UA_hx / C_water,   ε = 1 − exp(−NTU)
Q_water  = ε · C_water · (T_inlet − T_air)
T_outlet = T_inlet − Q_water / C_water
```

**Default parameters (30 m³ greenhouse):**

| Parameter | Value | Meaning |
|---|---|---|
| Volume | 30 m³ | Compact research greenhouse |
| Flow rate | 0.05 kg/s | Small hot-water boiler |
| UA_hx | 500 W/K | Fin-and-tube heat exchanger |
| UA_envelope | 150 W/K | Polycarbonate cladding |
| Fan max flow | 0.15 m³/s | Single exhaust fan |
| T_ambient | −5 °C | Cold winter day |
| T_inlet | 50 °C | Boiler outlet |

**Design-point check** (SP = 20 °C, T_inlet = 50 °C, T_ambient = −5 °C):
- Steady-state fan ≈ **42 %** → good authority in both directions
- Open-loop τ ≈ **109 s** | Closed-loop τ ≈ **89 s**

---

## Controller (greenhouse_controller.py)

**PID + feedforward:**

```
fan = Kp·e + Ki·∫e·dt + Kd·ė_filtered + Kff·ΔT_water
```

where `e = T_air − setpoint` and `ΔT_water = T_inlet − T_outlet`.

| Gain | Default | Purpose |
|---|---|---|
| Kp | 0.08 | Proportional response to error |
| Ki | 0.003 | Eliminate steady-state offset |
| Kd | 2.0 s | Dampen overshoot |
| Kff | 0.015 | Proactive response to heat load |

Features:
- **Derivative filter** (τ = 5 s) — attenuates sensor noise amplification
- **Anti-windup** — conditional integration clamping at output saturation
- **Rate limiter** — max 0.08 fan-units/s prevents actuator shock

---

## Scenario (main.py)

| Time | Disturbance | What it shows |
|---|---|---|
| t = 0 → 180 s | Cold start, warm-up | Tracking from 15 °C to SP = 20 °C; fan activates at setpoint |
| t = 180 s | **SP: 20 → 24 °C** | Setpoint step; fan ramps down, system warms naturally |
| t = 380 s | **T_inlet: 50 → 38 °C** | Heat supply drop; controller loses authority, T_air falls |
| t = 520 s | **T_inlet: 38 → 50 °C** | Supply restored; system recovers |
| t = 680 s | **SP: 24 → 20 °C** | Downward step; fan at ~68 % to exhaust excess heat |

---

## Dashboard panels

```
┌─────────────────────────┬─────────────────────────┐
│  Air temperature        │  Fan signal  0–1         │
│  (measured + true + SP) │  + live % readout        │
├─────────────────────────┼─────────────────────────┤
│  Water temps            │  PID term breakdown      │
│  T_inlet, T_outlet      │  P / I / D / FF          │
├─────────────────────────┴─────────────────────────┤
│  Heat fluxes: Q_water / Q_envelope / Q_fan  [W]   │
└────────────────────────────────────────────────────┘
```

Gold dotted vertical lines mark each disturbance event.
