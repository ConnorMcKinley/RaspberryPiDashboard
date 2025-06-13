# google_calendar.py
from __future__ import print_function
import datetime
import os.path
from typing import List, Dict
import pytz

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
LOCAL_TZ = pytz.timezone("America/Chicago")

# --- MODIFICATION START ---
# Get the absolute path of the directory where this script is located
script_dir = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(script_dir, 'token.pickle')
CREDENTIALS_PATH = os.path.join(script_dir, 'calendar_credentials.json')


# --- MODIFICATION END ---


def get_events_surrounding_days(num_days=2) -> List[Dict]:
    """
    Returns calendar events for today ±num_days (default: 2).
    Each event is a dict: {'date': 'YYYY-MM-DD', 'time': 'HH:MM', 'title': 'Event Title'}
    Dates/times are always LOCAL to America/Chicago.
    """
    creds = None
    # Use the absolute path for token.pickle
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Use the absolute path for calendar_credentials.json
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=8080)
        # Save the credentials for the next run
        with open(TOKEN_PATH, 'wb') as token:
            pickle.dump(creds, token)

    service = build('calendar', 'v3', credentials=creds)

    # Set up local midnight boundaries
    today = datetime.datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - datetime.timedelta(days=num_days)
    end = today + datetime.timedelta(days=num_days + 1)  # up to midnight after the last day

    # Convert to RFC3339 timestamp
    timeMin = start.isoformat()
    timeMax = end.isoformat()

    events_result = service.events().list(
        calendarId='primary',
        timeMin=timeMin,
        timeMax=timeMax,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    event_list = []
    for event in events:
        # Handle all-day events and timed events
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


if __name__ == '__main__':
    import json

    print(json.dumps(get_events_surrounding_days(), indent=2))