#!/usr/bin/env python3
# app.py - Main Flask app for Raspberry Pi Dashboard
import os, json, threading, time, argparse, sys, glob
from datetime import datetime, date, timedelta
import schedule
from flask import Flask, jsonify, render_template, request

# Import modules
from fidelity import FidelityAutomation
from robinhood import get_robinhood_positions
from weather import get_chicago_weekly
from google_calendar import get_events_surrounding_days, get_upcoming_events, create_reminder_event
from health import get_weekly_health_summary
from news import get_political_news

CONFIG = "config.json"
STATE = "state.json"


def load_cfg():
    if not os.path.exists(CONFIG):
        print(f"[config] Warning: {CONFIG} not found.")
        return {}
    with open(CONFIG) as f:
        return json.load(f)


def save_state():
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)


def manual_login_flow():
    print("Launching browser for manual FIDELITY login...")
    cfg = config.get("fidelity", {})
    if not cfg:
        print("Error: Fidelity config missing.")
        return
    bot = FidelityAutomation(headless=False, debug=False, save_state=True)
    bot.page.goto("https://digital.fidelity.com/prgw/digital/login/full-page", timeout=600000)
    input("After logging in and seeing your account summary, press Enter here to save session and exit...")
    bot.save_storage_state()
    bot.close_browser()
    print("Fidelity session saved.")
    sys.exit(0)


def setup_robinhood_flow():
    print("\n--- ROBINHOOD INTERACTIVE SETUP ---")
    print("This will log in once to generate a valid session file (pickle).")
    print("Please have your phone ready for the SMS code.\n")

    cfg = config.get("robinhood", {})
    if not cfg or not cfg.get("username"):
        print("Error: Robinhood config missing in config.json")
        return

    # Force a login attempt
    try:
        data = get_robinhood_positions(cfg["username"], cfg["password"], cfg["totp_secret"])
        if data:
            print("\nSUCCESS: Robinhood login successful. Session saved.")
            print(f"Current Equity: ${data.get('equity', 0)}")
        else:
            print("\nFAILURE: Login returned no data.")
    except Exception as e:
        print(f"\nERROR: {e}")

    sys.exit(0)


