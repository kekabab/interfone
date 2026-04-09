import requests
from requests.auth import HTTPBasicAuth

project_id = '88fe61b5-59a5-4bd9-957a-eb70df85408e'
api_token = 'PTa90dfa5f1cc1b81085a78e2a59c9997f21473b36bb9dbedb'
space_url = 'beraaa.signalwire.com'

endpoints = ['interfone', 'claudia', 'paloma', 'ligia', 'mauricio']
password = 'InterfoneAi123!'

url = f"https://{space_url}/api/relay/rest/endpoints/sip"

for user in endpoints:
    payload = {
        "username": user,
        "password": password,
        "caller_id": user.title()
    }
    response = requests.post(url, json=payload, auth=HTTPBasicAuth(project_id, api_token))
    if response.status_code in [200, 201]:
        print(f"Endpoint {user} created successfully!")
    elif response.status_code == 422 and "already exists" in response.text:
        print(f"Endpoint {user} already exists.")
    else:
        print(f"Failed to create {user}: {response.text}")

print(f"\nAll done! SIP Domain is: {space_url.replace('.signalwire.com', '.sip.signalwire.com')}")
