#!/usr/bin/env python
# encoding: utf-8

import logging

from flask import Blueprint, request

from simplyblock_core.controllers import device_controller
from simplyblock_web import utils

from simplyblock_core import db_controller

logger = logging.getLogger(__name__)

bp = Blueprint("device", __name__)
db = db_controller.DBController()


@bp.route('/device/list/<string:uuid>', methods=['GET'])
def list_devices_by_node(uuid):
    try:
        snode = db.get_storage_node_by_id(uuid)
    except KeyError:
        return utils.get_response_error(f"snode not found: {uuid}", 404)

    data = []
    for dev in snode.nvme_devices:
        data.append(dev.get_clean_dict())
    return utils.get_response(data)


@bp.route('/device', methods=['GET'], defaults={'uuid': None})
@bp.route('/device/<string:uuid>', methods=['GET'])
def list_storage_devices(uuid):
    devices = []
    if uuid:
        try:
            dev = db.get_storage_device_by_id(uuid)
        except KeyError:
            return utils.get_response_error(f"device not found: {uuid}", 404)
        devices = [dev]
    else:
        cluster_id = utils.get_cluster_id(request)
        nodes = db.get_storage_nodes_by_cluster_id(cluster_id)
        for node in nodes:
            devices.append(node.nvme_devices)
    data = []
    for dev in devices:
        data.append(dev.get_clean_dict())
    return utils.get_response(data)

@bp.route('/device/capacity/<string:uuid>/history/<string:history>', methods=['GET'])
@bp.route('/device/capacity/<string:uuid>', methods=['GET'], defaults={'history': None})
def device_capacity(uuid, history):
    try:
        db.get_storage_device_by_id(uuid)
    except KeyError:
        return utils.get_response_error(f"devices not found: {uuid}", 404)

    records = device_controller.get_device_capacity(uuid, history, parse_sizes=False)
    return utils.get_response(records)


@bp.route('/device/iostats/<string:uuid>/history/<string:history>', methods=['GET'])
@bp.route('/device/iostats/<string:uuid>', methods=['GET'], defaults={'history': None})
def device_iostats(uuid, history):
    try:
        device = db.get_storage_device_by_id(uuid)
    except KeyError:
        return utils.get_response_error(f"devices not found: {uuid}", 404)

    data = device_controller.get_device_iostats(uuid, history, parse_sizes=False)
    ret = {
        "object_data": device.get_clean_dict(),
        "stats": data or []
    }
    return utils.get_response(ret)


@bp.route('/device/reset/<string:uuid>', methods=['GET'])
def device_reset(uuid):
    try:
        db.get_storage_device_by_id(uuid)
    except KeyError:
        return utils.get_response_error(f"device not found: {uuid}", 404)

    data = device_controller.reset_storage_device(uuid)
    return utils.get_response(data)


@bp.route('/device/remove/<string:uuid>', methods=['GET'])
def device_remove(uuid):
    try:
        db.get_storage_device_by_id(uuid)
    except KeyError:
        return utils.get_response_error(f"device not found: {uuid}", 404)

    data = device_controller.device_remove(uuid)
    return utils.get_response(data)


@bp.route('/device/<string:uuid>', methods=['POST'])
def device_add(uuid):
    try:
        db.get_storage_device_by_id(uuid)
    except KeyError:
        return utils.get_response_error(f"device not found: {uuid}", 404)

    data = device_controller.add_device(uuid)
    return utils.get_response(data)
