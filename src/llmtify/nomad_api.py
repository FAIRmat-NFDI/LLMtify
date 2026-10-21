query = {
    "and": [
      {},
      {
        "results.material.topology.label:any": [
          "MOF"
        ]
      }
    ]
  }
required_data = {
    'results':{'material':'*'},
    'run': {'system':{'atoms':'*'}}
}
"""Low-level HTTP client for the NOMAD API with retry/backoff on transient errors."""

import time

import requests

import os
NOMAD_BASE_URL = 'https://nomad-lab.eu/prod/v1/api/v1'
class NomadClient:
    """Thin wrapper around `requests` for calling the NOMAD API.

    Retries on 5xx responses and on connection/timeout errors, using a fixed
    backoff between attempts. Raises `requests.HTTPError` (or the underlying
    `requests` exception) once retries are exhausted.
    """

    def __init__(
        self,
        base_url: str = NOMAD_BASE_URL,
        max_retries: int = 3,
        backoff_seconds: float = 5.0,
        timeout: float = 60.0,
    ):
        self.base_url = base_url
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.timeout = timeout

    def post(self, path: str, payload: dict) -> dict:
        """POST `payload` as JSON to `base_url + path`, returning the parsed JSON body."""
        url = f'{self.base_url}{path}'
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(url, json=payload, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except requests.HTTPError as exc:
                if exc.response is None:
                    raise
                elif exc.response.status_code == 429:
                    # Too Many Requests - wait and retry
                    time.sleep(self.backoff_seconds)
                    continue
                elif exc.response.status_code < 500:
                    raise
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds)
        raise last_exc

def fetch_archives():
        """Fetch full archive data (system + calculation sections) for `entry_ids`.

        Requests are batched (`batch_size` entry_ids per request) to keep payload
        sizes reasonable. Yields one archive dict (as returned in the response
        `data` list) per entry.
        """
        save_dir = 'test_archives'
        client = NomadClient()
        payload = {
            'query': query,
            'required': required_data,
            'pagination': {'page_size': 5},
        }
        response = client.post('/entries/archive/query', payload)
        time.sleep(1)  # avoid overwhelming the server with back-to-back requests
        for archive_entry in response.get('data', []):
            if save_dir:
                os.makedirs(f'{save_dir}/archives', exist_ok=True)
                entry_id = archive_entry.get('entry_id')
                if entry_id:
                    with open(
                        f'{save_dir}/archives/archive_{entry_id}.json', 'w'
                    ) as f:
                        import json
                        json.dump(archive_entry, f, indent=2)