from flask import Flask
from dotenv import load_dotenv
from .config import Config
from .extensions import db, migrate, login_manager
from .models import User

def create_app():
    load_dotenv()

    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id: str):
        return User.query.get(int(user_id))

    # Blueprints
    from .auth.routes import auth_bp
    from .pos.routes import pos_bp
    from .admin.routes import admin_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(pos_bp, url_prefix="/pos")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # Healthcheck
    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
