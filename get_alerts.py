import json
import requests
import subprocess
from playwright.sync_api import sync_playwright

url = "https://www.washingtonpost.com/prism/api/alerts"
local_file_path = 'alerts.json'


def fetch_new_alerts_with_playwright(url):
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)  # Use headless=False for debugging
        page = browser.new_page()
        page.goto(url)

        # Wait for a specific element if necessary, or delay for content loading
        # page.wait_for_selector("selector-to-wait-for")

        content = page.content()  # Get the full page content
        json_data = page.evaluate("() => JSON.parse(document.body.innerText)")  # Adjust as needed
        browser.close()

        return json_data

def fetch_new_alerts_with_requests(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:118.0) Gecko/20100101 Firefox/118.0'
    }
    try:
        response = requests.get(url, headers=headers, verify=False)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching alerts: {e}")
        return []

def fetch_new_alerts_with_curl(url):
    # Use curl to download the JSON data with SSL verification disabled
    try:
        result = subprocess.run(
            ['curl', '-k', url],
            check=True,
            capture_output=True,
            text=True
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error fetching alerts: {e}")
        return []

def load_existing_alerts(file_path):
    try:
        with open(file_path, 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        return []

def add_new_alerts(existing_alerts, new_alerts):
    existing_ids = {alert['airshipId'] for alert in existing_alerts}
    new_alerts_to_add = [alert for alert in new_alerts if alert['airshipId'] not in existing_ids]
    return existing_alerts + new_alerts_to_add

def save_alerts_to_file(alerts, file_path):
    with open(file_path, 'w') as file:
        json.dump(alerts, file, indent=4)

def update_alerts(url, local_file_path):
    new_alerts = fetch_new_alerts_with_playwright(url)
#    new_alerts = fetch_new_alerts_with_curl(url)
    existing_alerts = load_existing_alerts(local_file_path)
    updated_alerts = add_new_alerts(existing_alerts, new_alerts)
    save_alerts_to_file(updated_alerts, local_file_path)

# Run the update function
update_alerts(url, local_file_path)
