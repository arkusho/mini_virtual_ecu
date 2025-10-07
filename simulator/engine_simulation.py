#!/usr/bin/env python3
"""Engine simulator with improved realism and safety guards.

This implementation includes:
- soft/hard RPM limiter and overspeed logging
- stronger coolant/oil coupling and more effective fan
- smoothed and capped oil pressure
- derate behavior that ramps max_rpm and logs DTC
- softer fault injection spikes
"""
import argparse
import csv
import math
import random
import struct
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
LOGS_DIR = BASE_DIR / 'logs'
DATA_LOG = LOGS_DIR / 'data_log.csv'

# Scaling constants
RPM_MAX = 8000.0
TEMP_MAX = 150.0
PRESSURE_MAX = 400.0

DEFAULT_INTERVAL = 0.5


def encode_frame(rpm, temp, pressure):
    rpm_raw = int(max(0, min(RPM_MAX, rpm)) / RPM_MAX * 65535)
    temp_raw = int(max(0, min(TEMP_MAX, temp)) / TEMP_MAX * 255)
    pres_raw = int(max(0, min(PRESSURE_MAX, pressure)) / PRESSURE_MAX * 255)
    return struct.pack('>HBB', rpm_raw, temp_raw, pres_raw)


class EngineModel:
    def __init__(self, seed=None, engine_type='petrol'):
        self.rng = random.Random(seed)
        # states
        self.rpm = 900.0
        self.coolant_temp = 75.0
        self.ambient = 25.0
        self.oil_health = 1.0
        self.oil_temp = 80.0

        # parameters
        self.idle_rpm = 800.0
        self.max_rpm = 7000.0
        self.rpm_time_constant = 1.5
        self.thermal_time_constant = 40.0
        self.oil_pump_base = 120.0

        # engine type
        self.engine_type = engine_type
        self.oil_health_decay_rate = 0.0015
        self.redline_rpm = 6500.0 if self.engine_type == 'petrol' else 4500.0

        # fan/cooling params
        self.fan_on_temp = 100.0
        self.fan_off_temp = 95.0
        # slightly lower fan power to avoid sudden coolant drops; tuneable
        self.fan_cooling_power = 9.0
        self.fan_active = False

        # critical
        self.critical_oil_temp = 125.0

        # coolant behaviour
        self.hot_seconds = 0.0
        self.total_run_seconds = 0.0
        self.max_coolant_cool_rate = 1.5

        # derate
        self.derate_temp = 120.0
        self.derate_active = False
        self.derate_factor = 0.5
        self.derate_logged = False
        self._original_max_rpm = self.max_rpm
        self._derate_target_max = self.max_rpm
        self._derate_ramp_rate = 500.0

        # pressure smoothing
        self.pressure = 120.0
        self.pressure_time_constant = 0.6

        # event logging
        self.event_log = LOGS_DIR / 'event_log.csv'
        self._overspeed_active = False

    def _log_event(self, code, description):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        header = ['timestamp', 'code', 'description']
        ts = time.time()
        exists = self.event_log.exists()
        with open(self.event_log, 'a', newline='') as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(header)
            writer.writerow([f"{ts:.6f}", code, description])

    def _log_dtc(self, code, description):
        dtc_file = LOGS_DIR / 'dtc_log.csv'
        header = ['timestamp', 'code', 'description']
        exists = dtc_file.exists()
        ts = time.time()
        with open(dtc_file, 'a', newline='') as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(header)
            writer.writerow([f"{ts:.6f}", code, description])

    def step(self, throttle, dt, fault_injector=None):
        # apply derate to throttle (soft)
        if self.derate_active:
            throttle = throttle * self.derate_factor

        # throttle->target rpm (non-linear)
        throttle_curve = throttle ** 0.85
        target_rpm = self.idle_rpm + throttle_curve * (self.max_rpm - self.idle_rpm)

        # soft limiter: compress excess above redline
        redline = self.redline_rpm
        if target_rpm > redline:
            excess = target_rpm - redline
            target_rpm = redline + excess * 0.25

        # RPM dynamics
        alpha = 1 - math.exp(-dt / max(1e-6, self.rpm_time_constant))
        self.rpm += (target_rpm - self.rpm) * alpha

        # overspeed guard and logging
        if self.rpm > self.redline_rpm + 50.0:
            if not self._overspeed_active:
                self._overspeed_active = True
                self._log_event('OVERSPEED', f'RPM {self.rpm:.1f} exceeded redline {self.redline_rpm:.0f}')
                self._log_dtc('OVERSPEED', f'RPM {self.rpm:.1f} exceeded redline')
            self.rpm = min(self.rpm, self.redline_rpm + 100.0)
        else:
            if self._overspeed_active and self.rpm < self.redline_rpm - 100.0:
                self._overspeed_active = False
                self._log_event('OVERSPEED_CLEAR', f'RPM {self.rpm:.1f} returned below clear threshold')

        # heat generation and cooling
        heat = (0.9 * throttle + 0.3 * (self.rpm / max(1.0, self.max_rpm))) * 10.0
        heat += self.rng.uniform(-0.1, 0.1)
        cooling = (self.coolant_temp - self.ambient) / max(1.0, self.thermal_time_constant)

        # fan hysteresis (log transitions)
        if (self.coolant_temp > self.fan_on_temp or self.oil_temp > self.fan_on_temp) and not self.fan_active:
            self.fan_active = True
            try:
                self._log_event('FAN_ON', f'Fan turned ON at coolant {self.coolant_temp:.1f}C oil {self.oil_temp:.1f}C')
            except Exception:
                pass
        if (self.coolant_temp < self.fan_off_temp and self.oil_temp < self.fan_off_temp) and self.fan_active:
            self.fan_active = False
            try:
                self._log_event('FAN_OFF', f'Fan turned OFF at coolant {self.coolant_temp:.1f}C oil {self.oil_temp:.1f}C')
            except Exception:
                pass
        fan_cooling = 0.0
        if self.fan_active:
            fan_cooling = self.fan_cooling_power * (0.5 + 0.5 * (self.rpm / max(1.0, self.max_rpm)))

        # oil->coolant coupling (increased to improve coolant response)
        oil_to_coolant = 0.10 * (self.oil_temp - self.coolant_temp)
        coolant_delta = (heat - cooling - fan_cooling + oil_to_coolant) * dt
        if coolant_delta < 0 and abs(coolant_delta) > self.max_coolant_cool_rate * dt:
            coolant_delta = -self.max_coolant_cool_rate * dt
        self.coolant_temp += coolant_delta

        # oil dynamics
        oil_heat = 0.0020 * self.rpm
        oil_cooling = (self.oil_temp - self.ambient) / 200.0
        self.oil_temp += (oil_heat - oil_cooling) * dt

        # pressure model (smoothed)
        pump_flow = (self.rpm / max(1.0, self.max_rpm)) * self.oil_health
        base_pressure = 120.0
        rpm_pressure = 350.0 * (self.rpm / max(1.0, self.max_rpm)) ** 0.9
        temp_factor = max(0.4, 1.0 - (self.oil_temp - 90.0) / 200.0)
        pressure_kpa = (base_pressure + rpm_pressure * pump_flow) * temp_factor
        alpha_p = 1 - math.exp(-dt / max(1e-6, self.pressure_time_constant))
        self.pressure = self.pressure * (1 - alpha_p) + pressure_kpa * alpha_p
        pressure_kpa = max(0.0, min(350.0, self.pressure))

        if pressure_kpa > 320.0:
            self._log_event('PRESSURE_SPIKE', f'Pressure spike {pressure_kpa:.1f} kPa at RPM {self.rpm:.1f}')
            self._log_dtc('PRESSURE_SPIKE', f'Pressure {pressure_kpa:.1f} kPa')

        # fault injection
        if fault_injector is not None:
            fault_injector(self, throttle)

        # oil health decay
        if self.oil_temp > 100.0:
            self.hot_seconds += dt
            decay = self.oil_health_decay_rate * (self.oil_temp - 90.0) * dt * (1.0 + self.hot_seconds / 120.0)
            if self.oil_temp > 115.0:
                extra = math.exp((self.oil_temp - 115.0) / 8.0) * 0.002 * dt
                if self.oil_temp > 120.0:
                    extra *= 5.0
                decay += extra
            self.oil_health = max(0.0, self.oil_health - decay)
        else:
            self.hot_seconds = max(0.0, self.hot_seconds - dt * 0.5)

        self.total_run_seconds += dt

        # sensor noise
        noise_rpm = self.rng.gauss(0, max(1.0, 0.002 * self.rpm))
        noise_temp = self.rng.gauss(0, max(0.2, 0.002 * self.coolant_temp))
        noise_pressure = self.rng.gauss(0, max(0.5, 0.005 * pressure_kpa))

        rpm_noisy = max(0.0, self.rpm + noise_rpm)
        temp_noisy = max(self.ambient, self.coolant_temp + noise_temp)
        pressure_noisy = max(0.0, pressure_kpa + noise_pressure)

        if self.fan_active:
            temp_noisy = max(self.ambient, temp_noisy - fan_cooling * min(1.0, dt))
            pressure_noisy = max(0.0, pressure_noisy * 0.985)

        self.coolant_temp = max(self.ambient, self.coolant_temp)
        self.rpm = max(0.0, self.rpm)

        # redline handling
        redline = self.redline_rpm
        if self.engine_type == 'diesel':
            redline = min(redline, 4500.0)
        if self.rpm > redline:
            self.rpm = min(self.rpm, redline + 100.0)

        # warnings
        if self.oil_temp > 110.0:
            print(f"WARNING: OilTemp high: {self.oil_temp:.1f} C")
        if pressure_noisy < 100.0:
            print(f"WARNING: LowOilPressure: {pressure_noisy:.1f} kPa")

        # derate behavior
        if self.oil_temp >= self.derate_temp and not self.derate_active:
            self.derate_active = True
            self.derate_logged = False
            self._derate_target_max = max(self.max_rpm * self.derate_factor, 1500.0)
            self._log_event('DERATE_ON', f'OilTemp {self.oil_temp:.1f}C reached derate threshold')
        if self.derate_active:
            ramp = self._derate_ramp_rate * dt
            if self.max_rpm > self._derate_target_max:
                self.max_rpm = max(self._derate_target_max, self.max_rpm - ramp)
            if not self.derate_logged:
                self._log_dtc('DERATE_ACTIVE', f'Derate engaged at OilTemp {self.oil_temp:.1f}C')
                self.derate_logged = True
        if self.derate_active and self.oil_temp < (self.derate_temp - 3.0):
            self.derate_active = False
            self._log_event('DERATE_OFF', f'OilTemp {self.oil_temp:.1f}C cooled below derate threshold')
        if not self.derate_active and self.max_rpm < self._original_max_rpm:
            ramp = self._derate_ramp_rate * dt
            self.max_rpm = min(self._original_max_rpm, self.max_rpm + ramp)

        # critical
        if self.oil_temp >= self.critical_oil_temp:
            self._log_event('CRITICAL_OIL_TEMP', f'CRITICAL: OilTemp exceeded {self.critical_oil_temp} C: {self.oil_temp:.1f} C')
            raise RuntimeError(f"CRITICAL: OilTemp exceeded {self.critical_oil_temp} C: {self.oil_temp:.1f} C")

        status = 'derate' if self.derate_active else 'ok'
        return rpm_noisy, temp_noisy, pressure_noisy, status


