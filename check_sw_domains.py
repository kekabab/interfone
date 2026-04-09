import requests
from requests.auth import HTTPBasicAuth

project_id = '88fe61b5-59a5-4bd9-957a-eb70df85408e'
api_token = 'PTa90dfa5f1cc1b81085a78e2a59c9997f21473b36bb9dbedb'
space_url = 'beraaa.signalwire.com'

url = f"https://{space_url}/api/relay/rest/domains"
try:
    response = requests.get(url, auth=HTTPBasicAuth(project_id, api_token))
    if response.status_code == 200:
        data = response.json()
        print("DOMÍNIOS SIP ACHADOS NA CONTA:")
        for d in data.get('data', []):
            print(d.get('name'))
    else:
        print(f"Erro ao buscar dominios: {response.text}")
except Exception as e:
    print(f"Exception: {e}")
