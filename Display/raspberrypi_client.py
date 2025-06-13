import time
import subprocess
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from PIL import Image, ImageOps
import io
import os
import sys

# === NEW: Hardware communication imports (for Raspberry Pi) ===
try:
    import spidev
    import RPi.GPIO as GPIO
except ImportError:
    print("[client] WARN: RPi.GPIO or spidev not found. Sleep/hardware functions will not work.")
    spidev = None
    GPIO = None
# =============================================================

# === CONFIG ===
HOST_URL = "http://192.168.1.80:5000/"  # <-- Change this to your host machine's IP
IMAGE_WIDTH = 1872
IMAGE_HEIGHT = 1404
IMAGE_PATH = "/tmp/dashboard.raw"


# === NEW: E-Ink Hardware Constants (from your example code) ===
# Pin definitions for the IT8951 controller
class Pins:
    CS = 8
    HRDY = 24  # Gpio24, aka "Busy"
    RESET = 17


# Command codes for the IT8951 controller
class Commands:
    SLEEP = 0x03


# =============================================================

# === NEW: E-Ink Hardware Control Class ===
class EInk:
    """
    A minimal class to control the e-ink display hardware directly.
    """

    def __init__(self, spi_bus=0, spi_device=0, spi_hz=24000000):
        if not GPIO or not spidev:
            raise RuntimeError("Cannot initialize EInk class without RPi.GPIO and spidev libraries.")

        self.pins = Pins

        # SPI setup
        self.spi = spidev.SpiDev()
        self.spi.open(spi_bus, spi_device)
        self.spi.max_speed_hz = spi_hz
        self.spi.mode = 0b00

        # GPIO setup
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.pins.CS, GPIO.OUT)
        GPIO.setup(self.pins.HRDY, GPIO.IN)
        GPIO.setup(self.pins.RESET, GPIO.OUT)

        print("[eink] Hardware initialized.")

    def _wait_for_ready(self, timeout=30):
        """Waits for the HRDY pin to go high."""
        start_time = time.time()
        while GPIO.input(self.pins.HRDY) == GPIO.LOW:
            time.sleep(0.01)
            if time.time() - start_time > timeout:
                raise TimeoutError("E-Ink display failed to become ready.")

    def _send_command(self, command):
        """Sends a command to the display controller."""
        self._wait_for_ready()

        GPIO.output(self.pins.CS, GPIO.LOW)

        # Command preamble
        self.spi.xfer2([0x60, 0x00])
        self._wait_for_ready()

        # Send command
        self.spi.xfer2([command & 0xFF, (command >> 8) & 0xFF])

        GPIO.output(self.pins.CS, GPIO.HIGH)

    def sleep(self):
        """Send the sleep command to the display."""
        print("[eink] Sending sleep command...")
        self._send_command(Commands.SLEEP)
        print("[eink] Display is now in sleep mode.")

    def cleanup(self):
        """Clean up GPIO resources."""
        print("[eink] Cleaning up GPIO.")
        GPIO.cleanup([self.pins.CS, self.pins.RESET])
        self.spi.close()


# =============================================================

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
        # Force a hard reload to bypass cache - this is better
        driver.execute_script("location.reload(true);")
        print("[client] Waiting for page to load...")
        time.sleep(15)

        print("[client] Taking screenshot...")
        png = driver.get_screenshot_as_png()

        print("[client] Processing image...")
        image = Image.open(io.BytesIO(png)).convert("L")
        image = image.resize((width, height), Image.LANCZOS)
        # The IT8951 usually requires a specific orientation
        # This flip might need adjustment depending on your physical setup.
        # Common orientations are no flip, or just one flip.
        image = ImageOps.flip(image)

        print(f"[client] Saving final raw image to {out_path}...")
        with open(out_path, "wb") as f:
            f.write(image.tobytes())
    finally:
        print("[client] Quitting WebDriver.")
        driver.quit()


def main():
    # === MODIFIED: Handle command-line arguments ===
    if len(sys.argv) > 1 and sys.argv[1].lower() == 'sleep':
        if not GPIO:
            print("[client] ERROR: Cannot execute 'sleep' command. Hardware libraries not loaded.")
            return

        eink = None
        try:
            eink = EInk()
            eink.sleep()
        except Exception as e:
            print(f"[client] An error occurred during e-ink sleep command: {e}")
        finally:
            if eink:
                eink.cleanup()
        print("[client] Sleep command sent. Exiting.")

    else:
        print("[client] Fetching and rendering dashboard...")
        try:
            render_site_to_image(HOST_URL, IMAGE_WIDTH, IMAGE_HEIGHT, IMAGE_PATH)
            print("[client] Update complete. The image has been saved to /tmp/dashboard.raw.")
            print("[client] NOTE: You still need a separate script to read this file and display it on the screen.")
        except Exception as e:
            print(f"[client] An error occurred during rendering: {e}")

    # ===================================================


if __name__ == "__main__":
    main()