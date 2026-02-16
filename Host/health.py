import datetime
import os
import json
import pickle
import io
import time
import threading
from collections import defaultdict
from typing import List, Dict, Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# --- CONFIGURATION ---
TARGET_FOLDER_ID = "1uBEwWRgd4KHCs_YKa-isVGaLzUa3bFr1"
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
METRICS_TO_PROCESS = {
    'apple_exercise_time': {'name': 'Exercise', 'unit': 'min'},
    'resting_heart_rate': {'name': 'Resting HR', 'unit': 'bpm'},
    'active_energy': {'name': 'Calories', 'unit': 'kcal'},
    'step_count': {'name': 'Steps', 'unit': ''}
}

# --- FILE PATHS ---
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HEALTH_HISTORY_FILE = os.path.join(_SCRIPT_DIR, 'health_data.json')
TOKEN_PATH = os.path.join(_SCRIPT_DIR, 'drive_token.pickle')
DRIVE_CREDENTIALS_PATH = os.path.join(_SCRIPT_DIR, 'drive_credentials.json')
CALENDAR_CREDENTIALS_PATH = os.path.join(_SCRIPT_DIR, 'google_credentials.json')

# --- CONSTANTS ---
HISTORY_DAYS_TO_KEEP = 14

# GLOBAL LOCK & COOLDOWN for Health Auth
_health_auth_lock = threading.Lock()
_last_health_auth_time = 0
AUTH_COOLDOWN_SECONDS = 300  # 5 minutes


def _get_drive_service():
    """Authenticates with Google and returns a Drive API service object."""
    global _last_health_auth_time
    creds = None
    if os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, 'rb') as token:
                creds = pickle.load(token)
        except Exception as e:
            print(f"[Health] Error loading pickle: {e}")
            creds = None

    if not creds or not creds.valid or not all(s in creds.scopes for s in SCOPES):
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.scopes = list(set(SCOPES + creds.scopes))
                creds.refresh(Request())
            except Exception as e:
                print(f"[Health] Token refresh failed: {e}")
                creds = None

        if not creds or not creds.valid:
            # --- AUTHENTICATION FLOW START ---
            if time.time() - _last_health_auth_time < AUTH_COOLDOWN_SECONDS:
                print("[Health] Auth required but cooldown active. Skipping.")
                return None

            if _health_auth_lock.acquire(blocking=False):
                try:
                    _last_health_auth_time = time.time()
                    credentials_file = DRIVE_CREDENTIALS_PATH if os.path.exists(
                        DRIVE_CREDENTIALS_PATH) else CALENDAR_CREDENTIALS_PATH

                    if not os.path.exists(credentials_file):
                        print(f"[Health] ERROR: Credentials file not found.")
                        return None

                    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
                    print("[Health] Initiating login sequence (Port 8081)...")

                    try:
                        creds = flow.run_local_server(port=8081, open_browser=False)
                        with open(TOKEN_PATH, 'wb') as token:
                            pickle.dump(creds, token)
                    except Exception as e:
                        print(f"[Health] Auth Server Error (Port 8081 busy?): {e}")
                        return None

                except Exception as e:
                    print(f"[Health] Auth failed: {e}")
                    return None
                finally:
                    _health_auth_lock.release()
            else:
                print("[Health] Auth already in progress in another thread.")
                return None
            # --- AUTHENTICATION FLOW END ---

    return build('drive', 'v3', credentials=creds)


def _find_and_download_latest_file(service: Any) -> str:
    """Finds the most recent health export file and returns its content as a string."""
    if not TARGET_FOLDER_ID or "YOUR_FOLDER_ID" in TARGET_FOLDER_ID:
        raise ValueError("TARGET_FOLDER_ID is not set.")

    query = f"'{TARGET_FOLDER_ID}' in parents and name contains 'HealthAutoExport-'"
    try:
        response = service.files().list(
            q=query, corpora="user", includeItemsFromAllDrives=True,
            supportsAllDrives=True, spaces='drive', fields='files(id, name)',
            orderBy='name desc', pageSize=1
        ).execute()

        files = response.get('files', [])
        if not files:
            # Silent fail is better than crash for dashboard
            return None

        file_id = files[0]['id']
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        return fh.getvalue().decode('utf-8')
    except Exception as e:
        print(f"[Health] Download failed: {e}")
        return None


def _parse_health_data(json_content: str) -> Dict[str, Dict[str, float]]:
    if not json_content: return {}
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

    for date, metrics in new_data.items():
        history_dict[date] = metrics

    updated_history = [
        {'date': date, 'metrics': metrics}
        for date, metrics in history_dict.items()
    ]

    updated_history.sort(key=lambda x: x['date'], reverse=True)
    updated_history = updated_history[:HISTORY_DAYS_TO_KEEP]

    with open(HEALTH_HISTORY_FILE, 'w') as f:
        json.dump(updated_history, f, indent=2)

    return updated_history


def _calculate_weekly_summary(history: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
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

        current_avg = sum(current_week_values) / len(current_week_values) if current_week_values else 0.0
        last_avg = sum(last_week_values) / len(last_week_values) if last_week_values else 0.0

        change_pct = 0.0
        if last_avg > 0:
            change_pct = ((current_avg - last_avg) / last_avg) * 100.0
        elif current_avg > 0:
            change_pct = 100.0

        summary[metric_key] = {
            "name": config['name'],
            "unit": config['unit'],
            "avg": current_avg,
            "change": change_pct
        }
    return summary


def get_weekly_health_summary() -> Dict[str, Dict[str, Any]]:
    service = _get_drive_service()

    # If service unavailable, try to serve from history cache
    if not service:
        history = []
        if os.path.exists(HEALTH_HISTORY_FILE):
            try:
                with open(HEALTH_HISTORY_FILE, 'r') as f:
                    history = json.load(f)
            except:
                pass
        return _calculate_weekly_summary(history)

    json_content = _find_and_download_latest_file(service)
    newly_parsed_data = _parse_health_data(json_content)

    if not newly_parsed_data:
        history = []
        if os.path.exists(HEALTH_HISTORY_FILE):
            try:
                with open(HEALTH_HISTORY_FILE, 'r') as f:
                    history = json.load(f)
            except:
                pass
        return _calculate_weekly_summary(history)

    updated_history = _update_and_save_history(newly_parsed_data)

    return _calculate_weekly_summary(updated_history)