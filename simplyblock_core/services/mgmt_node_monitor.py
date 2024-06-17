# coding=utf-8
import logging
import os

import time
import sys
from datetime import datetime


from simplyblock_core import constants, kv_store, utils
from simplyblock_core.controllers import mgmt_events
from simplyblock_core.models.mgmt_node import MgmtNode

# Import the GELF logger
from graypy import GELFUDPHandler

# configure logging
logger_handler = logging.StreamHandler(stream=sys.stdout)
logger_handler.setFormatter(logging.Formatter('%(asctime)s: %(levelname)s: %(message)s'))
gelf_handler = GELFUDPHandler('0.0.0.0', constants.GELF_PORT)
logger = logging.getLogger()
logger.addHandler(gelf_handler)
logger.addHandler(logger_handler)
logger.setLevel(logging.DEBUG)


# get DB controller
db_store = kv_store.KVStore()
db_controller = kv_store.DBController(kv_store=db_store)

def set_node_online(node):
    if node.status == MgmtNode.STATUS_UNREACHABLE:
        snode = db_controller.get_mgmt_node_by_id(node.get_id())
        old_status = snode.status
        snode.status = MgmtNode.STATUS_ONLINE
        snode.updated_at = str(datetime.now())
        snode.write_to_db(db_store)
        mgmt_events.status_change(snode, snode.status, old_status, caused_by="monitor")


def set_node_offline(node):
    if node.status == MgmtNode.STATUS_ONLINE:
        snode = db_controller.get_mgmt_node_by_id(node.get_id())
        old_status = snode.status
        snode.status = MgmtNode.STATUS_UNREACHABLE
        snode.updated_at = str(datetime.now())
        snode.write_to_db(db_store)
        mgmt_events.status_change(snode, snode.status, old_status, caused_by="monitor")


def ping_host(ip):
    logger.info(f"Pinging ip {ip}")
    response = os.system(f"ping -c 1 {ip}")
    if response == 0:
        logger.info(f"{ip} is UP")
        return True
    else:
        logger.info(f"{ip} is DOWN")
        return False


logger.info("Starting Mgmt node monitor")


while True:
    # get storage nodes
    nodes = db_controller.get_mgmt_nodes()
    for node in nodes:
        if node.status not in [MgmtNode.STATUS_ONLINE, MgmtNode.STATUS_UNREACHABLE]:
            logger.info(f"Node status is: {node.status}, skipping")
            continue

        logger.info(f"Checking node {node.hostname}")
        if not ping_host(node.node_ip):
            logger.info(f"Node {node.hostname} is offline")
            set_node_offline(node)
            continue

        c = utils.get_docker_client()
        nl = c.nodes.list(filters={'role': 'manager'})
        docker_node = None
        for n in nl:
            if n.attrs['Description']['Hostname'] == node.hostname:
                docker_node = n
                break
        if docker_node:
            logger.error("Node is not part of the docker swarm!")
            continue

        if n.attrs['Spec']['Availability'] == 'active':
            set_node_online(node)
        else:
            set_node_online(node)

    logger.info(f"Sleeping for {constants.NODE_MONITOR_INTERVAL_SEC} seconds")
    time.sleep(constants.NODE_MONITOR_INTERVAL_SEC)
