# google_calendar.py
from __future__ import print_function
import datetime
import os.path
import time
import threading
from typing import List, Dict
import pytz
import pickle

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/calendar']
LOCAL_TZ = pytz.timezone("America/Chicago")
script_dir = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(script_dir, 'calendar_token.pickle')
CREDENTIALS_PATH = os.path.join(script_dir, 'google_credentials.json')

# GLOBAL LOCK & COOLDOWN
# Prevents multiple threads (e.g. api/calendar + api/upcoming) from trying
# to open port 8081 simultaneously, which causes Errno 98.
_auth_lock = threading.Lock()
_last_auth_attempt_time = 0
AUTH_COOLDOWN_SECONDS = 300  # Don't try to auth more than once every 5 minutes


def _get_calendar_service():
    """Helper function to authenticate and return a Google Calendar service object."""
    global _last_auth_attempt_time

    creds = None
    if os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, 'rb') as token:
                creds = pickle.load(token)
        except Exception as e:
            print(f"[Calendar] Error loading pickle: {e}")
            creds = None

    if creds and creds.valid:
        if not any(s in creds.scopes for s in SCOPES):
            print("[Calendar] Scopes changed. Forcing re-authentication.")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[Calendar] Token refresh failed: {e}")
                creds = None

        if not creds or not creds.valid:
            # --- AUTHENTICATION FLOW START ---

            # 1. Check Cooldown
            if time.time() - _last_auth_attempt_time < AUTH_COOLDOWN_SECONDS:
                print("[Calendar] Auth required but cooldown active. Skipping to prevent log spam.")
                return None

            # 2. Acquire Lock (Non-blocking)
            if _auth_lock.acquire(blocking=False):
                try:
                    _last_auth_attempt_time = time.time()

                    if not os.path.exists(CREDENTIALS_PATH):
                        print("[Calendar] ERROR: google_credentials.json not found.")
                        return None

                    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
                    print("[Calendar] Initiating login sequence (Port 8081)...")

                    try:
                        creds = flow.run_local_server(port=8081, open_browser=False)
                        with open(TOKEN_PATH, 'wb') as token:
                            pickle.dump(creds, token)
                    except Exception as e:
                        print(f"[Calendar] Auth Server Error (Port 8081 busy?): {e}")
                        return None

                except Exception as e:
                    print(f"[Calendar] Authentication failed: {e}")
                    return None
                finally:
                    _auth_lock.release()
            else:
                print("[Calendar] Auth already in progress in another thread.")
                return None
            # --- AUTHENTICATION FLOW END ---

    return build('calendar', 'v3', credentials=creds)


def _format_event_list(events: List[Dict]) -> List[Dict]:
    """Helper to format a list of Google Calendar API event items."""
    event_list = []
    for event in events:
        if 'dateTime' in event['start']:
            event_dt = datetime.datetime.fromisoformat(event['start']['dateTime'])
            if event_dt.tzinfo is None:
                event_dt = LOCAL_TZ.localize(event_dt)
            else:
                event_dt = event_dt.astimezone(LOCAL_TZ)
            event_date = event_dt.strftime('%Y-%m-%d')
            event_time = event_dt.strftime('%H:%M')
        else:
            event_date = event['start']['date']
            event_time = ''
        title = event.get('summary', '(No title)')
        event_list.append({'date': event_date, 'time': event_time, 'title': title})
    return event_list


def get_events_surrounding_days(num_days=2) -> List[Dict]:
    service = _get_calendar_service()
    if not service: return []

    today = datetime.datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - datetime.timedelta(days=num_days)
    end = today + datetime.timedelta(days=num_days + 1)

    timeMin = start.isoformat()
    timeMax = end.isoformat()

    try:
        events_result = service.events().list(
            calendarId='primary', timeMin=timeMin, timeMax=timeMax,
            singleEvents=True, orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        return _format_event_list(events)
    except Exception as e:
        print(f"[Calendar] Fetch failed: {e}")
        return []


def get_upcoming_events(start_day_offset=3, num_days=30) -> List[Dict]:
    service = _get_calendar_service()
    if not service: return []

    today_start_of_day = datetime.datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    start_date = today_start_of_day + datetime.timedelta(days=start_day_offset)
    end_date = start_date + datetime.timedelta(days=num_days)

    timeMin = start_date.isoformat()
    timeMax = end_date.isoformat()

    try:
        events_result = service.events().list(
            calendarId='primary',
            timeMin=timeMin,
            timeMax=timeMax,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        return _format_event_list(events)
    except Exception as e:
        print(f"[Calendar] Upcoming fetch failed: {e}")
        return []


def create_reminder_event(title="Re-auth Robinhood"):
    service = _get_calendar_service()
    if not service:
        print("[Calendar] Cannot create reminder: Service unavailable.")
        return False

    now = datetime.datetime.now(LOCAL_TZ)
    target_start = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if now > target_start:
        target_start += datetime.timedelta(days=1)

    target_end = target_start + datetime.timedelta(minutes=30)

    event_body = {
        'summary': title,
        'description': 'Robinhood authentication failed or timed out. Please run manual login.',
        'start': {
            'dateTime': target_start.isoformat(),
            'timeZone': 'America/Chicago',
        },
        'end': {
            'dateTime': target_end.isoformat(),
            'timeZone': 'America/Chicago',
        },
        'reminders': {
            'useDefault': False,
            'overrides': [{'method': 'popup', 'minutes': 10}],
        },
    }

    try:
        service.events().insert(calendarId='primary', body=event_body).execute()
        print(f"[Calendar] Created reminder event for {target_start}")
        return True
    except Exception as e:
        print(f"[Calendar] Failed to create reminder event: {e}")
        return False


if __name__ == '__main__':
    import json

    print("--- Surrounding Events (for week view) ---")
    print(json.dumps(get_events_surrounding_days(), indent=2))
    print("\n--- Upcoming Events (3-33 days from now) ---")
    print(json.dumps(get_upcoming_events(), indent=2))