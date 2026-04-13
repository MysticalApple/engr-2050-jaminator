"""
main.py
-------
Connects the greenhouse thermal model with the fan controller and displays a
live animated dashboard using matplotlib FuncAnimation.

Five-panel layout:
┌──────────────────────────┬──────────────────────────┐
│ [1] Air temperature      │ [2] Fan signal            │
│     vs setpoint          │     0–1                   │
├──────────────────────────┼──────────────────────────┤
│ [3] Water temps          │ [4] PID term breakdown    │
│     inlet & outlet       │     P / I / D / FF        │
├──────────────────────────┴──────────────────────────┤
│ [5] Heat fluxes  Q_water / Q_envelope / Q_fan        │
└──────────────────────────────────────────────────────┘

Simulation scenario (disturbances exercise the controller):
  t =  180 s  →  Setpoint step          20 °C → 24 °C
  t =  380 s  →  Inlet water drop       50 °C → 38 °C  (controller loses authority)
  t =  520 s  →  Inlet water restored   38 °C → 50 °C  (recovery)
  t =  680 s  →  Setpoint step back     24 °C → 20 °C

Timing:
  At 25 fps × 2 model steps × 0.5 s/step → ~25 simulation-seconds per real second.
  The full 800-second scenario plays out in about 32 real seconds.

Run:
    python main.py

Dependencies:
    pip install numpy matplotlib
"""

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.animation import FuncAnimation

from greenhouse_model import GreenhouseModel, GreenhouseParams
from greenhouse_controller import GreenhouseController, ControllerParams


# ── Simulation timing ─────────────────────────────────────────────────────────
DT = 0.5  # model + controller step     [s]
ANIM_INTERVAL_MS = 40  # animation frame period      [ms]  ≈ 25 fps
STEPS_PER_FRAME = 2  # model steps per anim frame
HISTORY_S = 800  # seconds of history kept on screen


# ── Disturbance scenario ──────────────────────────────────────────────────────
# Each tuple: (trigger_time_s, display_label, action(model, ctrl))
EVENTS = [
    (180, "Setpoint  20 → 24 °C", lambda m, c: c.set_setpoint(24.0)),
    (380, "Inlet water  50 → 38 °C", lambda m, c: m.set_T_inlet(38.0)),
    (520, "Inlet water  38 → 50 °C", lambda m, c: m.set_T_inlet(50.0)),
    (680, "Setpoint  24 → 20 °C", lambda m, c: c.set_setpoint(20.0)),
]


# ── Colour palette ────────────────────────────────────────────────────────────
BG, PANEL, GRID = "#0d1117", "#161b22", "#21262d"
TEXT = "#c9d1d9"

C_TAIR = "#58a6ff"  # blue      – air temperature (measured)
C_TRUE = "#1f6feb"  # dark-blue – air temperature (true, noiseless)
C_SP = "#3fb950"  # green     – setpoint
C_FAN = "#ff7b72"  # coral     – fan signal
C_TIN = "#d2a8ff"  # violet    – inlet water
C_TOUT = "#ffa657"  # amber     – outlet water
C_P = "#79c0ff"  # sky-blue  – P term
C_I = "#56d364"  # green     – I term
C_D = "#ff7b72"  # coral     – D term
C_FF = "#e3b341"  # gold      – FF term
C_QW = "#56d364"  # green     – Q_water
C_QE = "#ff7b72"  # coral     – Q_envelope
C_QF = "#d2a8ff"  # violet    – Q_fan
C_EV = "#e3b341"  # gold      – event marker


