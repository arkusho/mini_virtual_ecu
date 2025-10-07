import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
import importlib.util
import math
import random
import time
import csv

BASE = Path(__file__).resolve().parents[1]
LOGS = BASE / 'logs'
DATA_LOG = LOGS / 'data_log.csv'
DTC_LOG = LOGS / 'dtc_log.csv'
EVENT_LOG = LOGS / 'event_log.csv'

st.set_page_config(page_title='Mini ECU Dashboard', layout='wide')

st.title('Mini Virtual ECU — Dashboard')

st.sidebar.header('Controls')
refresh = st.sidebar.button('Refresh')
rows = st.sidebar.slider('Rows to show', min_value=50, max_value=5000, value=100, step=50)

@st.cache_data
def load_data(nrows=None):
    if not DATA_LOG.exists():
        return pd.DataFrame()
    df = pd.read_csv(DATA_LOG)
    if nrows:
        return df.tail(nrows)
    return df

@st.cache_data
def load_events():
    if not DTC_LOG.exists():
        dtc = pd.DataFrame()
    else:
        dtc = pd.read_csv(DTC_LOG)
    if not EVENT_LOG.exists():
        events = pd.DataFrame()
    else:
        events = pd.read_csv(EVENT_LOG)
    return dtc, events


df = load_data(rows)
dtc, events = load_events()

if df.empty:
    st.warning('No data available. Run the simulator first.')
    st.stop()

# Convert timestamp to readable
try:
    df['ts'] = pd.to_datetime(df['timestamp'].astype(float), unit='s')
except Exception:
    pass

col1, col2 = st.columns([3, 1])
with col1:
    st.subheader('Signals')
    fig, axes = plt.subplots(4, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(df['ts'], df['rpm'], label='RPM')
    axes[0].legend()
    axes[1].plot(df['ts'], df['temp'], label='Coolant Temp (C)', color='tab:orange')
    axes[1].legend()
    axes[2].plot(df['ts'], df['oil_temp'], label='Oil Temp (C)', color='tab:red')
    axes[2].legend()
    axes[3].plot(df['ts'], df['pressure'], label='Oil Pressure (kPa)', color='tab:green')
    axes[3].legend()
    plt.tight_layout()
    st.pyplot(fig)

with col2:
    st.subheader('Current Status')
    latest = df.iloc[-1]
    st.metric('RPM', f"{float(latest['rpm']):.0f}")
    st.metric('Coolant (C)', f"{float(latest['temp']):.1f}")
    st.metric('Oil Temp (C)', f"{float(latest['oil_temp']):.1f}")
    st.metric('Oil Health', f"{float(latest['oil_health']):.2f}")

st.subheader('Recent Events & DTCs')
if not dtc.empty:
    st.write('DTCs')
    st.dataframe(dtc.tail(20))
else:
    st.info('No DTCs logged')

if not events.empty:
    st.write('Events')
    st.dataframe(events.tail(50))
else:
    st.info('No events logged')

st.subheader('Raw data (tail)')
st.dataframe(df.tail(200))


## Live simulation
st.markdown('---')
st.header('Live simulation')
col_a, col_b, col_c = st.columns(3)
with col_a:
    live_duration = st.number_input('Duration (s)', min_value=1, max_value=600, value=30)
    live_interval = st.number_input('Interval (s)', min_value=0.05, max_value=2.0, value=0.5)
with col_b:
    live_seed = st.number_input('RNG seed (0 for random)', min_value=0, value=42)
    live_fault = st.slider('Fault rate', 0.0, 0.5, 0.02)
with col_c:
    start_live = st.button('Start live simulation')


def load_engine_module():
    mod_path = Path(__file__).resolve().parents[1] / 'simulator' / 'engine_simulation.py'
    spec = importlib.util.spec_from_file_location('engine_sim', str(mod_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if start_live:
    mod = load_engine_module()
    EngineModel = mod.EngineModel
    make_fault_injector = mod.make_fault_injector

    rng = random.Random(live_seed if live_seed != 0 else None)
    engine = EngineModel(seed=live_seed if live_seed != 0 else None)
    injector = make_fault_injector(rng, fault_rate=live_fault)

    # prepare plotting
    placeholder = st.empty()
    steps = max(1, int(live_duration / live_interval))
    rows = []

    # ensure logs dir exists and open CSV for append
    LOGS = Path(__file__).resolve().parents[1] / 'logs'
    LOGS.mkdir(parents=True, exist_ok=True)
    data_log = LOGS / 'data_log.csv'
    write_header = not data_log.exists()

    with open(data_log, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        if write_header:
            writer.writerow(['timestamp', 'rpm', 'temp', 'pressure', 'throttle', 'ambient', 'oil_health', 'oil_temp'])

        for i in range(steps):
            t = i * live_interval
            throttle = 0.4 + 0.35 * math.sin(t / 6.0) + rng.uniform(-0.05, 0.05)
            throttle = max(0.0, min(1.0, throttle))
            try:
                rpm, temp, pressure, status = engine.step(throttle, live_interval, fault_injector=injector)
            except RuntimeError as e:
                st.error(f'Critical event: {e}')
                break
            ts = time.time()
            row = {'timestamp': ts, 'rpm': rpm, 'temp': temp, 'pressure': pressure, 'throttle': throttle, 'ambient': engine.ambient, 'oil_health': engine.oil_health, 'oil_temp': engine.oil_temp}
            rows.append(row)
            writer.writerow([f"{ts:.6f}", f"{rpm:.2f}", f"{temp:.2f}", f"{pressure:.2f}", f"{throttle:.3f}", f"{engine.ambient:.2f}", f"{engine.oil_health:.3f}", f"{engine.oil_temp:.2f}"])
            csvfile.flush()

            # update dataframe and plot
            live_df = pd.DataFrame(rows)
            try:
                live_df['ts'] = pd.to_datetime(live_df['timestamp'], unit='s')
            except Exception:
                pass

            fig, axes = plt.subplots(4, 1, figsize=(8, 8), sharex=True)
            axes[0].plot(live_df['ts'], live_df['rpm'], label='RPM')
            axes[0].legend()
            axes[1].plot(live_df['ts'], live_df['temp'], label='Coolant', color='tab:orange')
            axes[1].legend()
            axes[2].plot(live_df['ts'], live_df['oil_temp'], label='Oil Temp', color='tab:red')
            axes[2].legend()
            axes[3].plot(live_df['ts'], live_df['pressure'], label='Pressure', color='tab:green')
            axes[3].legend()
            plt.tight_layout()
            placeholder.pyplot(fig)

            time.sleep(live_interval)

    st.success('Live simulation finished')
