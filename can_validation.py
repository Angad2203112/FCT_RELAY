import can
import time
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

# ── Configuration ─────────────────────────────────────────────────────────────
PORT            = 'COM128'
TOLERANCE       = 0.30    # ±30 % window before flagging late
WATCHDOG_TICK_S = 0.5     # how often the watchdog checks (seconds)

PERIODIC_MS  = 1000   # expected cycle for PERIODIC messages  (ms)
HEARTBEAT_MS = 5000   # expected cycle for HEARTBEAT messages (ms)

# ANSI colours
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"


# ── Message type ──────────────────────────────────────────────────────────────
class T(Enum):
    PERIODIC  = "PERIODIC"
    HEARTBEAT = "HEARTBEAT"
    EVENT     = "EVENT"


# ── Message catalogue (from VecChgStnProtocolFile.dbc) ───────────────────────
# Format: CAN_ID (29-bit): (name, MsgType, expected_interval_ms or None)
#
# IDs are DBC raw IDs with bit-31 (extended-frame flag) stripped:
#   actual_id = dbc_raw_id & 0x1FFFFFFF
#
MESSAGES: Dict[int, tuple] = {
    # ── Heartbeats (every 5 s) ────────────────────────────────────────────
    0x00176769: ("MB_heartbeat",            T.HEARTBEAT, HEARTBEAT_MS),
    0x1F100101: ("DaughterBoardHeartBeat",  T.HEARTBEAT, HEARTBEAT_MS),
    0x01CF6301: ("BatteryHeartbeat",        T.HEARTBEAT, HEARTBEAT_MS),
    0x01CD2301: ("ChgHeartbeat",            T.HEARTBEAT, HEARTBEAT_MS),

    # ── Periodic status / live data (every 1 s) ───────────────────────────
    0x001BF5F1: ("relayboardStatus1",       T.PERIODIC,  PERIODIC_MS),
    0x001BF5F2: ("relayboardStatus2",       T.PERIODIC,  PERIODIC_MS),
    0x001BF5F3: ("relayboardStatus3",       T.PERIODIC,  PERIODIC_MS),
    0x001567DE: ("relayTemperature",        T.PERIODIC,  PERIODIC_MS),
    0x1F200301: ("TempData",               T.PERIODIC,  PERIODIC_MS),
    0x053CB501: ("BatteryLiveMsg1",         T.PERIODIC,  PERIODIC_MS),
    0x053DBB01: ("BatteryLiveMsg2",         T.PERIODIC,  PERIODIC_MS),
    0x055A6B01: ("BmsInfo",                T.PERIODIC,  PERIODIC_MS),
    0x054DF301: ("ChgLiveMsg1",             T.PERIODIC,  PERIODIC_MS),

    # ── Event-triggered (no fixed cycle) ──────────────────────────────────
    0x001C7169: ("relayboardControl",       T.EVENT,     None),
    0x001A7D01: ("doorAck",                T.EVENT,     None),
    0x001A1569: ("doorControlCommand",      T.EVENT,     None),
    0x00210069: ("uniqueId",               T.EVENT,     None),
    0x001CF569: ("nfcCardData",            T.EVENT,     None),
    0x053EC101: ("BatterySuspensionMsg",    T.EVENT,     None),
    0x054EF901: ("ChgSuspensionMsg",        T.EVENT,     None),
    0x056B0701: ("ChargerInfo",            T.EVENT,     None),
}


# ── Per-message tracker ───────────────────────────────────────────────────────
@dataclass
class MsgTracker:
    can_id:      int
    name:        str
    msg_type:    T
    expected_ms: Optional[float]
    last_rx:     Optional[float] = None
    rx_count:    int = 0
    late_count:  int = 0


def build_trackers() -> Dict[int, MsgTracker]:
    return {
        cid: MsgTracker(can_id=cid, name=name, msg_type=mtype, expected_ms=exp)
        for cid, (name, mtype, exp) in MESSAGES.items()
    }


# ── Startup summary ───────────────────────────────────────────────────────────
def _print_catalogue(trackers: Dict[int, MsgTracker]) -> None:
    print(f"\n{'─'*70}")
    print(f"  {'CAN ID':<12}  {'Name':<30}  {'Type':<10}  {'Cycle'}")
    print(f"{'─'*70}")
    for t in sorted(trackers.values(), key=lambda x: (x.msg_type.value, x.can_id)):
        cycle = f"{t.expected_ms:.0f} ms" if t.expected_ms else "event"
        colour = {T.PERIODIC: CYAN, T.HEARTBEAT: GREEN, T.EVENT: YELLOW}[t.msg_type]
        print(
            f"  0x{t.can_id:08X}   {t.name:<30}  "
            f"{colour}{t.msg_type.value:<10}{RESET}  {cycle}"
        )
    print(f"{'─'*70}\n")


