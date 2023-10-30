import requests
import urllib3
import certifi
import json
url = "backend.jarvislabs.net"

http = urllib3.PoolManager(
    cert_reqs="CERT_REQUIRED",
    ca_certs=certifi.where()
)


def post(data, func):
    encoded_body = json.dumps(data)
    try:
        r = http.request('POST', url+func,
                         headers={'Content-Type': 'application/json'},
                         body=encoded_body,
                         # fields={'files': files}
                         #   timeout=10
                         )
    except requests.exceptions.Timeout as e:
        print(e)
    return json.loads(r.data)


def post_files(files, func):
    r = requests.post(url+func, files=files)
    return r.text
