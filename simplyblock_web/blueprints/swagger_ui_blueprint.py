import os

import yaml
from flask_swagger_ui import get_swaggerui_blueprint

SWAGGER_URL="/swagger"

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
API_URL=f"{SCRIPT_PATH}/../static/swagger.yaml"

cnf = {}
with open(API_URL) as f:
    cnf = f.read()
    cnf = yaml.load(cnf, Loader=yaml.SafeLoader)

bp = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={
        'app_name': 'Access API',
        'spec': cnf,
    }
)
