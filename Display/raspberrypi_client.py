#!/usr/bin/python3
import time
import subprocess
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from PIL import Image, ImageOps
import io
import os
import sys

# === Hardware communication imports (for Raspberry Pi) ===
try:
    import spidev
    import RPi.GPIO as GPIO

    print("[client] RPi.GPIO and spidev loaded.")
except ImportError:
    print("[client] WARN: RPi.GPIO or spidev not found. Sleep/MOSFET/hardware functions will not work.")
    spidev = None
    GPIO = None

# === PiJuice Import ===
try:
    from pijuice import PiJuice

    # Standard I2C address is 0x14
    pijuice = PiJuice(1, 0x14)
    print("[client] PiJuice loaded.")
except ImportError:
    print("[client] WARN: PiJuice not found. Battery reporting will not work.")
    pijuice = None
except Exception as e:
    print(f"[client] WARN: PiJuice init failed: {e}")
    pijuice = None
# =============================================================

# === CONFIG ===
HOST_URL = "http://192.168.50.150:5000/"
IMAGE_WIDTH = 1872
IMAGE_HEIGHT = 1404
IMAGE_PATH = "/tmp/dashboard.raw"

# === MOSFET Control Configuration ===
EINK_POWER_PIN = 17  # BCM pin number for MOSFET control (e.g., GPIO17)


# ====================================

def get_battery_level():
    if not pijuice:
        return None
    try:
        charge = pijuice.status.GetChargeLevel()
        if charge.get('error') == 'NO_ERROR':
            return charge.get('data', 0)
    except Exception as e:
        print(f"[client] Error getting battery: {e}")
    return None


def report_battery_to_server(level):
    if level is None: return
    try:
        print(f"[client] Reporting battery level ({level}%) to server...")
        requests.post(f"{HOST_URL}api/battery", json={"level": level}, timeout=5)
    except Exception as e:
        print(f"[client] Failed to report battery level: {e}")


def check_server_connection(url):
    """Checks if the server is reachable before launching the heavy browser."""
    try:
        print(f"[client] Checking server status at {url}...")
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            print("[client] Server is up and reachable.")
            return True
        else:
            print(f"[client] Server returned status code: {r.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"[client] Server connection failed: {e}")
        print("[client] Tip: Check if the server is running and port 5000 is allowed in UFW.")
        return False


def render_site_to_image(url, width, height, out_path):
    print("[client] Setting up Chrome options...")
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument(f"--window-size={width},{height}")
    chrome_options.add_argument("--hide-scrollbars")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-application-cache")
    chrome_options.add_argument("--disk-cache-size=1")
    chrome_options.add_argument("--media-cache-size=1")
    chrome_options.add_argument("--aggressive-cache-discard")
    chrome_options.binary_location = "/usr/bin/chromium-browser"

    service = Service('/usr/bin/chromedriver')

    print("[client] Starting WebDriver...")
    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
        # Set a timeout so the driver doesn't hang indefinitely if the network drops mid-load
        driver.set_page_load_timeout(45)

        print(f"[client] Getting URL: {url}")
        driver.get(url)
        # Force a hard reload to bypass cache
        driver.execute_script("location.reload(true);")
        print("[client] Waiting for page to load (15s)...")
        time.sleep(15)

        print("[client] Taking screenshot...")
        png = driver.get_screenshot_as_png()

        print("[client] Processing image...")
        image = Image.open(io.BytesIO(png)).convert("L")
        image = image.resize((width, height), Image.LANCZOS)
        image = ImageOps.flip(image)

        print(f"[client] Saving final raw image to {out_path}...")
        with open(out_path, "wb") as f:
            f.write(image.tobytes())
        return True
    except Exception as e:
        print(f"[client] ERROR during WebDriver operation: {e}")
        return False
    finally:
        print("[client] Quitting WebDriver.")
        try:
            driver.quit()
        except:
            pass


# === MOSFET Control Functions ===
def setup_gpio_for_mosfet():
    if GPIO:
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(EINK_POWER_PIN, GPIO.OUT)
            print(f"[client] GPIO pin {EINK_POWER_PIN} initialized for MOSFET control.")
            return True
        except Exception as e:
            print(f"[client] WARN: Failed to initialize GPIO for MOSFET: {e}")
            return False
    else:
        print("[client] WARN: RPi.GPIO library not available. MOSFET control disabled.")
        return False


def power_mosfet_on():
    if GPIO and GPIO.getmode() is not None:
        print(f"[client] Turning MOSFET ON (GPIO {EINK_POWER_PIN} -> HIGH)")
        GPIO.output(EINK_POWER_PIN, GPIO.HIGH)
        print("[client] E-ink display power should be ON.")
    else:
        print("[client] WARN: GPIO not set up, cannot turn MOSFET ON.")


def power_mosfet_off():
    if GPIO and GPIO.getmode() is not None:
        print(f"[client] Turning MOSFET OFF (GPIO {EINK_POWER_PIN} -> LOW)")
        GPIO.output(EINK_POWER_PIN, GPIO.LOW)
        print("[client] E-ink display power should be OFF.")
    else:
        print("[client] WARN: GPIO not set up, cannot turn MOSFET OFF.")


def main():
    gpio_initialized_successfully = setup_gpio_for_mosfet()

    # Check for "sleep" argument
    if len(sys.argv) > 1 and sys.argv[1].lower() == "sleep":
        print("[client] 'sleep' argument received.")
        if gpio_initialized_successfully:
            power_mosfet_off()
        sys.exit(0)

    # 1. Check Server Connection FIRST
    # This prevents the heavy browser from launching if the server is blocked/down
    if not check_server_connection(HOST_URL):
        print("[client] Aborting render to save power.")
        sys.exit(1)

    # 2. Get Battery Level & Report to Server
    bat_level = get_battery_level()
    if bat_level is not None:
        report_battery_to_server(bat_level)
    else:
        print("[client] Could not read battery level.")

    # 3. Render site
    print("[client] Starting rendering process...")
    render_successful = False
    try:
        render_successful = render_site_to_image(HOST_URL, IMAGE_WIDTH, IMAGE_HEIGHT, IMAGE_PATH)
        if render_successful:
            print("[client] Image rendering successful.")
        else:
            print("[client] Image rendering failed.")
    except Exception as e:
        print(f"[client] An unexpected error occurred: {e}")

    # 4. Turn on E-ink
    if gpio_initialized_successfully:
        print("[client] Proceeding to turn on MOSFET for e-ink display.")
        power_mosfet_on()
    else:
        print("[client] GPIO not initialized, unable to turn on MOSFET.")


if __name__ == "__main__":
    main()