def clean_pickles():
    """Deletes authentication pickle files to force re-login."""
    print("[Clean] Searching for pickle files to remove...")

    files_to_check = [
        "calendar_token.pickle",
        "drive_token.pickle",
        "robinhood.pickle"
    ]
    files_to_check.extend(glob.glob("*.pickle"))

    script_dir = os.path.dirname(os.path.abspath(__file__))
    removed = False

    for filename in files_to_check:
        filepath = os.path.join(script_dir, filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                print(f"[Clean] Removed: {filename}")
                removed = True
            except Exception as e:
                print(f"[Clean] Failed to remove {filepath}: {e}")
        elif os.path.exists(filename):
            try:
                os.remove(filename)
                print(f"[Clean] Removed: {filename}")
                removed = True
            except Exception as e:
                print(f"[Clean] Failed to remove {filename}: {e}")

    if not removed:
        print("[Clean] No pickle files found.")
    else:
        print("[Clean] Cleanup complete. Please run --setup-robinhood to re-auth.")
    sys.exit(0)


# --- ROBINHOOD SAFETY NET ---
def check_rh_limits():
    """Returns True if we are allowed to attempt Robinhood auth."""
    now = datetime.now()

    # 1. Check for Hard Lockout (caused by 429s)
    lockout_str = state.get("rh_lockout_until")
    if lockout_str:
        try:
            lockout_dt = datetime.fromisoformat(lockout_str)
            if now < lockout_dt:
                remaining = int((lockout_dt - now).total_seconds() / 60)
                print(f"[RH Safety] LOCKED OUT due to previous 429 errors. Resuming in {remaining} mins.")
                return False
            else:
                # Lockout expired, clear it
                print("[RH Safety] Lockout expired. Resuming attempts.")
                state["rh_lockout_until"] = None
        except ValueError:
            state["rh_lockout_until"] = None

    # 2. Check Frequency Limits
    timestamps = state.get("rh_timestamps", [])
    try:
        dt_timestamps = [datetime.fromisoformat(t) for t in timestamps]
    except ValueError:
        dt_timestamps = []

    last_hour = [t for t in dt_timestamps if (now - t).total_seconds() < 3600]
    last_day = [t for t in dt_timestamps if (now - t).total_seconds() < 86400]

    state["rh_timestamps"] = [t.isoformat() for t in last_day]
    save_state()

    if len(last_hour) >= 2:
        print(f"[RH Safety] Hourly limit reached ({len(last_hour)}/2). Skipping.")
        return False
    if len(last_day) >= 10:  # Increased daily slightly, but hourly is strict
        print(f"[RH Safety] Daily limit reached ({len(last_day)}/10). Skipping.")
        return False

    return True


def record_rh_attempt():
    """Records a new Robinhood authentication attempt."""
    if "rh_timestamps" not in state:
        state["rh_timestamps"] = []
    state["rh_timestamps"].append(datetime.now().isoformat())
    save_state()


def activate_rh_lockout():
    """Locks Robinhood attempts for 24 hours."""
    lock_until = datetime.now() + timedelta(hours=24)
    state["rh_lockout_until"] = lock_until.isoformat()
    state["robinhood_error"] = True
    save_state()
    print(f"[RH Safety] ⛔ CRITICAL: 429 Rate Limit detected. Locking Robinhood for 24 hours (until {lock_until}).")
    create_reminder_event("RH Rate Limited (24h)")


def fetch_net_worth():
    print("[fetch] Updating account balances and details…")
    try:
        cfg = config.get("fidelity", {})
        rh_cfg = config.get("robinhood", {})

        fidelity_data = {}
        if not cfg:
            print("[fetch] Skipping Fidelity (no config)")
        else:
            try:
                bot = FidelityAutomation(headless=True, debug=False, save_state=True)
                need_pw, need_2fa = bot.login(cfg["username"], cfg["password"], save_device=True,
                                              totp_secret=cfg.get("totp_secret"))
                if not need_pw and not need_2fa:
                    raise Exception("Fidelity password error")
                if not need_2fa:
                    print("[fetch] Fidelity requesting manual 2FA in headless mode - skipping.")
                else:
                    fidelity_data = bot.get_detailed_portfolio()
                bot.close_browser()
            except Exception as e:
                print(f"[fetch] Fidelity Error: {e}")
                pass

        # 2. Robinhood (With Strict Safety Net)
        rh_data = None
        rh_positions = []

        should_attempt_rh = False
        if rh_cfg and rh_cfg.get("username"):
            should_attempt_rh = True

        if should_attempt_rh and check_rh_limits():
            print("[fetch] Attempting Robinhood login...")
            record_rh_attempt()
            try:
                rh_data = get_robinhood_positions(
                    rh_cfg["username"], rh_cfg["password"], rh_cfg["totp_secret"]
                )
                if rh_data:
                    rh_positions = rh_data.get("positions", [])
                    print(f"[fetch] Robinhood: {rh_data.get('equity'):,.2f} added")
                    state["robinhood_error"] = False
                else:
                    print("[fetch] Robinhood returned no data.")
                    state["robinhood_error"] = True

            except Exception as e:
                err_str = str(e)
                print(f"[fetch] Robinhood Failed: {err_str}")

                # Check for 429 in the exception message or type
                if "429" in err_str or "Too Many Requests" in err_str:
                    activate_rh_lockout()
                else:
                    state["robinhood_error"] = True

        elif should_attempt_rh:
            print("[fetch] Robinhood skipped due to limits/lockout.")
            # Keep error visible
            if state.get("robinhood_error") is None:
                state["robinhood_error"] = True

        # Combine Totals
        total_nw = fidelity_data.get('total_net_worth', 0.0)
        if rh_data:
            total_nw += rh_data.get('equity', 0.0)

        portfolio_details = {
            "total_value": total_nw,
            "fidelity": fidelity_data.get("fidelity_accounts", []),
            "non_fidelity": fidelity_data.get("non_fidelity_accounts", []),
            "robinhood": rh_positions
        }

        today_iso = date.today().isoformat()
        net_worth_from_last_run = state.get("net_worth")

        if state.get("stamp") != today_iso:
            if net_worth_from_last_run is not None:
                state["yesterday"] = net_worth_from_last_run
            else:
                state["yesterday"] = total_nw
        elif state.get("yesterday") is None:
            state["yesterday"] = total_nw

        state["net_worth"] = total_nw
        state["portfolio_details"] = portfolio_details
        state["last_updated"] = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        state["error"] = False
        state["stamp"] = today_iso
        save_state()

        yesterday_nw_for_log = state.get("yesterday")
        actual_delta_for_log = total_nw - (yesterday_nw_for_log if yesterday_nw_for_log else total_nw)

        print(f"[fetch] Success – Net Worth ${total_nw:,.2f} (Δ {actual_delta_for_log:+,.2f} vs yesterday)")

    except Exception as e:
        print(f"Fetch error: {e}")
        state["error"] = True
        state["last_updated"] = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        save_state()


def update_health_state():
    print("[health_state] Updating health stats...")
    try:
        health_summary = get_weekly_health_summary()
        state["health_stats"] = health_summary
        save_state()
        print("[health_state] Successfully updated health stats.")
    except Exception as e:
        print(f"[health_state] Error updating health state: {e}")
        state["health_stats"] = None
        save_state()


def reschedule():
    schedule.clear()
    for hr in config.get("refresh_hours", []):
        schedule.every().day.at(f"{int(hr):02d}:00").do(fetch_net_worth)
    schedule.every(1).hours.do(update_weather_state)
    schedule.every(6).hours.do(periodic_update)
    print("[scheduler] Jobs:", schedule.jobs)


def update_weather_state():
    print("[weather_state] Updating weather state...")
    try:
        new_7day_forecast_data = get_chicago_weekly()
        if not new_7day_forecast_data: return

        today_iso = date.today().isoformat()
        state["weather_forecast"] = new_7day_forecast_data
        state["weather_stamp"] = today_iso
        save_state()
    except Exception as e:
        print(f"[weather_state] Error: {e}")


config = load_cfg()
default_state = dict(
    net_worth=None,
    yesterday=None,
    last_updated=None,
    error=False,
    stamp=None,
    weather_history=[None, None],
    weather_forecast=[],
    weather_stamp=None,
    health_stats=None,
    portfolio_details=None,
    battery=None,
    rh_timestamps=[],
    rh_lockout_until=None,
    rh_last_reminder_date=None,
    robinhood_error=False
)
state = default_state.copy()

if os.path.exists(STATE):
    try:
        with open(STATE) as f:
            loaded_state = json.load(f)
        for key, default_value in default_state.items():
            state[key] = loaded_state.get(key, default_value)
    except Exception as e:
        print(f"[State Error] Failed to load {STATE}: {e}")


def parse_args():
    parser = argparse.ArgumentParser(description="Pi Dashboard App")
    parser.add_argument('--manual-login', action='store_true', help="Run browser for Fidelity manual login")
    parser.add_argument('--setup-robinhood', action='store_true',
                        help="Run interactive Robinhood login to fix 2FA/Pickle errors")
    parser.add_argument('--clean', action='store_true', help="Delete authentication pickle files to fix token errors")
    return parser.parse_args()


args = parse_args()

if args.clean:
    clean_pickles()

if args.manual_login:
    manual_login_flow()

if args.setup_robinhood:
    setup_robinhood_flow()


def periodic_update():
    """Combined update job."""
    print("[periodic_update] Running combined update...")
    update_weather_state()
    update_health_state()
    fetch_net_worth()


print("[startup] Performing initial data fetch...")
periodic_update()

reschedule()


def scheduler_loop():
    while True:
        schedule.run_pending()
        time.sleep(30)


threading.Thread(target=scheduler_loop, daemon=True).start()

app = Flask(__name__, static_url_path="/static")


@app.route("/")
def home():
    mode = request.args.get('mode', 'grayscale')
    return render_template("dashboard.html", mode=mode)


@app.route("/api/data")
def api_data():
    current_net_worth = state.get("net_worth")
    yesterday_net_worth = state.get("yesterday")
    delta_to_show = None
    if current_net_worth is not None and yesterday_net_worth is not None:
        try:
            delta_to_show = float(current_net_worth) - float(yesterday_net_worth)
        except (ValueError, TypeError):
            delta_to_show = None

    return jsonify(
        net_worth=current_net_worth,
        change=delta_to_show,
        last_updated=state.get("last_updated"),
        error=state.get("error", False),
        robinhood_error=state.get("robinhood_error", False),
        details=state.get("portfolio_details"),
        battery=state.get("battery")
    )


@app.route("/api/battery", methods=["POST"])
def api_battery():
    data = request.json
    if not data or "level" not in data:
        return jsonify({"error": "Missing 'level' in payload"}), 400

    try:
        level = int(data["level"])
        state["battery"] = level
        print(f"[battery] Updated battery level to {level}%")
        return jsonify({"status": "ok", "level": level})
    except ValueError:
        return jsonify({"error": "Invalid battery level format"}), 400


@app.route("/api/weather")
def api_weather():
    try:
        days_to_display_on_dashboard = [None] * 5
        history = state.get("weather_history", [None, None])
        days_to_display_on_dashboard[0] = history[0]
        days_to_display_on_dashboard[1] = history[1]
        current_7day_forecast = state.get("weather_forecast", [])
        today_iso = date.today().isoformat()
        forecast_start_index_for_today = 0

        if current_7day_forecast and current_7day_forecast[0] and current_7day_forecast[0].get("date") != today_iso:
            for idx, day_data in enumerate(current_7day_forecast):
                if day_data and day_data.get("date") == today_iso:
                    forecast_start_index_for_today = idx
                    break

        for i in range(3):
            forecast_idx_in_7day_list = forecast_start_index_for_today + i
            if forecast_idx_in_7day_list < len(current_7day_forecast):
                days_to_display_on_dashboard[2 + i] = current_7day_forecast[forecast_idx_in_7day_list]

        return jsonify({"forecast": days_to_display_on_dashboard})
    except Exception as e:
        print(f"[api/weather] API error: {e}")
        return jsonify({"error": "Could not construct weather view", "forecast": [None] * 5}), 500


@app.route("/api/calendar")
def api_calendar():
    try:
        events = get_events_surrounding_days(num_days=2)
        return jsonify({"events": events})
    except Exception as e:
        print(f"[calendar] API error: {e}")
        return jsonify({"error": "Could not fetch calendar events"}), 500


@app.route("/api/upcoming_events")
def api_upcoming_events():
    try:
        events = get_upcoming_events(start_day_offset=3, num_days=30)
        return jsonify({"events": events})
    except Exception as e:
        print(f"[upcoming_events] API error: {e}")
        return jsonify({"error": "Could not fetch upcoming events"}), 500


@app.route("/api/health")
def api_health():
    health_data = state.get("health_stats")
    if health_data:
        return jsonify(health_data)
    else:
        return jsonify(None)


@app.route("/api/news")
def api_news():
    try:
        # Get 4-5 news items
        news_items = get_political_news(limit=5)
        return jsonify({"news": news_items})
    except Exception as e:
        print(f"[news] API error: {e}")
        return jsonify({"error": "Could not fetch news"}), 500


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    hrs = request.json.get("hours")
    if not (isinstance(hrs, list) and all(isinstance(h, int) and 0 <= h < 24 for h in hrs)):
        return jsonify({"error": "hours must be list of ints 0-23"}), 400
    config["refresh_hours"] = sorted(list(set(hrs)))
    try:
        with open(CONFIG, "w") as f:
            json.dump(config, f, indent=2)
    except IOError as e:
        return jsonify({"error": "Failed to save config"}), 500
    reschedule()
    return jsonify({"status": "ok", "new_schedule": schedule.jobs})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)