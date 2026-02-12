
from datetime import datetime
from pydantic import BaseModel
import json

class Message(BaseModel):
    id: str
    metadata: dict

dt = datetime.now()
m = Message(id="1", metadata={"created": dt, "nested": {"time": dt}})

print("--- Testing model_dump(mode='json') ---")
dumped = m.model_dump(mode='json')
print(f"Dumped: {dumped}")

try:
    json.dumps(dumped)
    print("SUCCESS: Result is JSON serializable")
except TypeError as e:
    print(f"FAILURE: {e}")
    print("Analysis: model_dump(mode='json') did NOT convert nested datetimes in dict.")
