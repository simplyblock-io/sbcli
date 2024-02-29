# coding=utf-8
import logging
import os

import time
import sys
from datetime import datetime


from simplyblock_core.controllers import health_controller, storage_events
from simplyblock_core.models.storage_node import StorageNode
from simplyblock_core.rpc_client import RPCClient
from simplyblock_core import constants, kv_store


def set_node_status(snode, target_status):
    if target_status == StorageNode.STATUS_ONLINE:
        if snode.status == StorageNode.STATUS_ONLINE:
            return
    if target_status == StorageNode.STATUS_UNREACHABLE:
        if snode.status == StorageNode.STATUS_UNREACHABLE:
            return
    snode = db_controller.get_storage_node_by_id(snode.get_id())
    old_status = snode.status
    snode.status = target_status
    snode.updated_at = str(datetime.now())
    snode.write_to_db(db_store)
    storage_events.snode_status_change(snode, snode.status, old_status, caused_by="monitor")


def set_node_health_check(snode, health_check_status):
    snode = db_controller.get_storage_node_by_id(snode.get_id())
    if snode.health_check == health_check_status:
        return
    old_status = snode.health_check
    snode.health_check = health_check_status
    snode.updated_at = str(datetime.now())
    snode.write_to_db(db_store)
    storage_events.snode_health_check_change(snode, snode.health_check, old_status, caused_by="monitor")


def set_device_health_check(cluster_id, device, health_check_status):
    device = db_controller.get_storage_devices(device.get_id())
    if device.health_check == health_check_status:
        return
    old_status = device.health_check
    device.health_check = health_check_status
    device.updated_at = str(datetime.now())
    device.write_to_db(db_store)
    # todo: set device offline or online
    storage_events.device_health_check_change(cluster_id, device, device.health_check, old_status, caused_by="monitor")


def set_lvol_health_check(cluster_id, lvol, health_check_status):
    lvol = db_controller.get_lvol_by_id(lvol.get_id())
    if lvol.health_check == health_check_status:
        return
    old_status = lvol.health_check
    lvol.health_check = health_check_status
    lvol.updated_at = str(datetime.now())
    lvol.write_to_db(db_store)
    # todo: set lvol offline or online
    storage_events.lvol_health_check_change(cluster_id, lvol, lvol.health_check, old_status, caused_by="monitor")


# configure logging
logger_handler = logging.StreamHandler(stream=sys.stdout)
logger_handler.setFormatter(logging.Formatter('%(asctime)s: %(levelname)s: %(message)s'))
logger = logging.getLogger()
logger.addHandler(logger_handler)
logger.setLevel(logging.DEBUG)

# get DB controller
db_store = kv_store.KVStore()
db_controller = kv_store.DBController()

logger.info("Starting health check service")
while True:
    cluster_id = ""
    cl = db_controller.get_clusters()
    if cl:
        cluster_id = cl[0].get_id()

    snodes = db_controller.get_storage_nodes()
    if not snodes:
        logger.error("storage nodes list is empty")

    for snode in snodes:
        logger.info("Node: %s", snode.get_id())

        # 1- check node ping
        ping_check = health_controller._check_node_ping(snode.mgmt_ip)
        logger.info(f"Check: ping mgmt ip {snode.mgmt_ip} ... {ping_check}")

        # 2- check node API
        node_api_check = health_controller._check_node_api(snode.mgmt_ip)
        logger.info(f"Check: node API {snode.mgmt_ip}:5000 ... {node_api_check}")

        # 3- check node RPC
        node_rpc_check = health_controller._check_node_rpc(
            snode.mgmt_ip, snode.rpc_port, snode.rpc_username, snode.rpc_password)
        logger.info(f"Check: node RPC {snode.mgmt_ip}:{snode.rpc_port} ... {node_rpc_check}")

        # 4- docker API
        node_docker_check = health_controller._check_node_docker_api(snode.mgmt_ip)
        logger.info(f"Check: node docker API {snode.mgmt_ip}:2375 ... {node_docker_check}")

        is_node_online = ping_check and node_api_check and node_rpc_check and node_docker_check
        # if is_node_online:
        #     set_node_status(snode, StorageNode.STATUS_ONLINE)
        # else:
        #     set_node_status(snode, StorageNode.STATUS_UNREACHABLE)

        health_check_status = is_node_online
        if not node_rpc_check:
            logger.info("Skipping devices checks because RPC check failed")
        else:
            logger.info(f"Node device count: {len(snode.nvme_devices)}")
            node_devices_check = True
            node_remote_devices_check = True

            for dev in snode.nvme_devices:
                ret = health_controller.check_device(dev.get_id())
                set_device_health_check(cluster_id, dev, ret)
                node_devices_check &= ret

            logger.info(f"Node remote device: {len(snode.remote_devices)}")
            rpc_client = RPCClient(
                snode.mgmt_ip, snode.rpc_port,
                snode.rpc_username, snode.rpc_password,
                timeout=3, retry=1)
            for remote_device in snode.remote_devices:
                ret = rpc_client.get_bdevs(remote_device.remote_bdev)
                if ret:
                    logger.info(f"Checking bdev: {remote_device.remote_bdev} ... ok")
                else:
                    logger.info(f"Checking bdev: {remote_device.remote_bdev} ... not found")
                node_remote_devices_check &= bool(ret)

            health_check_status = is_node_online and node_devices_check and node_remote_devices_check
        set_node_health_check(snode, health_check_status)

    for lvol in db_controller.get_lvols():
        ret = health_controller.check_lvol(lvol.get_id())
        set_lvol_health_check(cluster_id, lvol, ret)

    time.sleep(constants.HEALTH_CHECK_INTERVAL_SEC)

