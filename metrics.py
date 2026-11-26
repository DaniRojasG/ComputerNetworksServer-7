# created 2026-11-04 by dani/Androtis
# last modified: 2026-11-26 by dani/Androtis
# metrics code

import csv
import json
import os
import threading
import time
from datetime import datetime, timezone
import scapy.all as sp

# Init Variables
SERVER_PORT = 3300
JSON_REPORT_PATH = "metrics_report.json"
CSV_REPORT_PATH = "metrics_report.csv"


metrics_lock = threading.Lock()
stop_event = threading.Event()

# Active Packet flows and Completed transfers of these flows
active_flows = {}
completed_transfers = []

# Outline of Data to Be Stored
metrics_data = {
    "start_time": time.time(),
    "active_connections": 0,
    "total_connections": 0,
    "bytes_received": 0,
    "bytes_sent": 0,
    "commands": {"UPLOAD": 0, "DOWNLOAD": 0, "DIR": 0, "SUBFOLDER": 0, "DELETE": 0, "OTHER": 0},
}

# Formats Bytes into proper denominations
def format_bytes(num_bytes):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:} PB"

# Saves the JSON & CSV Locally
def save_report():
    payload = {
        "summary": metrics_data,
        "transfers": completed_transfers,
    }
    tmp_json = JSON_REPORT_PATH + ".tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_json, JSON_REPORT_PATH)

    if completed_transfers:
        tmp_csv = CSV_REPORT_PATH + ".tmp"
        with open(tmp_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(completed_transfers[0].keys()))
            writer.writeheader()
            writer.writerows(completed_transfers)
        os.replace(tmp_csv, CSV_REPORT_PATH)

# Checks top active flow and finalizes it for the report
def finalize_flow(session_key, end_time):
    flow = active_flows.pop(session_key, None)
    metrics_data["active_connections"] = len(active_flows)
    if not flow:
        return

    duration = max(end_time - flow["start_time"], 1e-6)
    cmd = flow["command"]
    transfer_bytes = (
        flow["bytes_in"] if cmd == "UPLOAD"
        else flow["bytes_out"] if cmd == "DOWNLOAD"
        else flow["bytes_in"] + flow["bytes_out"]
    )
    bytes_per_sec = transfer_bytes / duration
    latency_ms = (
        round((flow["first_response"] - flow["start_time"]) * 1000, 3)
        if flow["first_response"]
        else None
    )

    completed_transfers.append({
        "timestamp_utc": datetime.fromtimestamp(flow["start_time"], tz=timezone.utc).isoformat(),
        "client_ip": session_key[0],
        "client_port": session_key[1],
        "command": cmd,
        "filename": flow["filename"],
        "bytes_uploaded": flow["bytes_in"],
        "bytes_downloaded": flow["bytes_out"],
        "transfer_time_sec": round(duration, 6),
        "data_rate_MBps": round(bytes_per_sec / (1024 * 1024), 4),
        "data_rate_Mbps": round((bytes_per_sec * 8) / 1_000_000, 4),
        "response_latency_ms": latency_ms,
        "packet_count": flow["packets"],
    })
    save_report()


# Process's a Packet
def process_packet(pkt):
    # Checks if its IP and TCP
    if not (pkt.haslayer(sp.IP) and pkt.haslayer(sp.TCP)):
        return

    # Pull Relevant Data
    tcp = pkt[sp.TCP]
    ip = pkt[sp.IP]
    now = float(pkt.time)
    raw_payload = bytes(tcp.payload)
    payload_len = len(raw_payload)

    # Determine flow of packet
    with metrics_lock:
        if tcp.dport == SERVER_PORT:
            session = (ip.src, tcp.sport)
            if session not in active_flows:
                active_flows[session] = {
                    "start_time": now,
                    "first_response": None,
                    "command": "UNKNOWN",
                    "filename": "",
                    "bytes_in": 0,
                    "bytes_out": 0,
                    "packets": 0,
                }
                metrics_data["total_connections"] += 1
                metrics_data["active_connections"] = len(active_flows)

            flow = active_flows[session]
            flow["bytes_in"] += payload_len
            flow["packets"] += 1
            metrics_data["bytes_received"] += payload_len

            if flow["command"] == "UNKNOWN" and raw_payload.startswith(b"{"):
                try:
                    header = json.loads(raw_payload.split(b"\n", 1)[0].decode("utf-8"))
                    cmd = str(header.get("command", "")).upper()
                    if cmd:
                        flow["command"] = cmd
                        flow["filename"] = header.get("filename") or header.get("path") or ""
                        key = cmd if cmd in metrics_data["commands"] else "OTHER"
                        metrics_data["commands"][key] += 1
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass

            if tcp.flags & 0x05:
                finalize_flow(session, now)

        elif tcp.sport == SERVER_PORT:
            session = (ip.dst, tcp.dport)
            metrics_data["bytes_sent"] += payload_len
            if session in active_flows:
                flow = active_flows[session]
                if flow["first_response"] is None and payload_len > 0:
                    flow["first_response"] = now
                flow["bytes_out"] += payload_len
                flow["packets"] += 1

                if tcp.flags & 0x05:
                    finalize_flow(session, now)

# Start scapy sniffer
def start_sniffer(port=SERVER_PORT):
    while not stop_event.is_set():
        sp.sniff(
            filter=f"tcp port {port}",
            prn=process_packet,
            store=False,
            timeout=1,
            stop_filter=lambda _: stop_event.is_set(),
        )

# Driver code
def start_metrics(port=SERVER_PORT):
    global SERVER_PORT
    SERVER_PORT = port
    stop_event.clear()
    save_report()
    sniffer_thread = threading.Thread(target=start_sniffer, args=(port,), daemon=True)
    sniffer_thread.start()
    print(f"[Metrics] Tracking port {port} -> {CSV_REPORT_PATH}")

# Gracefully shuts down metrics
def stop_metrics():
    stop_event.set()
    with metrics_lock:
        save_report()

# If ran separate tho it should be called from server.py
if __name__ == "__main__":
    start_metrics(SERVER_PORT)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped")
        stop_metrics() 