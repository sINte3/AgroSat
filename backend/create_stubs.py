import os

os.makedirs('api', exist_ok=True)
os.makedirs('models', exist_ok=True)

content = 'from fastapi import APIRouter\nrouter = APIRouter()\n'

for name in ['enterprises', 'fields', 'ndvi', 'alerts', 'dashboard', 'weather']:
    with open(f'api/{name}.py', 'w') as f:
        f.write(content)

for name in ['__init__']:
    with open(f'api/{name}.py', 'w') as f:
        f.write('')
    with open(f'models/{name}.py', 'w') as f:
        f.write('')

print('OK - all files created')