# ── Watchdog thread ───────────────────────────────────────────────────────────
def _watchdog(trackers: Dict[int, MsgTracker], stop: threading.Event) -> None:
    while not stop.wait(WATCHDOG_TICK_S):
        now = time.monotonic()
        for t in trackers.values():
            if t.msg_type == T.EVENT or t.last_rx is None:
                continue
            elapsed_ms  = (now - t.last_rx) * 1000
            deadline_ms = t.expected_ms * (1 + TOLERANCE)
            if elapsed_ms > deadline_ms:
                print(
                    f"{RED}[WATCHDOG] {t.msg_type.value:<10}  "
                    f"0x{t.can_id:08X}  {t.name}  "
                    f"silent for {elapsed_ms:.0f} ms  "
                    f"(deadline {deadline_ms:.0f} ms){RESET}"
                )


# ── Main receiver ─────────────────────────────────────────────────────────────
def receive_can_messages(port: str) -> None:
    trackers = build_trackers()
    _print_catalogue(trackers)

    print(f"Connecting to {port} ...")
    stop_event = threading.Event()

    try:
        bus = can.interface.Bus(interface='serial', channel=port)
        print(f"Listening on {port} ...  (Ctrl+C to stop)\n")

        wdog = threading.Thread(
            target=_watchdog, args=(trackers, stop_event), daemon=True
        )
        wdog.start()

        while True:
            msg = bus.recv(1.0)
            if msg is None:
                continue

            now    = time.monotonic()
            ts     = time.strftime('%H:%M:%S')
            data_h = " ".join(f"{b:02X}" for b in msg.data)
            t      = trackers.get(msg.arbitration_id)

            if t is None:
                print(
                    f"[{ts}] {YELLOW}[UNKNOWN]   {RESET}"
                    f"0x{msg.arbitration_id:08X}  {'?':<30}  "
                    f"DLC:{msg.dlc}  {data_h}"
                )
                continue

            # Timing check
            timing = ""
            if t.expected_ms and t.last_rx is not None:
                elapsed_ms  = (now - t.last_rx) * 1000
                deadline_ms = t.expected_ms * (1 + TOLERANCE)
                early_ms    = t.expected_ms * (1 - TOLERANCE)

                if elapsed_ms > deadline_ms:
                    timing = f"  {RED}LATE  {elapsed_ms - t.expected_ms:+.0f} ms{RESET}"
                    t.late_count += 1
                elif elapsed_ms < early_ms:
                    timing = f"  {YELLOW}EARLY {elapsed_ms - t.expected_ms:+.0f} ms{RESET}"
                else:
                    timing = f"  {GREEN}OK  {elapsed_ms:.0f} ms{RESET}"

            t.last_rx   = now
            t.rx_count += 1

            tag = {
                T.PERIODIC:  CYAN   + "[PERIODIC] " + RESET,
                T.HEARTBEAT: GREEN  + "[HEARTBEAT]" + RESET,
                T.EVENT:     YELLOW + "[EVENT]    " + RESET,
            }[t.msg_type]

            print(
                f"[{ts}] {tag}  "
                f"0x{msg.arbitration_id:08X}  {t.name:<30}  "
                f"DLC:{msg.dlc}  {data_h}"
                f"{timing}"
            )

    except KeyboardInterrupt:
        print("\nStopping ...")
    except Exception as exc:
        print(f"Error: {exc}")
        raise
    finally:
        stop_event.set()
        if 'bus' in locals():
            bus.shutdown()
        _print_summary(trackers)


# ── Session summary ───────────────────────────────────────────────────────────
def _print_summary(trackers: Dict[int, MsgTracker]) -> None:
    received = [t for t in trackers.values() if t.rx_count > 0]
    if not received:
        print("\nNo messages received.")
        return

    print(f"\n{'─'*70}")
    print(f"  {'CAN ID':<12}  {'Name':<30}  {'Type':<10}  {'RX':>6}  {'Late':>5}")
    print(f"{'─'*70}")
    for t in sorted(received, key=lambda x: x.can_id):
        late_s = f"{RED}{t.late_count}{RESET}" if t.late_count else "0"
        print(
            f"  0x{t.can_id:08X}   {t.name:<30}  "
            f"{t.msg_type.value:<10}  {t.rx_count:>6}  {late_s:>5}"
        )

    never = [
        t for t in trackers.values()
        if t.rx_count == 0 and t.msg_type != T.EVENT
    ]
    if never:
        print(f"\n{RED}Never received:{RESET}")
        for t in sorted(never, key=lambda x: x.can_id):
            print(f"  0x{t.can_id:08X}  {t.name}  [{t.msg_type.value}]")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    receive_can_messages(PORT)
