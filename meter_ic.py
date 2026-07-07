import can
import time
import csv

PORT = "COM11"
LOG_TO_FILE = True

# ---------------- CSV SETUP ----------------
if LOG_TO_FILE:
    file = open("can_log.csv", "w", newline="")
    writer = csv.writer(file)
    writer.writerow(["Time", "Raw", "Voltage(V)", "Current(A)", "Power(W)"])

# ---------------- CAN INIT ----------------
bus = can.interface.Bus(interface='serial', channel=PORT)

print(f"Listening on {PORT}...\n")

try:
    while True:
        msg = bus.recv(1.0)

        if msg is None or msg.arbitration_id != 0x669d1:
            continue

        data = msg.data

        # RAW STRING
        raw = " ".join(f"{b:02X}" for b in data)

        if len(data) < 6:
            continue

        # ---------------- DECODE ----------------
        voltage_raw = (data[0] << 8) | data[1]
        current_raw = (data[2] << 8) | data[3]
        power_raw   = (data[4] << 8) | data[5]
        pulse_count = (data[6] << 8) | data[7]

        voltage = 1.095 * voltage_raw / 10.0
        current = current_raw / 100.0
        power   = power_raw
        pulse_count = 0.00110*pulse_count
        timestamp = time.strftime('%H:%M:%S')

        # ---------------- PRINT CLEAN ----------------                           
        print(f"[{timestamp}] {raw} || {voltage:.2f} V || {current+0.052:.3f} A || {power:.2f} W || {pulse_count:.2f} Kwh")

        # ---------------- SAVE CSV ---------------- 
        if LOG_TO_FILE:
            writer.writerow([timestamp, raw, voltage, current, power, pulse_count])
            file.flush()

except KeyboardInterrupt:
    print("\nStopped.")

finally:
    if LOG_TO_FILE:
        file.close()
    bus.shutdown()
