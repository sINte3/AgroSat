from sqlalchemy import Column, Integer, String, JSON, Text
from sqlalchemy.orm import relationship
from database import Base


# Справочник культур для Узбекистана
UZBEKISTAN_CROPS = [
    {
        "code": "cotton",
        "name_ru": "Хлопок",
        "name_uz": "G'o'za",
        "name_en": "Cotton",
        "typical_sowing_month": 4,    # Апрель
        "typical_harvest_month": 10,  # Октябрь
        "growth_stages": [
            {"name": "Прорастание", "name_uz": "Unib chiqish", "month_start": 4, "month_end": 5,
             "ndvi_min": 0.10, "ndvi_max": 0.30, "description": "Всходы, ранний рост"},
            {"name": "Вегетация", "name_uz": "O'sish davri", "month_start": 5, "month_end": 7,
             "ndvi_min": 0.35, "ndvi_max": 0.70, "description": "Интенсивный рост"},
            {"name": "Бутонизация", "name_uz": "Kurtaklash", "month_start": 6, "month_end": 7,
             "ndvi_min": 0.45, "ndvi_max": 0.75, "description": "Формирование бутонов"},
            {"name": "Цветение", "name_uz": "Gullash", "month_start": 7, "month_end": 8,
             "ndvi_min": 0.55, "ndvi_max": 0.80, "description": "Цветение"},
            {"name": "Коробочки", "name_uz": "Ko'sak hosil bo'lishi", "month_start": 8, "month_end": 9,
             "ndvi_min": 0.40, "ndvi_max": 0.70, "description": "Формирование коробочек"},
            {"name": "Созревание", "name_uz": "Pishish", "month_start": 9, "month_end": 10,
             "ndvi_min": 0.15, "ndvi_max": 0.40, "description": "Созревание, раскрытие коробочек"},
        ],
        "alerts": {
            "drought_risk_months": [6, 7, 8],
            "disease_risk": ["фузариозное увядание", "вертициллёзное увядание", "угловая пятнистость"],
            "pest_risk": ["хлопковая совка", "паутинный клещ", "трипсы"],
        }
    },
    {
        "code": "wheat",
        "name_ru": "Пшеница озимая",
        "name_uz": "Kuzgi bug'doy",
        "name_en": "Winter Wheat",
        "typical_sowing_month": 10,   # Октябрь
        "typical_harvest_month": 6,   # Июнь
        "growth_stages": [
            {"name": "Посев-всходы", "name_uz": "Unib chiqish", "month_start": 10, "month_end": 11,
             "ndvi_min": 0.10, "ndvi_max": 0.30, "description": "Прорастание семян"},
            {"name": "Кущение", "name_uz": "Tillash", "month_start": 11, "month_end": 2,
             "ndvi_min": 0.25, "ndvi_max": 0.50, "description": "Активное кущение"},
            {"name": "Трубкование", "name_uz": "Naylash", "month_start": 3, "month_end": 4,
             "ndvi_min": 0.40, "ndvi_max": 0.70, "description": "Выход в трубку"},
            {"name": "Колошение", "name_uz": "Boshoqlash", "month_start": 4, "month_end": 5,
             "ndvi_min": 0.55, "ndvi_max": 0.80, "description": "Колошение и цветение"},
            {"name": "Налив зерна", "name_uz": "Don to'lishi", "month_start": 5, "month_end": 5,
             "ndvi_min": 0.40, "ndvi_max": 0.65, "description": "Молочно-восковая спелость"},
            {"name": "Созревание", "name_uz": "Pishish", "month_start": 5, "month_end": 6,
             "ndvi_min": 0.15, "ndvi_max": 0.35, "description": "Полная спелость, уборка"},
        ],
        "alerts": {
            "drought_risk_months": [4, 5],
            "disease_risk": ["бурая ржавчина", "мучнистая роса", "септориоз"],
            "pest_risk": ["злаковая тля", "хессенская муха"],
        }
    },
    {
        "code": "rice",
        "name_ru": "Рис",
        "name_uz": "Sholi",
        "name_en": "Rice",
        "typical_sowing_month": 5,
        "typical_harvest_month": 9,
        "growth_stages": [
            {"name": "Рассада", "name_uz": "Ko'chat", "month_start": 5, "month_end": 6,
             "ndvi_min": 0.15, "ndvi_max": 0.35, "description": "Высадка рассады"},
            {"name": "Кущение", "name_uz": "Tillash", "month_start": 6, "month_end": 7,
             "ndvi_min": 0.45, "ndvi_max": 0.70, "description": "Активный рост"},
            {"name": "Выметывание", "name_uz": "Boshlanish", "month_start": 7, "month_end": 8,
             "ndvi_min": 0.55, "ndvi_max": 0.80, "description": "Выметывание метёлки"},
            {"name": "Созревание", "name_uz": "Pishish", "month_start": 8, "month_end": 9,
             "ndvi_min": 0.25, "ndvi_max": 0.50, "description": "Созревание"},
        ],
        "alerts": {
            "drought_risk_months": [],  # риса нужна вода постоянно
            "disease_risk": ["пирикуляриоз", "бактериоз"],
            "pest_risk": ["рисовый долгоносик", "стеблевой мотылёк"],
        }
    },
    {
        "code": "maize",
        "name_ru": "Кукуруза",
        "name_uz": "Makkajo'xori",
        "name_en": "Maize",
        "typical_sowing_month": 4,
        "typical_harvest_month": 9,
        "growth_stages": [
            {"name": "Всходы", "name_uz": "Unib chiqish", "month_start": 4, "month_end": 5,
             "ndvi_min": 0.10, "ndvi_max": 0.30, "description": "Прорастание"},
            {"name": "Вегетация", "name_uz": "O'sish", "month_start": 5, "month_end": 7,
             "ndvi_min": 0.45, "ndvi_max": 0.80, "description": "Интенсивный рост"},
            {"name": "Цветение", "name_uz": "Gullash", "month_start": 7, "month_end": 8,
             "ndvi_min": 0.60, "ndvi_max": 0.85, "description": "Выброс метёлки"},
            {"name": "Созревание", "name_uz": "Pishish", "month_start": 8, "month_end": 9,
             "ndvi_min": 0.25, "ndvi_max": 0.55, "description": "Молочно-восковая спелость"},
        ],
        "alerts": {
            "drought_risk_months": [6, 7, 8],
            "disease_risk": ["пузырчатая головня", "корневая гниль"],
            "pest_risk": ["стеблевой мотылёк", "хлопковая совка"],
        }
    },
    {
        "code": "sunflower",
        "name_ru": "Подсолнечник",
        "name_uz": "Kungaboqar",
        "name_en": "Sunflower",
        "typical_sowing_month": 4,
        "typical_harvest_month": 9,
        "growth_stages": [
            {"name": "Всходы", "name_uz": "Unib chiqish", "month_start": 4, "month_end": 5,
             "ndvi_min": 0.10, "ndvi_max": 0.25, "description": "Прорастание"},
            {"name": "Вегетация", "name_uz": "O'sish", "month_start": 5, "month_end": 7,
             "ndvi_min": 0.40, "ndvi_max": 0.75, "description": "Активный рост"},
            {"name": "Цветение", "name_uz": "Gullash", "month_start": 7, "month_end": 8,
             "ndvi_min": 0.50, "ndvi_max": 0.75, "description": "Цветение"},
            {"name": "Созревание", "name_uz": "Pishish", "month_start": 8, "month_end": 9,
             "ndvi_min": 0.20, "ndvi_max": 0.45, "description": "Созревание семян"},
        ],
        "alerts": {
            "drought_risk_months": [6, 7, 8],
            "disease_risk": ["склеротиниоз", "фомоз"],
            "pest_risk": ["подсолнечная огнёвка"],
        }
    },
    {
        "code": "vegetables",
        "name_ru": "Овощи открытого грунта",
        "name_uz": "Ochiq yerda sabzavotlar",
        "name_en": "Open Field Vegetables",
        "typical_sowing_month": 3,
        "typical_harvest_month": 10,
        "growth_stages": [
            {"name": "Рост", "name_uz": "O'sish", "month_start": 3, "month_end": 10,
             "ndvi_min": 0.25, "ndvi_max": 0.75, "description": "Активный рост"},
        ],
        "alerts": {
            "drought_risk_months": [6, 7, 8],
            "disease_risk": ["фитофтороз", "альтернариоз"],
            "pest_risk": ["тля", "белокрылка", "паутинный клещ"],
        }
    },
    {
        "code": "alfalfa",
        "name_ru": "Люцерна",
        "name_uz": "Beda",
        "name_en": "Alfalfa",
        "typical_sowing_month": 3,
        "typical_harvest_month": 10,
        "growth_stages": [
            {"name": "Активный рост", "name_uz": "Faol o'sish", "month_start": 3, "month_end": 10,
             "ndvi_min": 0.30, "ndvi_max": 0.75, "description": "3-4 укоса в сезон"},
        ],
        "alerts": {
            "drought_risk_months": [7, 8],
            "disease_risk": ["корневые гнили"],
            "pest_risk": ["люцерновый долгоносик"],
        }
    },
    {
        "code": "fallow",
        "name_ru": "Пар / Отдых",
        "name_uz": "Haydov yer",
        "name_en": "Fallow",
        "typical_sowing_month": None,
        "typical_harvest_month": None,
        "growth_stages": [],
        "alerts": {}
    },
]


class CropType(Base):
    """Справочник культур."""
    __tablename__ = "crop_types"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False)
    name_ru = Column(String(100), nullable=False)
    name_uz = Column(String(100), nullable=True)
    name_en = Column(String(100), nullable=True)
    typical_sowing_month = Column(Integer, nullable=True)
    typical_harvest_month = Column(Integer, nullable=True)
    growth_stages = Column(JSON, nullable=True)   # Фенологический календарь
    alerts_config = Column(JSON, nullable=True)   # Конфигурация алертов

    # Relationships
    seasons = relationship("CropSeason", back_populates="crop_type", lazy="raise_on_sql")
