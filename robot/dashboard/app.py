import os
from flask import Flask
from robot.config.settings import BASE_DIR

def create_app():
    """Create and configure the Flask dashboard application."""
    
    template_dir = os.path.join(BASE_DIR, 'robot', 'dashboard', 'templates')
    static_dir = os.path.join(BASE_DIR, 'robot', 'dashboard', 'static')
    
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    
    # In a real application, set a strong secret key
    app.config['SECRET_KEY'] = 'dev-secret-key-bmo'
    
    # Register routes
    from robot.dashboard.routes import bp as dashboard_bp
    app.register_blueprint(dashboard_bp)
    
    return app
