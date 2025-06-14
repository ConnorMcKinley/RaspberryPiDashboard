#!/usr/bin/python3
import time
import subprocess
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from PIL import Image, ImageOps
import io
import os
import sys

# === Hardware communication imports (for Raspberry Pi) ===
try:
    import spidev # Though not used in this version, kept for consistency if your project grows
    import RPi.GPIO as GPIO
    print("[client] RPi.GPIO and spidev loaded.")
except ImportError:
    print("[client] WARN: RPi.GPIO or spidev not found. Sleep/MOSFET/hardware functions will not work.")
    spidev = None
    GPIO = None
# =============================================================

# === CONFIG ===
HOST_URL = "http://192.168.1.80:5000/"  # <-- Change this to your host machine's IP
IMAGE_WIDTH = 1872
IMAGE_HEIGHT = 1404
IMAGE_PATH = "/tmp/dashboard.raw"

# === MOSFET Control Configuration ===
EINK_POWER_PIN = 17  # BCM pin number for MOSFET control (e.g., GPIO17)
# ====================================

def render_site_to_image(url, width, height, out_path):
    print("[client] Setting up Chrome options...")
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument(f"--window-size={width},{height}")
    chrome_options.add_argument("--hide-scrollbars")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")  # Often needed on Pi
    chrome_options.add_argument("--disable-dev-shm-usage")  # Often needed on Pi
    chrome_options.add_argument("--disable-application-cache")
    chrome_options.add_argument("--disk-cache-size=1")
    chrome_options.add_argument("--media-cache-size=1")
    chrome_options.add_argument("--aggressive-cache-discard")
    chrome_options.binary_location = "/usr/bin/chromium-browser"

    service = Service('/usr/bin/chromedriver')

    print("[client] Starting WebDriver...")
    driver = webdriver.Chrome(service=service, options=chrome_options)
    try:
        print(f"[client] Getting URL: {url}")
        driver.get(url)
        # Force a hard reload to bypass cache
        driver.execute_script("location.reload(true);")
        print("[client] Waiting for page to load (15s)...")
        time.sleep(15) # Adjust as needed for your site

        print("[client] Taking screenshot...")
        png = driver.get_screenshot_as_png()

        print("[client] Processing image...")
        image = Image.open(io.BytesIO(png)).convert("L") # Convert to grayscale
        image = image.resize((width, height), Image.LANCZOS)
        image = ImageOps.flip(image) # Adjust flip/rotation as per your e-ink's needs

        print(f"[client] Saving final raw image to {out_path}...")
        with open(out_path, "wb") as f:
            f.write(image.tobytes())
        return True # Indicate success
    except Exception as e:
        print(f"[client] ERROR during WebDriver operation: {e}")
        return False # Indicate failure
    finally:
        print("[client] Quitting WebDriver.")
        driver.quit()

# === MOSFET Control Functions ===
def setup_gpio_for_mosfet():
    """Initializes GPIO pins for MOSFET control."""
    if GPIO:
        try:
            # Using BCM numbering for GPIO pins
            GPIO.setmode(GPIO.BCM)
            # Disable warnings for channels already in use, etc.
            GPIO.setwarnings(False)
            # Set the power pin as an output
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
    """Turns the MOSFET ON, supplying power to the e-ink display."""
    if GPIO and GPIO.getmode() is not None: # Check if GPIO has been set up
        print(f"[client] Turning MOSFET ON (GPIO {EINK_POWER_PIN} -> HIGH)")
        GPIO.output(EINK_POWER_PIN, GPIO.HIGH)
        print("[client] E-ink display power should be ON.")
    else:
        if not GPIO:
            print("[client] WARN: RPi.GPIO not available, cannot turn MOSFET ON.")
        else:
            print("[client] WARN: GPIO not set up, cannot turn MOSFET ON. Run setup_gpio_for_mosfet() first.")


def power_mosfet_off():
    """Turns the MOSFET OFF, cutting power to the e-ink display."""
    if GPIO and GPIO.getmode() is not None: # Check if GPIO has been set up
        print(f"[client] Turning MOSFET OFF (GPIO {EINK_POWER_PIN} -> LOW)")
        GPIO.output(EINK_POWER_PIN, GPIO.LOW)
        print("[client] E-ink display power should be OFF.")
    else:
        if not GPIO:
            print("[client] WARN: RPi.GPIO not available, cannot turn MOSFET OFF.")
        else:
            print("[client] WARN: GPIO not set up, cannot turn MOSFET OFF. Run setup_gpio_for_mosfet() first.")
# =====================================

def main():
    # Initialize GPIO for MOSFET control early
    gpio_initialized_successfully = setup_gpio_for_mosfet()

    # Check for "sleep" argument
    if len(sys.argv) > 1 and sys.argv[1].lower() == "sleep":
        print("[client] 'sleep' argument received.")
        if gpio_initialized_successfully:
            power_mosfet_off()
        else:
            print("[client] GPIO not initialized, unable to control MOSFET for sleep.")
        sys.exit(0)  # Exit after attempting to turn off

    # Default behavior: render site and then turn on MOSFET
    print("[client] Starting default operation: render dashboard and power on e-ink.")
    render_successful = False
    try:
        render_successful = render_site_to_image(HOST_URL, IMAGE_WIDTH, IMAGE_HEIGHT, IMAGE_PATH)
        if render_successful:
            print("[client] Image rendering successful.")
            print("[client] NOTE: A separate script is needed to display the image on the e-ink screen.")
        else:
            print("[client] Image rendering failed.")
    except Exception as e:
        print(f"[client] An unexpected error occurred during rendering process: {e}")

    # Turn on the MOSFET after the rendering attempt
    # You might decide to only turn it on if render_successful is True
    if gpio_initialized_successfully:
        print("[client] Proceeding to turn on MOSFET for e-ink display.")
        power_mosfet_on()
    else:
        print("[client] GPIO not initialized, unable to turn on MOSFET.")

    # We do not call GPIO.cleanup() here because we want the pin
    # state (ON or OFF) to persist after this script exits.

if __name__ == "__main__":
    main()