#!/usr/bin/env python
# encoding: utf-8
import argparse
import logging
import os

from flask import Flask, request

import utils
from simplyblock_core import constants

logger_handler = logging.StreamHandler()
logger_handler.setFormatter(logging.Formatter('%(asctime)s: %(levelname)s: %(message)s'))
logger = logging.getLogger()
logger.addHandler(logger_handler)
logger.setLevel(logging.DEBUG)


app = Flask(__name__)
app.url_map.strict_slashes = False
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = True


@app.route('/', methods=['GET'])
def status():
    return utils.get_response("Live")


@app.route('/', methods=['POST'])
def rpc_method():
    data = request.get_json()
    method = data.get('method')
    params = data.get('params')
    return utils.get_response({"jsonrpc":"2.0","id":1,"result":True})


MODES = [
    "storage_node",
    "caching_docker_node",
    "caching_kubernetes_node",
    "storage_node_mock",
]

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=MODES)
parser.add_argument("port", type=int, default=5000)


if __name__ == '__main__':
    args = parser.parse_args()
    port = args.port
    mode = args.mode
    if mode == "caching_docker_node":
        from blueprints import node_api_basic, node_api_caching_docker
        app.register_blueprint(node_api_basic.bp)
        app.register_blueprint(node_api_caching_docker.bp)

    if mode == "caching_kubernetes_node":
        from blueprints import node_api_basic, node_api_caching_ks
        app.register_blueprint(node_api_basic.bp)
        app.register_blueprint(node_api_caching_ks.bp)

    if mode == "storage_node":
        from blueprints import snode_ops
        app.register_blueprint(snode_ops.bp)

    if mode == "storage_node_mock":
        os.environ["MOCK_PORT"] = str(port)
        from blueprints import snode_ops_mock
        app.register_blueprint(snode_ops_mock.bp)

    app.run(host='0.0.0.0', debug=constants.LOG_WEB_DEBUG, port=port)
