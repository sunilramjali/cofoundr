# backend/inputs: input channels
**Owner: Jade**

Gmail intake, WhatsApp photos via Saint, external APIs (weather, events, Treg).

Every channel hands documents to the middle with **one call**:
```python
from backend.pipeline import ingest_invoice
result = ingest_invoice(data, mime_type, source="email", origin="email from billing@x.com: 'Invoice 7802'")
# result.ok, result.message (show/log it), result.invoice, result.needs_review, result.drafted, result.error
```
Don't call Gemini, save to MongoDB or run the overdue check yourself; the pipeline does it.
