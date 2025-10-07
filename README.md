# Mini Virtual ECU

This repository contains a small engine simulator (RPM, coolant temp, oil pressure), a diagnostics layer that logs DTCs/events, and a Streamlit dashboard for visualization. There is optional SocketCAN (vcan) support to publish CAN frames.

## Quick start (recommended)

1. Create and activate a virtual environment (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
# Mini Virtual ECU

This repository contains a small engine simulator (RPM, coolant temp, oil pressure), a diagnostics layer that logs DTCs/events, and a Streamlit dashboard for visualization. There is optional SocketCAN (vcan) support to publish CAN frames.

## Quick start (recommended)

1. Create and activate a virtual environment (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install Python dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

3. Run the simulator (prints frames and writes `logs/data_log.csv`):

```bash
# from repo root
python mini_virtual_ecu/simulator/engine_simulation.py --no-can --duration 30 --interval 0.5 --seed 42
```

4. Run the Streamlit dashboard (in another terminal, venv active):

```bash
streamlit run visualizer/streamlit_dashboard.py --server.port 8503
```

Open http://localhost:8503 in your browser.

## Optional: SocketCAN (vcan) integration

If you want the simulator to publish frames on a Linux SocketCAN interface (useful for testing CAN tools), you can create a virtual CAN device:

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0
```

Then install `python-can` in the same environment and run the simulator with `--vcan`:

```bash
pip install python-can
python mini_virtual_ecu/simulator/engine_simulation.py --vcan --can-iface vcan0 --duration 30 --interval 0.5 --seed 42
```

You can listen with the included helper (if present):

```bash
python tools/can_listener.py --iface vcan0
```

If you do not want to use vcan, pass `--no-can` to force printing and CSV logging.

## Files and structure

- `mini_virtual_ecu/simulator/engine_simulation.py` — engine model, fault injector, logging, CLI
- `mini_virtual_ecu/visualizer/streamlit_dashboard.py` — Streamlit dashboard (includes a live-simulation mode)
- `mini_virtual_ecu/visualizer/plot_signals.py` — static plotter (if present)
- `mini_virtual_ecu/tools/can_listener.py` — optional helper to listen to vcan0 and decode frames
- `mini_virtual_ecu/logs/` — CSV logs written by the simulator:
  - `data_log.csv` — time series
  - `dtc_log.csv` — DTCs written by diagnostics
  - `event_log.csv` — notable events (derate, pressure spikes, fan on/off)

## Troubleshooting

- "ModuleNotFoundError: No module named 'matplotlib'" or similar: make sure you're running Streamlit from the same Python environment where you installed the dependencies. Use `.venv/bin/streamlit` or `source .venv/bin/activate` first.
- If Streamlit fails to import packages when launched via `pipx` or system streamlit, prefer running the one from the project venv to ensure the same packages are available.
- If CAN frames are not appearing on `vcan0`: ensure `vcan0` exists and is `up` (see commands above). Use `ip -details link show vcan0` to inspect. Ensure `python-can` is installed in the same environment used to run the simulator.


## CLI arguments (simulator)

The simulator script `mini_virtual_ecu/simulator/engine_simulation.py` accepts the following command-line arguments:

- `--no-can` (flag)
  - Do not use python-can / SocketCAN. The simulator will print frames and write CSV logs.
- `--vcan` (flag)
  - Attempt to send frames over SocketCAN (default interface `vcan0`). If opening/sending to CAN fails the simulator will fall back to printing frames and will log an event.
- `--duration` (float, default: 10.0)
  - How long to run the simulation (seconds).
- `--interval` (float, default: 0.5)
  - Time between frames / simulation steps (seconds).
- `--seed` (int, default: None)
  - RNG seed for reproducible runs.
- `--fault-rate` (float, default: 0.02)
  - Base probability controlling fault injection frequency.
- `--can-iface` (string, default: `vcan0`)
  - SocketCAN interface name to use when `--vcan` is passed (e.g., `vcan0`, `can0`).

Note: currently the parsed `--can-iface` value is defined by the CLI but the running code uses a local `can_iface` variable set to `'vcan0'`. I can patch the simulator to honor `--can-iface` if you want — it is a quick change.
##Screenshots: 
1) Simple Simulation Graph via matplotlib: 
<img width="1920" height="1080" alt="Screenshot_2025-10-07_16_00_21" src="https://github.com/user-attachments/assets/5f3b0dcb-7adb-46f4-8998-ddd48e378351" />
2) DTCs and Events Data: 
<img width="1920" height="1080" alt="Screenshot_2025-10-07_16_00_32" src="https://github.com/user-attachments/assets/6479a7cc-701b-48ed-8fa8-9e4389f2a5a0" />


