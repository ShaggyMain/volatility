# Data contract

All timestamps are UTC ISO-8601.

Every source record should include:
- source_name;
- source_type;
- published_at;
- retrieved_at;
- source_id/url when available;
- raw payload hash when possible.

Critical market inputs must have point-in-time timestamps.
If a field cannot be verified, use null and mark its quality as LOW rather than inferring it.
