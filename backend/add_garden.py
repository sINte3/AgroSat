import sys
sys.path.insert(0, '.')
from models.enterprise import Enterprise
from models.field import Field, CropSeason
from models.monitoring import NDVIRecord, Alert, User
from models.crop import CropType
from database import SessionLocal, init_db

init_db()
db = SessionLocal()
e = Enterprise(
    name='Garden Bukhoro Agroklaster',
    code='BAK-06',
    region='Bukhara',
    is_active=True
)
db.add(e)
db.commit()
db.refresh(e)
print('OK! ID =', e.id)
db.close()