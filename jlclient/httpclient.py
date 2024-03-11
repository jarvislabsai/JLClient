import requests
import urllib3
import certifi
import json
import urllib.parse
url = "https://backendprod.jarvislabs.net/"

http = urllib3.PoolManager(
    cert_reqs="CERT_REQUIRED",
    ca_certs=certifi.where()
)


def post(data, func, token, query_params=None, no_template = None):
    encoded_body = json.dumps(data)
    try:
        full_url = url + func
        if query_params:
                full_url += "?" + urllib.parse.urlencode(query_params)
        r = http.request('POST', full_url,
                         headers = {
                                    'Authorization': f'Bearer {token}',
                                    'Content-Type': 'application/json'
                                   },
                         body=encoded_body,
                         # fields={'files': files}
                         #   timeout=10
                         )
    except requests.exceptions.Timeout as e:
        print(e)
    return json.loads(r.data)

def get(func, token, data=None):
    try:
        r = http.request('GET', url+func,
                         headers = {
                                    'Authorization': f'Bearer {token}',
                                    'Content-Type': 'application/json'
                                   },
                         # fields={'files': files}
                         #   timeout=10
                         )
    except requests.exceptions.Timeout as e:
        print(e)
    return json.loads(r.data)


def post_files(files, func):
    r = requests.post(url+func, files=files)
    return r.text

