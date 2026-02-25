import requests
import urllib3
import certifi
import json
import urllib.parse
url = "https://backendprod.jarvislabs.net/"
REGION_API_URLS = {
    'india-01': "https://backendprod.jarvislabs.net/",
    'india-noida-01': "https://backendn.jarvislabs.net/",
}


def get_base_url(region=None):
    if region is None:
        return url
    return REGION_API_URLS.get(region, url)

http = urllib3.PoolManager(
    cert_reqs="CERT_REQUIRED",
    ca_certs=certifi.where()
)

def post(data, func, token, query_params=None, no_template = None, base_url=None):
    encoded_body = json.dumps(data)
    try:
        full_url = (base_url if base_url else url) + func
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

def get(func, token, data=None, base_url=None):
    try:
        r = http.request('GET', (base_url if base_url else url)+func,
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

def post_files(files, func, base_url=None):
    r = requests.post((base_url if base_url else url)+func, files=files)
    return r.text