def make_fault_injector(rng, fault_rate=0.01):
    state = {'overspeed_timer': 0, 'overheat_timer': 0, 'oil_failure': False}

    def injector(engine, throttle):
        # soften spike magnitude and spread over more ticks
        if state['overspeed_timer'] > 0:
            # smaller increment per tick
            engine.rpm += rng.uniform(300, 900)
            if state['overspeed_timer'] == 1:
                try:
                    engine._log_event('INJECT_OVERSPEED', f'Injected RPM spike at throttle {throttle:.2f}')
                except Exception:
                    pass
            state['overspeed_timer'] -= 1
        else:
            if rng.random() < fault_rate:
                state['overspeed_timer'] = rng.randint(2, 6)

        if state['overheat_timer'] > 0:
            engine.coolant_temp += rng.uniform(0.3, 0.9)
            if state['overheat_timer'] == 1:
                try:
                    engine._log_event('INJECT_OVERHEAT', f'Injected overheat spike')
                except Exception:
                    pass
            state['overheat_timer'] -= 1
        else:
            if rng.random() < fault_rate * 0.5:
                state['overheat_timer'] = rng.randint(10, 30)

        if (not state['oil_failure']) and rng.random() < fault_rate * 0.2:
            state['oil_failure'] = True
            delta = rng.uniform(0.2, 0.6)
            engine.oil_health = max(0.0, engine.oil_health - delta)
            try:
                engine._log_event('INJECT_OILFAIL', f'Injected oil pump failure, oil_health -{delta:.2f}')
            except Exception:
                pass

    return injector


