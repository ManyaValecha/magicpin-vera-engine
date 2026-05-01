import requests
import json

BASE_URL = "http://localhost:8080/v1"

def print_res(res):
    print(f"Status: {res.status_code}")
    print(json.dumps(res.json(), indent=2))
    print("-" * 40)

print("GET /metadata")
print_res(requests.get(f"{BASE_URL}/metadata"))

print("GET /healthz")
print_res(requests.get(f"{BASE_URL}/healthz"))

print("POST /context (new)")
ctx_req = {
  "scope": "merchant",
  "context_id": "m_001_drmeera",
  "version": 1,
  "payload": {
    "category_slug": "dentists",
    "identity": {"name": "Dr. Meera's Clinic", "locality": "Lajpat Nagar"}
  },
  "delivered_at": "2026-04-26T10:00:00Z"
}
print_res(requests.post(f"{BASE_URL}/context", json=ctx_req))

print("POST /context (same ver)")
print_res(requests.post(f"{BASE_URL}/context", json=ctx_req))

print("POST /context (older ver -> 409)")
ctx_req["version"] = 0
print_res(requests.post(f"{BASE_URL}/context", json=ctx_req))

print("POST /context (trigger)")
trg_req = {
  "scope": "trigger",
  "context_id": "t_001",
  "version": 1,
  "payload": {
    "kind": "recall_due",
    "merchant_id": "m_001_drmeera"
  },
  "delivered_at": "2026-04-26T10:05:00Z"
}
print_res(requests.post(f"{BASE_URL}/context", json=trg_req))

print("GET /healthz (after counts)")
print_res(requests.get(f"{BASE_URL}/healthz"))

print("POST /tick")
tick_req = {
  "now": "2026-04-26T10:30:00Z",
  "available_triggers": ["t_001"]
}
print_res(requests.post(f"{BASE_URL}/tick", json=tick_req))

print("POST /reply")
reply_req = {
  "conversation_id": "conv_m_001_drmeera_t_001",
  "from_role": "merchant",
  "message": "Yes, send me the abstract",
  "received_at": "2026-04-26T10:45:00Z",
  "turn_number": 2
}
print_res(requests.post(f"{BASE_URL}/reply", json=reply_req))

print("POST /reply (stop)")
reply_req["message"] = "please stop"
print_res(requests.post(f"{BASE_URL}/reply", json=reply_req))
