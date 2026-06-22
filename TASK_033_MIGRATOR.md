Please fix the `psycopg2.ProgrammingError: can't adapt type 'dict'` in `backend/scripts/migrate_to_local.py`.

At the very beginning of the `insert_batch` function, before any SQL statement construction or execution, import `json` and add a loop to serialize any Python `dict` or `list` values to JSON strings.

Add this exact code:
```python
    import json
    for row in rows:
        for k, v in list(row.items()):
            if isinstance(v, (dict, list)):
                row[k] = json.dumps(v)