import curses
import time
import threading
import sys

# Simulated State
state = {
    "active_provider": "AWS",
    "cpu": 45,
    "memory": 60,
    "latency": 120,
    "logs": [
        "System Initialized.",
        "Monitoring loop started.",
        "Waiting for telemetry..."
    ]
}

def monitor_loop():
    import random
    while True:
        state["cpu"] = random.randint(30, 90)
        state["memory"] = random.randint(40, 85)
        state["latency"] = random.randint(50, 600)
        
        if state["latency"] > 500:
            state["logs"].append(f"WARNING: Latency spiked to {state['latency']}ms on {state['active_provider']}")
            if len(state["logs"]) > 10:
                state["logs"].pop(0)

        time.sleep(2)

def draw_dashboard(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(1)
    
    # Start background loop
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()

    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        
        # Title
        title = " ☁️  SMART MULTI-CLOUD MIGRATOR DASHBOARD ☁️  "
        stdscr.attron(curses.A_BOLD)
        stdscr.addstr(1, (width - len(title)) // 2, title)
        stdscr.attroff(curses.A_BOLD)
        
        # State Panel
        stdscr.addstr(3, 2, "┌── CURRENT STATE ──────────────┐")
        stdscr.addstr(4, 2, f"│ ACTIVE CLOUD : {state['active_provider'].ljust(14)} │")
        stdscr.addstr(5, 2, f"│ CPU LOAD     : {str(state['cpu']) + '%'.ljust(13)} │")
        stdscr.addstr(6, 2, f"│ MEMORY       : {str(state['memory']) + '%'.ljust(13)} │")
        stdscr.addstr(7, 2, f"│ LATENCY (p95): {str(state['latency']) + 'ms'.ljust(11)} │")
        stdscr.addstr(8, 2, "└───────────────────────────────┘")
        
        # Commands Panel
        stdscr.addstr(3, 40, "┌── CONTROLS ───────────────────┐")
        stdscr.addstr(4, 40, "│ [O] Onboard Workload          │")
        stdscr.addstr(5, 40, "│ [M] Force Migrate to Azure    │")
        stdscr.addstr(6, 40, "│ [G] Force Migrate to GCP      │")
        stdscr.addstr(7, 40, "│ [Q] Quit Dashboard            │")
        stdscr.addstr(8, 40, "└───────────────────────────────┘")
        
        # Logs Panel
        stdscr.addstr(10, 2, "── ACTIVITY LOGS " + "─"*(width - 20))
        for i, log in enumerate(state["logs"][-10:]):
            if 12 + i < height - 1:
                stdscr.addstr(12 + i, 2, f"> {log}")
            
        stdscr.refresh()
        
        # Input handling
        try:
            c = stdscr.getch()
            if c != -1:
                ch = chr(c).lower()
                if ch == 'q':
                    break
                elif ch == 'o':
                    state["logs"].append("Initiating Phase 1 Containerization...")
                elif ch == 'm':
                    state["logs"].append(f"Triggering Phase 3.. Migrating {state['active_provider']} -> Azure")
                    state["active_provider"] = "Azure"
                elif ch == 'g':
                    state["logs"].append(f"Triggering Phase 3.. Migrating {state['active_provider']} -> GCP")
                    state["active_provider"] = "GCP"
        except Exception:
            pass

        time.sleep(0.1)

if __name__ == "__main__":
    try:
        curses.wrapper(draw_dashboard)
    except KeyboardInterrupt:
        sys.exit(0)
