import json
import requests

url = "https://www.washingtonpost.com/prism/api/alerts"

r = requests.get(url)
alerts = r.json()

local_file_path = 'alerts.json'

def fetch_new_alerts(url):
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
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
    new_alerts = fetch_new_alerts(url)
    existing_alerts = load_existing_alerts(local_file_path)
    updated_alerts = add_new_alerts(existing_alerts, new_alerts)
    save_alerts_to_file(updated_alerts, local_file_path)

# Update the alerts
update_alerts(url, local_file_path)
