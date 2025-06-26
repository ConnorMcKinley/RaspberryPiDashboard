import datetime
import os
import json
import pickle
import io
from collections import defaultdict
from typing import List, Dict, Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# --- CONFIGURATION ---

# The ID of the Google Drive folder containing your health exports.
# To find this, open the folder in your browser and copy the last part of the URL.
# Example URL: https://drive.google.com/drive/folders/1aBcDeFgHiJkLmNoPqRsTuVwXyZ_12345
# Example ID: 1aBcDeFgHiJkLmNoPqRsTuVwXyZ_12345
TARGET_FOLDER_ID = "1uBEwWRgd4KHCs_YKa-isVGaLzUa3bFr1"

# The scopes required for the Google Drive API.
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Define the metrics to track. The keys must match the 'name' in the JSON file.
METRICS_TO_PROCESS = {
    'apple_exercise_time': {'name': 'Exercise', 'unit': 'min'},
    'resting_heart_rate': {'name': 'Resting HR', 'unit': 'bpm'},
    'active_energy': {'name': 'Calories', 'unit': 'kcal'},
    'step_count': {'name': 'Steps', 'unit': ''}
}

# --- FILE PATHS ---
# These paths are relative to the script's location.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HEALTH_HISTORY_FILE = os.path.join(_SCRIPT_DIR, 'health_data.json')
TOKEN_PATH = os.path.join(_SCRIPT_DIR, 'drive_token.pickle')
# Assumes you have one of these credentials files in the same directory.
DRIVE_CREDENTIALS_PATH = os.path.join(_SCRIPT_DIR, 'drive_credentials.json')
CALENDAR_CREDENTIALS_PATH = os.path.join(_SCRIPT_DIR, 'google_credentials.json')

# --- CONSTANTS ---
HISTORY_DAYS_TO_KEEP = 14


