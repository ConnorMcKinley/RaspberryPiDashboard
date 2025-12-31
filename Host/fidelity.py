import os
import traceback
import json
import re
import time
from bs4 import BeautifulSoup

import pyotp
import typing
from typing import Literal

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import StealthConfig, stealth_sync
from enum import Enum


class fid_months(Enum):
    Jan = 1
    Feb = 2
    March = 3
    April = 4
    May = 5
    June = 6
    July = 7
    Aug = 8
    Sep = 9
    Oct = 10
    Nov = 11
    Dec = 12


class FidelityAutomation:
    def __init__(self, headless: bool = True, debug: bool = False, title: str = None, source_account: str = None,
                 save_state: bool = True, profile_path: str = ".") -> None:
        self.headless: bool = headless
        self.title: str = title
        self.save_state: bool = save_state
        self.debug = debug
        self.profile_path: str = profile_path
        self.stealth_config = StealthConfig(
            navigator_languages=False,
            navigator_user_agent=False,
            navigator_vendor=False,
        )
        self.getDriver()
        self.account_dict: dict = {}
        self.source_account = source_account
        self.new_account_number = None
        self.page.set_default_navigation_timeout(360000)
        self.page.set_default_timeout(360000)

    def getDriver(self):
        self.playwright = sync_playwright().start()
        if self.save_state:
            self.profile_path = os.path.abspath(self.profile_path)
            if self.title is not None:
                self.profile_path = os.path.join(self.profile_path, f"Fidelity_{self.title}.json")
            else:
                self.profile_path = os.path.join(self.profile_path, "Fidelity.json")
            if not os.path.exists(self.profile_path):
                os.makedirs(os.path.dirname(self.profile_path), exist_ok=True)
                with open(self.profile_path, "w") as f:
                    json.dump({}, f)

        self.browser = self.playwright.firefox.launch(
            headless=self.headless,
            args=["--disable-webgl", "--disable-software-rasterizer"],
        )
        self.context = self.browser.new_context(
            storage_state=self.profile_path if self.save_state else None,
            viewport={"width": 1920, "height": 1080}
        )
        if self.debug:
            self.context.tracing.start(name="fidelity_trace", screenshots=True, snapshots=True)
        self.page = self.context.new_page()
        stealth_sync(self.page, self.stealth_config)

    def login(self, username: str, password: str, totp_secret: str = None, save_device: bool = True) -> bool:
        try:
            self.page.goto("https://digital.fidelity.com/prgw/digital/login/full-page", timeout=600000)

            try:
                self.page.wait_for_selector('input[name="username"]', timeout=5000)
            except:
                if "summary" in self.page.url: return (True, True)

            self.page.get_by_label("Username", exact=True).click()
            self.page.get_by_label("Username", exact=True).fill(username)
            self.page.get_by_label("Password", exact=True).click()
            self.page.get_by_label("Password", exact=True).fill(password)
            self.page.get_by_role("button", name="Log in").click()

            self.wait_for_loading_sign()
            self.page.wait_for_timeout(5000)
            self.wait_for_loading_sign()

            if "summary" in self.page.url:
                return (True, True)

            totp_secret = None if totp_secret == "NA" else totp_secret

            if "login" in self.page.url:
                self.wait_for_loading_sign()
                try:
                    widget = self.page.locator("#dom-widget div").first
                    widget.wait_for(timeout=20000, state='visible')
                except:
                    pass

                if (totp_secret is not None and self.page.get_by_role("heading",
                                                                      name="Enter the code from your").is_visible()):
                    if save_device:
                        self.page.locator("label").filter(has_text="Don't ask me again on this").check(timeout=0)

                    self.page.get_by_placeholder("XXXXXX").click()
                    placeholder = self.page.get_by_placeholder("XXXXXX")
                    continue_button = self.page.get_by_role("button", name="Continue")
                    code = pyotp.TOTP(totp_secret).now()
                    placeholder.fill(code, timeout=30000)
                    continue_button.click(timeout=30000)

                    self.wait_for_loading_sign(timeout=0)
                    self.page.wait_for_url("https://digital.fidelity.com/ftgw/digital/portfolio/summary", timeout=60000)
                    return (True, True)

                if self.page.get_by_role("link", name="Try another way").is_visible(timeout=5000):
                    if save_device:
                        self.page.locator("label").filter(has_text="Don't ask me again on this").check(timeout=0)
                    self.page.get_by_role("link", name="Try another way").click(timeout=0)

                if self.page.get_by_role("button", name="Text me the code").is_visible(timeout=5000):
                    self.page.get_by_role("button", name="Text me the code").click(timeout=0)
                    self.page.get_by_placeholder("XXXXXX").click(timeout=0)

                return (True, False)

            raise Exception("Cannot get to login page. Maybe other 2FA method present")

        except PlaywrightTimeoutError:
            print("Timeout waiting for login page.")
            return (False, False)
        except Exception as e:
            print(f"An error occurred: {str(e)}")
            traceback.print_exc()
            return (False, False)

    def login_2FA(self, code: str, save_device: bool = True):
        try:
            self.page.get_by_placeholder("XXXXXX").fill(code, timeout=0)
            if save_device:
                self.page.locator("label").filter(has_text="Don't ask me again on this").check(timeout=0)
            self.page.get_by_role("button", name="Submit").click(timeout=0)
            self.page.wait_for_url("https://digital.fidelity.com/ftgw/digital/portfolio/summary", timeout=0)
            return True
        except Exception as e:
            print(f"An error occurred: {str(e)}")
            return False

    def wait_for_loading_sign(self, timeout: int = 30000):
        signs = [self.page.locator("div:nth-child(2) > .loading-spinner-mask-after").first,
                 self.page.locator(".pvd-spinner__mask-inner").first,
                 self.page.locator("pvd-loading-spinner").first,
                 self.page.locator(".pvd3-spinner-root > .pvd-spinner__spinner").first]
        for sign in signs:
            try:
                sign.wait_for(timeout=timeout, state="hidden")
            except:
                pass

    def get_detailed_portfolio(self):
        result = {"total_net_worth": 0.0, "fidelity_accounts": [], "non_fidelity_accounts": []}

        try:
            # 1. POSITIONS PAGE
            print("Navigating to Positions...")
            self.page.goto("https://digital.fidelity.com/ftgw/digital/portfolio/positions", timeout=60000)
            self.wait_for_loading_sign(timeout=60000)
            self.page.wait_for_timeout(8000)

            content = self.page.content()
            soup = BeautifulSoup(content, 'html.parser')

            pinned_container = soup.find('div', {'class': 'ag-pinned-left-cols-container'}) or soup.select_one(
                '.ag-pinned-left-cols-container')
            center_container = soup.find('div', {'class': 'ag-center-cols-container'}) or soup.select_one(
                '.ag-center-cols-container')

            if not pinned_container or not center_container:
                print("Could not find grid containers.")
                return result

            pinned_rows = pinned_container.find_all('div', {'role': 'row'})
            center_rows = center_container.find_all('div', {'role': 'row'})
            center_map = {row.get('row-id'): row for row in center_rows}

            current_account = None
            account_holdings = []

            def clean_number(txt):
                if not txt or txt == '--': return 0.0
                match = re.search(r'([+\-]?[0-9,]+(\.[0-9]+)?)', txt)
                if match:
                    clean_str = match.group(1).replace(',', '').replace('+', '')
                    try:
                        return float(clean_str)
                    except:
                        return 0.0
                return 0.0

            for p_row in pinned_rows:
                row_id = p_row.get('row-id')
                classes = p_row.get('class', [])

                if 'posweb-row-account' in classes:
                    if current_account and account_holdings:
                        self._merge_and_add_account(result, current_account, account_holdings)

                    account_name_el = p_row.select_one('.posweb-cell-account_primary')
                    if account_name_el:
                        current_account = account_name_el.get_text(strip=True)
                    else:
                        current_account = p_row.get_text(strip=True)
                    account_holdings = []
                    continue

                if 'posweb-row-position' in classes and current_account:
                    sym_el = p_row.select_one('.posweb-cell-symbol-name_container span')
                    if not sym_el: continue
                    symbol = sym_el.get_text(strip=True)

                    c_row = center_map.get(row_id)
                    if not c_row: continue

                    # SIMPLIFIED PARSING USING EXACT COLUMN INDICES (0-indexed)
                    # Index 4: Total Gain $
                    # Index 5: Total Gain % (6th gridcell)
                    # Index 6: Current Value (7th gridcell)
                    # Index 10: Cost Basis Total (11th gridcell) - backup for calculation

                    cells = c_row.find_all('div', {'role': 'gridcell'})

                    val_raw = ""
                    pct_raw = ""
                    gain_dol_raw = ""
                    cost_raw = ""

                    if len(cells) > 6:
                        # Safety check
                        try:
                            gain_dol_raw = cells[4].get_text(" ", strip=True)  # 5th cell
                            pct_raw = cells[5].get_text(" ", strip=True)  # 6th cell
                            val_raw = cells[6].get_text(" ", strip=True)  # 7th cell
                            if len(cells) > 10:
                                cost_raw = cells[10].get_text(" ", strip=True)  # 11th cell
                        except Exception as parse_err:
                            print(f"DEBUG: Error accessing cells for {symbol}: {parse_err}")

                    val = clean_number(val_raw)
                    pct = clean_number(pct_raw)
                    gain_dol = clean_number(gain_dol_raw)
                    cost = clean_number(cost_raw)

                    # Debug prints for verification
                    if gain_dol == 0.0 and val > 0:
                        print(
                            f"DEBUG: {symbol} Gain$ 0. Raw: '{gain_dol_raw}', CostRaw: '{cost_raw}', PctRaw: '{pct_raw}'")

                    account_holdings.append({
                        "symbol": symbol,
                        "value": val,
                        "pct_gain": pct,
                        "gain_dollar": gain_dol,
                        "cost_basis_raw": cost
                    })

            if current_account and account_holdings:
                self._merge_and_add_account(result, current_account, account_holdings)

            # 2. BALANCES PAGE
            print("Navigating to Balances...")
            self.page.goto("https://digital.fidelity.com/ftgw/digital/portfolio/balances", timeout=60000)
            self.wait_for_loading_sign(timeout=60000)
            self.page.wait_for_timeout(5000)

            content_bal = self.page.content()
            soup_bal = BeautifulSoup(content_bal, 'html.parser')

            nw_el = soup_bal.select_one('div.total-balance__value')
            if nw_el:
                result['total_net_worth'] = clean_number(nw_el.get_text(strip=True))

            balance_rows = soup_bal.select('div.expand-header-section')

            for row in balance_rows:
                try:
                    name_el = row.select_one('.expand-header-section__title span.pvd-link__text')
                    if not name_el: continue
                    name = name_el.get_text(strip=True)

                    desc_el = row.select_one('.expand-header-section__sectiontitle-description')
                    acc_id = desc_el.get_text(strip=True) if desc_el else ""

                    is_fidelity = False
                    if re.match(r'^[XYZ]\d+$', acc_id) or (acc_id.isdigit() and len(acc_id) < 15):
                        is_fidelity = True

                    if not is_fidelity:
                        val_el = row.select_one(
                            '.expand-header-section__center-content__amount div[data-testid*="totalaccountvalue-label"]')
                        if val_el:
                            val = clean_number(val_el.get_text(strip=True))
                            result['non_fidelity_accounts'].append({
                                "name": name,
                                "value": val
                            })
                except:
                    continue

        except Exception as e:
            print(f"Detailed portfolio fetch failed: {e}")
            traceback.print_exc()

        return result

    def _merge_and_add_account(self, result_dict, account_name, raw_holdings):
        merged = {}
        for h in raw_holdings:
            sym = h['symbol']
            if sym == "Pending activity": continue
            if sym not in merged:
                merged[sym] = {
                    "symbol": sym,
                    "value": 0.0,
                    "total_gain_dollars": 0.0,
                    "cost_basis": 0.0
                }

            merged[sym]['value'] += h['value']
            merged[sym]['total_gain_dollars'] += h['gain_dollar']

            if h.get('cost_basis_raw', 0) > 0:
                merged[sym]['cost_basis'] += h['cost_basis_raw']
            else:
                merged[sym]['cost_basis'] += (h['value'] - h['gain_dollar'])

        final_list = []
        for sym, data in merged.items():
            if data['cost_basis'] != 0:
                pct = (data['total_gain_dollars'] / data['cost_basis']) * 100
            else:
                pct = 0.0

            final_list.append({
                "symbol": sym,
                "value": data['value'],
                "pct_gain": pct
            })

        final_list.sort(key=lambda x: x['value'], reverse=True)

        result_dict['fidelity_accounts'].append({
            "name": account_name,
            "holdings": final_list
        })

    def close_browser(self):
        self.save_storage_state()
        self.context.close()
        self.browser.close()
        self.playwright.stop()

    def save_storage_state(self):
        if self.save_state:
            storage_state = self.page.context.storage_state()
            with open(self.profile_path, "w") as f:
                json.dump(storage_state, f)