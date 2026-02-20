import os
from dotenv import load_dotenv

# 🔹 Cargar variables de entorno ANTES de crear la app
load_dotenv()

from app import create_app
from app.extensions import db
from app.models import User, Role

app = create_app()

with app.app_context():
    print("📌 DB:", db.engine.url)

    if not User.query.filter_by(username="admin").first():
        admin = User(
            username="admin",
            email="admin@admin.cl",
            role=Role.ADMIN.value,
            is_active=True
        )
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        print("✅ Admin creado: admin / Admin1234!")
    else:
        print("⚠️ El usuario admin ya existe")