def _get_drive_service():
    """Authenticates with Google and returns a Drive API service object."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid or not all(s in creds.scopes for s in SCOPES):
        if creds and creds.expired and creds.refresh_token:
            creds.scopes = list(set(SCOPES + creds.scopes))
            creds.refresh(Request())
        else:
            credentials_file = DRIVE_CREDENTIALS_PATH if os.path.exists(
                DRIVE_CREDENTIALS_PATH) else CALENDAR_CREDENTIALS_PATH
            if not os.path.exists(credentials_file):
                raise FileNotFoundError(
                    f"Google credentials not found. Please place 'drive_credentials.json' or 'google_credentials.json' in the script directory.")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=8080)

        with open(TOKEN_PATH, 'wb') as token:
            pickle.dump(creds, token)

    return build('drive', 'v3', credentials=creds)


def _find_and_download_latest_file(service: Any) -> str:
    """Finds the most recent health export file and returns its content as a string."""
    if not TARGET_FOLDER_ID or "YOUR_FOLDER_ID" in TARGET_FOLDER_ID:
        raise ValueError("TARGET_FOLDER_ID is not set. Please update it in health.py.")

    query = f"'{TARGET_FOLDER_ID}' in parents and name contains 'HealthAutoExport-'"
    response = service.files().list(
        q=query, corpora="user", includeItemsFromAllDrives=True,
        supportsAllDrives=True, spaces='drive', fields='files(id, name)',
        orderBy='name desc', pageSize=1
    ).execute()

    files = response.get('files', [])
    if not files:
        raise FileNotFoundError(f"No 'HealthAutoExport-*' files found in the specified Google Drive folder.")

    file_id = files[0]['id']
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return fh.getvalue().decode('utf-8')


def _parse_health_data(json_content: str) -> Dict[str, Dict[str, float]]:
    try:
        data = json.loads(json_content)
        metrics_list = data.get('data', {}).get('metrics', [])
    except (json.JSONDecodeError, AttributeError):
        return {}

    points_by_day = defaultdict(lambda: defaultdict(list))

    for metric_obj in metrics_list:
        metric_name = metric_obj.get('name')
        if metric_name not in METRICS_TO_PROCESS:
            continue

        for point in metric_obj.get('data', []):
            try:
                iso_date = datetime.datetime.strptime(
                    point['date'], '%Y-%m-%d %H:%M:%S %z'
                ).date().isoformat()
                qty = float(point['qty'])
                points_by_day[iso_date][metric_name].append(qty)
            except (KeyError, IndexError, ValueError, TypeError):
                continue

    final_daily_metrics = {}
    for date, metrics in points_by_day.items():
        day_totals = {}
        for metric_name, values in metrics.items():
            if not values:
                continue
            if metric_name == 'resting_heart_rate':
                day_totals[metric_name] = values[-1]
            else:
                day_totals[metric_name] = round(sum(values), 2)
        if day_totals:
            final_daily_metrics[date] = day_totals

    return final_daily_metrics


def _update_and_save_history(new_data: Dict[str, Dict[str, float]]) -> List[Dict[str, Any]]:
    history = []
    if os.path.exists(HEALTH_HISTORY_FILE):
        try:
            with open(HEALTH_HISTORY_FILE, 'r') as f:
                history = json.load(f)
        except json.JSONDecodeError:
            history = []

    history_dict = {item['date']: item['metrics'] for item in history}

    # Merge new data correctly
    for date, metrics in new_data.items():
        history_dict[date] = metrics

    updated_history = [
        {'date': date, 'metrics': metrics}
        for date, metrics in history_dict.items()
    ]

    # Sort by date (most recent first) and limit to desired length
    updated_history.sort(key=lambda x: x['date'], reverse=True)
    updated_history = updated_history[:HISTORY_DAYS_TO_KEEP]

    with open(HEALTH_HISTORY_FILE, 'w') as f:
        json.dump(updated_history, f, indent=2)

    return updated_history



def _calculate_weekly_summary(history: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Calculates 7-day averages and the change from the previous 7 days.

    Args:
        history: A sorted list of daily health data for the last 14 days.

    Returns:
        A dictionary containing the summary for each metric.
    """
    today = datetime.date.today()
    summary = {}

    for metric_key, config in METRICS_TO_PROCESS.items():
        current_week_values = []
        last_week_values = []

        for item in history:
            try:
                item_date = datetime.datetime.strptime(item['date'], '%Y-%m-%d').date()
                days_ago = (today - item_date).days
                value = item['metrics'].get(metric_key, 0.0)

                if 0 <= days_ago < 7:
                    current_week_values.append(value)
                elif 7 <= days_ago < 14:
                    last_week_values.append(value)
            except (KeyError, ValueError):
                continue

        # Calculate averages. If no data, average is 0.
        current_avg = sum(current_week_values) / len(current_week_values) if current_week_values else 0.0
        last_avg = sum(last_week_values) / len(last_week_values) if last_week_values else 0.0

        # Calculate percentage change, handling division by zero.
        change_pct = 0.0
        if last_avg > 0:
            change_pct = ((current_avg - last_avg) / last_avg) * 100.0
        elif current_avg > 0:
            change_pct = 100.0  # From 0 to a positive number is a 100% increase.

        summary[metric_key] = {
            "name": config['name'],
            "unit": config['unit'],
            "avg": current_avg,
            "change": change_pct
        }
    return summary


def get_weekly_health_summary() -> Dict[str, Dict[str, Any]]:
    """
    The main public function to orchestrate the entire health data update process.

    It fetches the latest data from Google Drive, updates the local history,
    and returns a calculated weekly summary.

    Returns:
        A dictionary containing the weekly summary for all tracked metrics.
    """
    service = _get_drive_service()
    json_content = _find_and_download_latest_file(service)
    newly_parsed_data = _parse_health_data(json_content)

    if not newly_parsed_data:
        # If parsing fails or yields no data, load old history and calculate from that.
        history = []
        if os.path.exists(HEALTH_HISTORY_FILE):
            with open(HEALTH_HISTORY_FILE, 'r') as f:
                history = json.load(f)
        return _calculate_weekly_summary(history)

    updated_history = _update_and_save_history(newly_parsed_data)

    return _calculate_weekly_summary(updated_history)