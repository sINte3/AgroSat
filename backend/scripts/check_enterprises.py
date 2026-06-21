import sys
sys.path.insert(0, '..')
from models.enterprise import Enterprise
from models.field import Field
from models.monitoring import NDVIRecord, Alert, User
from models.crop import CropType
from database import SessionLocal, init_db
init_db()
db = SessionLocal()
for e in db.query(Enterprise).all():
    print(f'ID={e.id}: {e.name}')
db.close()