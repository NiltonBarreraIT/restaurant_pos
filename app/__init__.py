from flask import Flask
from dotenv import load_dotenv

from .config import Config
from .extensions import db, migrate, login_manager


def create_app():
    # ===============================
    # 🔹 Cargar variables de entorno
    # ===============================
    load_dotenv()

    app = Flask(__name__)
    app.config.from_object(Config)

    # ===============================
    # 🔹 Inicializar extensiones
    # ===============================
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    # Configuración opcional login
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Debes iniciar sesión para acceder."
    login_manager.login_message_category = "warning"

    # ===============================
    # 🔹 Registro de Blueprints
    # ===============================
    from .auth.routes import auth_bp
    from .pos.routes import pos_bp
    from .admin.routes import admin_bp
    from .cocina.routes import cocina_bp
    

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(pos_bp, url_prefix="/pos")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(cocina_bp, url_prefix="/cocina")

    # ===============================
    # 🔹 User Loader
    # ===============================
    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        return User.query.get(int(user_id))

    # ===============================
    # 🔹 Ruta base opcional
    # ===============================
    @app.route("/")
    def index():
        return {
            "app": "Restaurant POS",
            "status": "running"
        }

    # ===============================
    # 🔹 Health check (Azure ready)
    # ===============================
    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app