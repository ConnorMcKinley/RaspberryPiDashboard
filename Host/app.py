#!/usr/bin/env python3
# app.py - Main Flask app for Raspberry Pi Dashboard
import os, json, threading, time, argparse, sys
from datetime import datetime, date, timedelta
import schedule
from flask import Flask, jsonify, render_template, request

# Import modules
from fidelity import FidelityAutomation
from robinhood import get_robinhood_positions
from weather import get_chicago_weekly
from google_calendar import get_events_surrounding_days, get_upcoming_events
from health import get_weekly_health_summary
from news import get_political_news

CONFIG = "config.json"
STATE = "state.json"


def load_cfg():
    with open(CONFIG) as f:
        return json.load(f)


def save_state():
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)


def manual_login_flow():
    print("Launching browser for manual login. Please log in and complete any 2FA if required.")
    cfg = config["fidelity"]
    bot = FidelityAutomation(headless=False, debug=False, save_state=True)
    bot.page.goto("https://digital.fidelity.com/prgw/digital/login/full-page", timeout=600000)
    input("After logging in and seeing your account summary, press Enter here to save session and exit...")
    bot.save_storage_state()
    bot.close_browser()
    print("Login session saved. Future runs will use this session and avoid 2FA where possible.")
    sys.exit(0)


def fetch_net_worth():
    print("[fetch] Updating account balances and details…")
    try:
        cfg = config["fidelity"]
        rh_cfg = config.get("robinhood", {})

        # 1. Fidelity
        bot = FidelityAutomation(headless=True, debug=False, save_state=True)
        need_pw, need_2fa = bot.login(cfg["username"], cfg["password"], save_device=True,
                                      totp_secret=cfg.get("totp_secret"))
        if not need_pw and not need_2fa:
            print("Password error")
            raise Exception("Fidelity password error")
        if not need_2fa:
            code = input("Enter 2-factor code: ")
            bot.login_2FA(code)

        # Get detailed portfolio structure
        fidelity_data = bot.get_detailed_portfolio()
        bot.close_browser()

        if not fidelity_data or fidelity_data.get('total_net_worth') == 0:
            print("Warning: Fidelity data seems empty")

        # 2. Robinhood
        rh_data = None
        if rh_cfg and rh_cfg.get("username"):
            rh_data = get_robinhood_positions(
                rh_cfg["username"], rh_cfg["password"], rh_cfg["totp_secret"]
            )

        # Combine Totals
        total_nw = fidelity_data.get('total_net_worth', 0.0)
        if rh_data:
            total_nw += rh_data.get('equity', 0.0)
            print(f"[fetch] Robinhood: {rh_data.get('equity'):,.2f} added")

        # Build Portfolio Details Object for State
        portfolio_details = {
            "total_value": total_nw,
            "fidelity": fidelity_data.get("fidelity_accounts", []),
            "non_fidelity": fidelity_data.get("non_fidelity_accounts", []),
            "robinhood": rh_data.get("positions", []) if rh_data else []
        }

        # Update State logic (History etc)
        today_iso = date.today().isoformat()
        net_worth_from_last_run = state.get("net_worth")

        if state.get("stamp") != today_iso:
            if net_worth_from_last_run is not None:
                state["yesterday"] = net_worth_from_last_run
            else:
                # Fallback if no yesterday, just use current
                state["yesterday"] = total_nw
        elif state.get("yesterday") is None:
            state["yesterday"] = total_nw

        state["net_worth"] = total_nw
        state["portfolio_details"] = portfolio_details  # Save detailed structure
        state["last_updated"] = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        state["error"] = False
        state["stamp"] = today_iso
        save_state()

        yesterday_nw_for_log = state.get("yesterday")
        actual_delta_for_log = total_nw - (yesterday_nw_for_log if yesterday_nw_for_log else total_nw)

        print(f"[fetch] Success – Net Worth ${total_nw:,.2f} (Δ {actual_delta_for_log:+,.2f} vs yesterday)")

    except Exception as e:
        print(f"Fetch error: {e}")
        # traceback.print_exc() # Optional: import traceback if needed
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
    portfolio_details=None
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
    parser.add_argument('--manual-login', action='store_true', help="Run browser for manual login and save session")
    return parser.parse_args()


args = parse_args()

if args.manual_login:
    manual_login_flow()


def periodic_update():
    """Combined update job."""
    print("[periodic_update] Running combined update...")
    update_weather_state()
    update_health_state()
    fetch_net_worth()


print("[startup] Performing initial data fetch...")
# Uncommented to ensure data is preloaded on server start
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
    return render_template("dashboard.html")


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
        details=state.get("portfolio_details")  # Send detailed data
    )


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
        # Fetch events starting from 3 days from now, for the next 30 days.
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