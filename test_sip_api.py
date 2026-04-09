import requests
from requests.auth import HTTPBasicAuth
import json

project_id = '88fe61b5-59a5-4bd9-957a-eb70df85408e'
api_token = 'PTa90dfa5f1cc1b81085a78e2a59c9997f21473b36bb9dbedb'
space_url = 'beraaa.signalwire.com'

url = f"https://{space_url}/api/relay/rest/endpoints/sip"

response = requests.get(url, auth=HTTPBasicAuth(project_id, api_token))
if response.status_code == 200:
    data = response.json()
    print("Endpoints List:")
    for ep in data.get('data', []):
        print(f"User: {ep.get('username')}, Caller_ID: {ep.get('caller_id')}, Encryption: {ep.get('encryption')}, Send_As: {ep.get('send_as')}")
else:
    print(f"Error {response.status_code}: {response.text}")
