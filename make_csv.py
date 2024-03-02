import json
import csv

# Define the input and output file paths
input_file_path = 'alerts.json'
output_file_path = 'alerts.csv'

# Define the headers for the CSV file
headers = ['airshipId', 'alert_body', 'alert_title', 'datetime', 'pushID', 'targetTopic', 'text']

# Function to extract the necessary data from each alert object
def extract_data(alert):
    return {
        'airshipId': alert['airshipId'],
        'alert_body': alert['notification']['alert'] if 'alert' in alert['notification'] else None,
        'alert_title': alert['notification']['ios']['alert']['title'] if 'ios' in alert['notification'] and 'alert' in alert['notification']['ios'] and 'title' in alert['notification']['ios']['alert'] else None,
        'datetime': alert['notification']['ios']['extra']['custom']['datetime'] if 'ios' in alert['notification'] and 'extra' in alert['notification']['ios'] and 'custom' in alert['notification']['ios']['extra'] and 'datetime' in alert['notification']['ios']['extra']['custom'] else None,
        'pushID': alert['notification']['ios']['extra']['custom']['pushID'] if 'ios' in alert['notification'] and 'extra' in alert['notification']['ios'] and 'custom' in alert['notification']['ios']['extra'] and 'pushID' in alert['notification']['ios']['extra']['custom'] else None,
        'targetTopic': alert['notification']['ios']['extra']['custom']['targetTopic'] if 'ios' in alert['notification'] and 'extra' in alert['notification']['ios'] and 'custom' in alert['notification']['ios']['extra'] and 'targetTopic' in alert['notification']['ios']['extra']['custom'] else None,
        'text': alert['notification']['ios']['extra']['custom']['text'] if 'ios' in alert['notification'] and 'extra' in alert['notification']['ios'] and 'custom' in alert['notification']['ios']['extra'] and 'text' in alert['notification']['ios']['extra']['custom'] else None,
    }

# Open the input JSON file and load the data
with open(input_file_path, 'r') as json_file:
    alerts = json.load(json_file)

# Open the output CSV file and write the data
with open(output_file_path, 'w', newline='') as csv_file:
    writer = csv.DictWriter(csv_file, fieldnames=headers)
    writer.writeheader()
    for alert in alerts:
        writer.writerow(extract_data(alert))
