# coding=utf-8
import logging
import time
import sys

from simplyblock_core import constants, kv_store, storage_node_ops
from simplyblock_core.models.nvme_device import NVMeDevice
from simplyblock_core.models.storage_node import StorageNode

# Import the GELF logger
from graypy import GELFUDPHandler

from simplyblock_core.snode_client import SNodeClient

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
db_controller = kv_store.DBController()


logger.info("Starting new device discovery service...")
while True:
    nodes = db_controller.get_storage_nodes()
    for node in nodes:
        auto_restart_devices = []
        online_devices = []
        if node.status != StorageNode.STATUS_ONLINE:
            logger.warning(f"Node status is not online, id: {node.get_id()}, status: {node.status}")
            continue

        snode_api = SNodeClient(node.api_endpoint)
        node_info, _ = snode_api.info()

        known_pcie = [dev.pcie_address for dev in node.nvme_devices]
        node_pcie = node_info['spdk_pcie_list']

        for pc in node_pcie:
            if pc not in known_pcie:
                logger.info(f"New ssd found: {pc}")
                new_dev = storage_node_ops.addNvmeDevices(node, node_info['spdk_pcie_list'])
                new_dev.status = NVMeDevice.STATUS_NEW
                node.nvme_devices.append(new_dev)
                node.write_to_db(db_controller.kv_store)

    time.sleep(constants.DEV_DISCOVERY_INTERVAL_SEC)