matplotlib.rcParams.update(
    {
        "font.family": "monospace",
        "axes.facecolor": PANEL,
        "figure.facecolor": BG,
        "axes.edgecolor": GRID,
        "axes.labelcolor": TEXT,
        "xtick.color": TEXT,
        "ytick.color": TEXT,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "axes.titlesize": 9,
        "axes.labelsize": 8,
        "grid.color": GRID,
        "grid.linestyle": "--",
        "grid.linewidth": 0.5,
        "text.color": TEXT,
        "axes.titlecolor": TEXT,
        "legend.fontsize": 7,
        "legend.facecolor": PANEL,
        "legend.edgecolor": GRID,
        "legend.labelcolor": TEXT,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Build simulation objects
# ─────────────────────────────────────────────────────────────────────────────


def build_simulation():
    """
    Instantiate the greenhouse model and fan controller with parameters that
    are physically consistent and produce visually clear dynamics.

    Key design point:
      At steady state (T_air = 20 °C, T_inlet = 50 °C, T_ambient = 10 °C)
      the fan runs at ~80 %, providing good authority in both directions.
      The closed-loop time constant is ~89 s, so the full scenario plays
      out over ~800 simulation seconds (≈ 32 real seconds in the animation).
    """
    params = GreenhouseParams(
        volume_m3=0.102,  # 30 m³ — compact research greenhouse
        flow_rate_kgs=0.042,  # kg/s  — small hot-water boiler
        UA_hx=50.0,  # W/K   — desktop radiator
        UA_envelope=10.0,  # W/K   — plastic bin
        fan_max_flow_m3s=0.057 * 2,  # m³/s  — dual exhaust 140 mm fans
        T_ambient=10.0,  # °C    — outside air temperature
        T_inlet=50.0,  # °C    — kettle outlet
        noise_std_air=0.1,
        noise_std_water=0.1,
    )
    # params = GreenhouseParams(
    #     volume_m3=30.0,  # 30 m³ — compact research greenhouse
    #     flow_rate_kgs=0.05,  # kg/s  — small hot-water boiler
    #     UA_hx=500.0,  # W/K   — fin-and-tube heat exchanger
    #     UA_envelope=150.0,  # W/K   — polycarbonate cladding
    #     fan_max_flow_m3s=0.15,  # m³/s  — single exhaust fan
    #     T_ambient=-5.0,  # °C    — cold winter day
    #     T_inlet=50.0,  # °C    — boiler outlet
    #     noise_std_air=0.05,
    #     noise_std_water=0.03,
    # )

    # Print design-point summary
    ss = params.steady_state_fan(20.0)
    tau_ol = (
        params.mass_air_kg
        * 1006.0
        / (params.effectiveness * params.C_water + params.UA_envelope)
    )
    denom_cl = (
        params.effectiveness * params.C_water
        + params.UA_envelope
        + ss * params.C_fan_air
    )
    tau_cl = params.mass_air_kg * 1006.0 / denom_cl
    nofan_eq = params.no_fan_equilibrium()

    print(f"  ε = {params.effectiveness:.4f},  C_water = {params.C_water:.1f} W/K")
    print(f"  No-fan equilibrium = {nofan_eq:.1f} °C")
    print(f"  Steady-state fan @ SP=20 °C = {ss * 100:.1f} %")
    print(f"  Open-loop τ  = {tau_ol:.0f} s  |  Closed-loop τ ≈ {tau_cl:.0f} s")

    model = GreenhouseModel(params, T_air_init=15.0)

    ctrl = GreenhouseController(
        params=ControllerParams(
            setpoint=20.0,
            Kp=0.15,
            Ki=0.005,
            Kd=2.0,
            Kff=0.015,
            tau_filter=5.0,
            rate_limit=0.08,
        ),
        dt=DT,
    )
    return model, ctrl


# ─────────────────────────────────────────────────────────────────────────────
# Figure layout
# ─────────────────────────────────────────────────────────────────────────────


def make_figure():
    fig = plt.figure(figsize=(15, 9), facecolor=BG)
    fig.suptitle(
        "   Water-Heated Greenhouse — Fan Control Live Simulation",
        fontsize=12,
        fontweight="bold",
        color=TEXT,
        y=0.987,
    )
    gs = gridspec.GridSpec(
        3,
        2,
        figure=fig,
        hspace=0.55,
        wspace=0.32,
        left=0.07,
        right=0.97,
        top=0.93,
        bottom=0.06,
    )
    ax = {
        "temp": fig.add_subplot(gs[0, 0]),
        "fan": fig.add_subplot(gs[0, 1]),
        "water": fig.add_subplot(gs[1, 0]),
        "pid": fig.add_subplot(gs[1, 1]),
        "flux": fig.add_subplot(gs[2, :]),
    }
    TITLES = {
        "temp": "Air Temperature  [°C]",
        "fan": "Fan Signal  (0 = off,  1 = full speed)",
        "water": "Water Loop Temperatures  [°C]",
        "pid": "Controller Term Breakdown  [fan units]",
        "flux": "Heat Fluxes  [W]",
    }
    YLABELS = {
        "temp": "°C",
        "fan": "Signal",
        "water": "°C",
        "pid": "Contribution",
        "flux": "Power [W]",
    }
    for key, a in ax.items():
        a.set_title(TITLES[key], pad=5)
        a.set_ylabel(YLABELS[key])
        a.set_xlabel("Elapsed time  [s]")
        a.grid(True, alpha=0.6)

    ax["fan"].set_ylim(-0.05, 1.15)
    return fig, ax


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard runner
# ─────────────────────────────────────────────────────────────────────────────


def run_dashboard():
    model, ctrl = build_simulation()

    # Pre-allocated ring-buffer
    max_pts = int(HISTORY_S / DT) + 20
    KEYS = (
        "t",
        "T_air_m",
        "T_air_true",
        "T_sp",
        "fan",
        "T_inlet",
        "T_outlet",
        "P",
        "I",
        "D",
        "FF",
        "Q_water",
        "Q_envelope",
        "Q_fan",
    )
    buf = {k: np.full(max_pts, np.nan) for k in KEYS}
    ptr = [0]

    triggered = set()

    # ── Figure ────────────────────────────────────────────────────────────────
    fig, ax = make_figure()

    # ── Line objects ──────────────────────────────────────────────────────────
    def L(a, color, lw=1.4, ls="-", label=""):
        (ln,) = a.plot([], [], color=color, lw=lw, ls=ls, label=label)
        return ln

    ln_Tair_m = L(ax["temp"], C_TAIR, lw=1.4, label="T_air (measured)")
    ln_Tair_true = L(ax["temp"], C_TRUE, lw=0.8, ls="--", label="T_air (true)")
    ln_Tsp = L(ax["temp"], C_SP, lw=1.4, ls=":", label="Setpoint")
    ln_fan = L(ax["fan"], C_FAN, lw=1.8, label="Fan signal")
    ln_Tin = L(ax["water"], C_TIN, lw=1.4, label="T_inlet")
    ln_Tout = L(ax["water"], C_TOUT, lw=1.4, label="T_outlet")
    ln_P = L(ax["pid"], C_P, label="P  (proportional)")
    ln_I = L(ax["pid"], C_I, label="I  (integral)")
    ln_D = L(ax["pid"], C_D, label="D  (derivative, filtered)")
    ln_FF = L(ax["pid"], C_FF, label="FF (feedforward ΔT_water)")
    ln_Qw = L(ax["flux"], C_QW, lw=1.8, label="Q_water (heat supply)")
    ln_Qe = L(ax["flux"], C_QE, lw=1.4, label="Q_envelope (loss)")
    ln_Qf = L(ax["flux"], C_QF, lw=1.4, label="Q_fan (exhaust)")

    for key in ("temp", "water", "pid", "flux"):
        ax[key].legend(loc="upper left")

    # ── Live text overlays ────────────────────────────────────────────────────
    txt_air = ax["temp"].text(
        0.99,
        0.99,
        "",
        transform=ax["temp"].transAxes,
        color=C_TAIR,
        fontsize=8,
        va="top",
        ha="right",
    )
    txt_fan = ax["fan"].text(
        0.99,
        0.99,
        "",
        transform=ax["fan"].transAxes,
        color=C_FAN,
        fontsize=8,
        va="top",
        ha="right",
    )
    txt_event = fig.text(
        0.5, 0.004, "", ha="center", fontsize=8, color=C_EV, style="italic"
    )

    # ── Y-autoscale helper ────────────────────────────────────────────────────
    def autoscale(a, *arrays, margin=0.18):
        valid = np.concatenate([v[~np.isnan(v)] for v in arrays])
        if valid.size == 0:
            return
        lo, hi = valid.min(), valid.max()
        span = hi - lo if (hi != lo) else (abs(hi) * 0.1 + 0.5)
        a.set_ylim(lo - margin * span, hi + margin * span)

    # ── Event helper ──────────────────────────────────────────────────────────
    def fire_event(t_ev, label, action):
        action(model, ctrl)
        for a in ax.values():
            a.axvline(t_ev, color=C_EV, lw=0.9, ls=":", alpha=0.8)
        txt_event.set_text(f"   t = {t_ev} s  →  {label}")

    # ── Animation frame ───────────────────────────────────────────────────────
    def update(_frame):
        for _ in range(STEPS_PER_FRAME):
            # Fire any due events
            for t_ev, label, action in EVENTS:
                if t_ev not in triggered and model.time >= t_ev:
                    triggered.add(t_ev)
                    fire_event(t_ev, label, action)

            # Sensor readings (with noise)
            Ti = model.T_inlet_measured
            To = model.T_outlet_measured
            Ta = model.T_air_measured  # noisy measurement → controller

            # Fan command from controller
            fan = ctrl.update(Ti, To, Ta)

            # Advance model
            model.step(fan, DT)

            # Write to ring-buffer
            i = ptr[0] % max_pts
            buf["t"][i] = model.time
            buf["T_air_m"][i] = Ta  # measured (noisy)
            buf["T_air_true"][i] = model.T_air  # true state
            buf["T_sp"][i] = ctrl.setpoint
            buf["fan"][i] = fan
            buf["T_inlet"][i] = Ti
            buf["T_outlet"][i] = To
            buf["P"][i] = ctrl.term_P
            buf["I"][i] = ctrl.term_I
            buf["D"][i] = ctrl.term_D
            buf["FF"][i] = ctrl.term_FF
            buf["Q_water"][i] = model.Q_water
            buf["Q_envelope"][i] = model.Q_envelope
            buf["Q_fan"][i] = model.Q_fan
            ptr[0] += 1

        # ── Ordered view of ring-buffer ───────────────────────────────────────
        n = min(ptr[0], max_pts)
        idx = np.roll(np.arange(max_pts), -(ptr[0] % max_pts))[:n]
        t = buf["t"][idx]

        valid = ~np.isnan(t)
        if not np.any(valid):
            return []

        t = t[valid]
        g = lambda k: buf[k][idx][valid]

        if n == 0:
            return []

        t_now = t[-1]
        t_min = max(0.0, t_now - HISTORY_S)
        t_max = t_now + 8.0

        # ── Update lines ──────────────────────────────────────────────────────
        ln_Tair_m.set_data(t, g("T_air_m"))
        ln_Tair_true.set_data(t, g("T_air_true"))
        ln_Tsp.set_data(t, g("T_sp"))
        ln_fan.set_data(t, g("fan"))
        ln_Tin.set_data(t, g("T_inlet"))
        ln_Tout.set_data(t, g("T_outlet"))
        ln_P.set_data(t, g("P"))
        ln_I.set_data(t, g("I"))
        ln_D.set_data(t, g("D"))
        ln_FF.set_data(t, g("FF"))
        ln_Qw.set_data(t, g("Q_water"))
        ln_Qe.set_data(t, g("Q_envelope"))
        ln_Qf.set_data(t, g("Q_fan"))

        # ── Autoscale Y ───────────────────────────────────────────────────────
        autoscale(ax["temp"], g("T_air_m"), g("T_air_true"), g("T_sp"))
        autoscale(ax["water"], g("T_inlet"), g("T_outlet"))
        autoscale(ax["pid"], g("P"), g("I"), g("D"), g("FF"))
        autoscale(ax["flux"], g("Q_water"), g("Q_envelope"), g("Q_fan"))

        # ── Scroll x ─────────────────────────────────────────────────────────
        for a in ax.values():
            a.set_xlim(t_min, t_max)

        # ── Live readouts ─────────────────────────────────────────────────────
        Ta_now = g("T_air_true")[-1]
        sp_now = g("T_sp")[-1]
        fan_now = g("fan")[-1]
        txt_air.set_text(
            f"T = {Ta_now:.2f} °C   SP = {sp_now:.1f} °C   Δ = {Ta_now - sp_now:+.2f} °C"
        )
        txt_fan.set_text(f"Fan = {fan_now:.3f}  ({fan_now * 100:.1f} %)")

        return (
            ln_Tair_m,
            ln_Tair_true,
            ln_Tsp,
            ln_fan,
            ln_Tin,
            ln_Tout,
            ln_P,
            ln_I,
            ln_D,
            ln_FF,
            ln_Qw,
            ln_Qe,
            ln_Qf,
            txt_air,
            txt_fan,
            txt_event,
        )

    ani = FuncAnimation(
        fig,
        update,
        interval=ANIM_INTERVAL_MS,
        blit=False,  # blit=False keeps event vlines visible
        cache_frame_data=False,
    )
    plt.show()
    return ani  # hold reference — GC would destroy it otherwise


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 64)
    print("  Greenhouse Fan Control — Live Simulation Dashboard")
    print("=" * 64)
    print("  System parameters:")
    ani = run_dashboard()  # prints params, then opens window
