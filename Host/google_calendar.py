# google_calendar.py
from __future__ import print_function
import datetime
import os.path
from typing import List, Dict
import pytz
import pickle

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
LOCAL_TZ = pytz.timezone("America/Chicago")
script_dir = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(script_dir, 'calendar_token.pickle')
CREDENTIALS_PATH = os.path.join(script_dir, 'google_credentials.json')


def _get_calendar_service():
    """Helper function to authenticate and return a Google Calendar service object."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, 'rb') as token:
                creds = pickle.load(token)
        except Exception as e:
            print(f"[Calendar] Error loading pickle, will regenerate: {e}")
            creds = None

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[Calendar] Token refresh failed: {e}")
                creds = None  # Force re-auth

        if not creds or not creds.valid:
            if not os.path.exists(CREDENTIALS_PATH):
                print("[Calendar] ERROR: google_credentials.json not found.")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            # open_browser=False prevents tabs from spamming the server
            print("[Calendar] Initiating login sequence. If prompted, please visit the URL below.")
            creds = flow.run_local_server(port=8080, open_browser=False)

        with open(TOKEN_PATH, 'wb') as token:
            pickle.dump(creds, token)

    return build('calendar', 'v3', credentials=creds)


def _format_event_list(events: List[Dict]) -> List[Dict]:
    """Helper to format a list of Google Calendar API event items."""
    event_list = []
    for event in events:
        if 'dateTime' in event['start']:
            # Timed event
            event_dt = datetime.datetime.fromisoformat(event['start']['dateTime'])
            if event_dt.tzinfo is None:
                event_dt = LOCAL_TZ.localize(event_dt)
            else:
                event_dt = event_dt.astimezone(LOCAL_TZ)
            event_date = event_dt.strftime('%Y-%m-%d')
            event_time = event_dt.strftime('%H:%M')
        else:
            # All-day event
            event_date = event['start']['date']
            event_time = ''
        title = event.get('summary', '(No title)')
        event_list.append({'date': event_date, 'time': event_time, 'title': title})
    return event_list


def get_events_surrounding_days(num_days=2) -> List[Dict]:
    """
    Returns calendar events for today ±num_days (default: 2).
    """
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
    """
    Returns calendar events for a future range, starting from an offset.
    """
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


if __name__ == '__main__':
    import json

    print("--- Surrounding Events (for week view) ---")
    print(json.dumps(get_events_surrounding_days(), indent=2))
    print("\n--- Upcoming Events (3-33 days from now) ---")
    print(json.dumps(get_upcoming_events(), indent=2))