def run_simulation(duration=10.0, interval=DEFAULT_INTERVAL, use_vcan=False, no_can=True, seed=None, fault_rate=0.02):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    engine = EngineModel(seed=seed)
    rng = random.Random(seed)
    injector = make_fault_injector(rng, fault_rate=fault_rate)

    start = time.time()
    t = 0.0
    can_bus = None
    can_iface = 'vcan0'

    def log_dtc(code, description, timestamp):
        dtc_file = LOGS_DIR / 'dtc_log.csv'
        header = ['timestamp', 'code', 'description']
        exists = dtc_file.exists()
        with open(dtc_file, 'a', newline='') as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(header)
            writer.writerow([f"{timestamp:.6f}", code, description])

    with open(DATA_LOG, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['timestamp', 'rpm', 'temp', 'pressure', 'throttle', 'ambient', 'oil_health', 'oil_temp'])

        # if requested, try to open a persistent python-can SocketCAN bus once
        if use_vcan and not no_can:
            try:
                import can
                can_bus = can.interface.Bus(channel=can_iface, bustype='socketcan')
            except Exception as e:
                print('Warning: unable to open CAN interface', can_iface, '-', e)
                try:
                    engine._log_event('CAN_OPEN_FAIL', f'Failed to open CAN interface {can_iface}: {e}')
                except Exception:
                    pass

        while time.time() - start < duration:
            throttle = 0.4 + 0.35 * math.sin(t / 6.0) + rng.uniform(-0.05, 0.05)
            throttle = max(0.0, min(1.0, throttle))

            try:
                rpm, temp, pressure, status = engine.step(throttle, interval, fault_injector=injector)
            except RuntimeError as e:
                now = time.time()
                print('CRITICAL EVENT:', e)
                log_dtc('FATAL_OIL_TEMP', str(e), now)
                writer.writerow([f"{now:.6f}", f"{engine.rpm:.2f}", f"{engine.coolant_temp:.2f}", f"{max(0.0, pressure):.2f}", f"{throttle:.3f}", f"{engine.ambient:.2f}", f"{engine.oil_health:.3f}", f"{engine.oil_temp:.2f}"])
                csvfile.flush()
                return
            timestamp = time.time()

            writer.writerow([f"{timestamp:.6f}", f"{rpm:.2f}", f"{temp:.2f}", f"{pressure:.2f}", f"{throttle:.3f}", f"{engine.ambient:.2f}", f"{engine.oil_health:.3f}", f"{engine.oil_temp:.2f}"])
            csvfile.flush()

            frame = encode_frame(rpm, temp, min(pressure, PRESSURE_MAX))
            # prefer sending on an opened bus
            if can_bus is not None:
                try:
                    import can
                    msg = can.Message(arbitration_id=0x100, data=frame, is_extended_id=False)
                    can_bus.send(msg)
                except Exception as e:
                    print('CAN send failed:', e)
                    try:
                        engine._log_event('CAN_SEND_FAIL', f'CAN send failed: {e}')
                    except Exception:
                        pass
            else:
                # fallback: either user disabled CAN or opening the interface failed
                print(f"ID:0x100 | Time:{timestamp:.3f} | Data:{frame.hex()} | RPM:{rpm:.1f} Temp:{temp:.1f} Pres:{pressure:.1f}kPa Throttle:{throttle:.2f} OilHealth:{engine.oil_health:.2f} OilTemp:{engine.oil_temp:.1f}C")

            time.sleep(interval)
            t += interval


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-can', action='store_true', help='Do not use python-can; write to CSV and print frames instead')
    parser.add_argument('--vcan', action='store_true', help='Send frames to vcan0 using python-can')
    parser.add_argument('--duration', type=float, default=10.0, help='Duration in seconds')
    parser.add_argument('--interval', type=float, default=DEFAULT_INTERVAL, help='Interval between frames (s)')
    parser.add_argument('--seed', type=int, default=None, help='RNG seed')
    parser.add_argument('--fault-rate', type=float, default=0.02, help='Base fault injection probability')
    parser.add_argument('--can-iface', type=str, default='vcan0', help='SocketCAN interface to use when --vcan is set')
    args = parser.parse_args()

    run_simulation(duration=args.duration, interval=args.interval, use_vcan=args.vcan, no_can=args.no_can or not args.vcan, seed=args.seed, fault_rate=args.fault_rate)


if __name__ == '__main__':
    main()

