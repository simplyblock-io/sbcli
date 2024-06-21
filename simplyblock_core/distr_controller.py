# coding=utf-8
import datetime
import logging
import re

from simplyblock_core.models.nvme_device import NVMeDevice
from simplyblock_core.rpc_client import RPCClient
from simplyblock_core.kv_store import DBController

logger = logging.getLogger()


def send_node_status_event(node, node_status):
    db_controller = DBController()
    node_id = node.get_id()
    logging.info(f"Sending event updates, node: {node_id}, status: {node_status}")
    node_status_event = {
        "timestamp": datetime.datetime.now().isoformat("T", "seconds") + 'Z',
        "event_type": "node_status",
        "UUID_node": node_id,
        "status": node_status}
    events = {"events": [node_status_event]}
    logger.debug(node_status_event)
    snodes = db_controller.get_storage_nodes_by_cluster_id(node.cluster_id)
    for node in snodes:
        if node.status != node.STATUS_ONLINE:
            continue
        logger.info(f"Sending to: {node.get_id()}")
        rpc_client = RPCClient(node.mgmt_ip, node.rpc_port, node.rpc_username, node.rpc_password)
        ret = rpc_client.distr_status_events_update(events)


def send_dev_status_event(device, dev_status):
    db_controller = DBController()
    storage_ID = device.cluster_device_order
    logging.info(f"Sending event updates, device: {storage_ID}, status: {dev_status}")
    node_status_event = {
        "timestamp": datetime.datetime.now().isoformat("T", "seconds") + 'Z',
        "event_type": "device_status",
        "storage_ID": storage_ID,
        "status": dev_status}
    events = {"events": [node_status_event]}
    logger.debug(node_status_event)
    snodes = db_controller.get_storage_nodes_by_cluster_id(device.cluster_id)
    for node in snodes:
        if node.status != node.STATUS_ONLINE:
            continue
        logger.info(f"Sending to: {node.get_id()}")
        rpc_client = RPCClient(node.mgmt_ip, node.rpc_port, node.rpc_username, node.rpc_password)
        ret = rpc_client.distr_status_events_update(events)
        if not ret:
            logger.warning("Failed to send event update")


def disconnect_device(device):
    db_controller = DBController()
    snodes = db_controller.get_storage_nodes_by_cluster_id(device.cluster_id)
    for node in snodes:
        if node.status != node.STATUS_ONLINE:
            continue
        new_remote_devices = []
        rpc_client = RPCClient(node.mgmt_ip, node.rpc_port, node.rpc_username, node.rpc_password)
        for rem_dev in node.remote_devices:
            if rem_dev.get_id() == device.get_id():
                ctrl_name = rem_dev.remote_bdev[:-2]
                rpc_client.bdev_nvme_detach_controller(ctrl_name)
            else:
                new_remote_devices.append(rem_dev)
        node.remote_devices = new_remote_devices
        node.write_to_db(db_controller.kv_store)


def get_distr_cluster_map(snodes, target_node):
    map_cluster = {}
    map_prob = []
    for snode in snodes:
        dev_map = {}
        dev_w_map = []
        node_w = 0
        for i, dev in enumerate(snode.nvme_devices):
            logger.debug(f"Device: {dev.get_id()}, status: {dev.status}")
            if dev.status in [NVMeDevice.STATUS_JM, NVMeDevice.STATUS_NEW]:
                continue

            dev_w = int(dev.size/(1024*1024*1024)) or 1
            node_w += dev_w
            name = None
            if snode.get_id() == target_node.get_id():
                name = dev.alceml_bdev
            else:
                for dev2 in target_node.remote_devices:
                    if dev2.get_id() == dev.get_id():
                        name = dev2.remote_bdev
                        break
            if not name:
                name = f"remote_{dev.alceml_bdev}n1"
            dev_map[dev.cluster_device_order] = {
                "UUID": dev.get_id(),
                "bdev_name": name,
                "status": dev.status,
                "physical_label": dev.physical_label
            }
            dev_w_map.append({
                "weight": dev_w,
                "id": dev.cluster_device_order})
        map_cluster[snode.get_id()] = {
            "status": snode.status,
            "devices": dev_map}
        map_prob.append({
            "weight": node_w,
            "items": dev_w_map
        })
    cl_map = {
        "name": "",
        "UUID_node_target": "",
        "timestamp": datetime.datetime.now().isoformat("T", "seconds")+'Z',
        "map_cluster": map_cluster,
        "map_prob": map_prob
    }
    return cl_map


def parse_distr_cluster_map(map_string):
    db_controller = DBController()
    node_pattern = re.compile(r".*uuid_node=(.*)  status=(.*)$", re.IGNORECASE)
    device_pattern = re.compile(
        r".*storage_ID=(.*)  status=(.*)  uuid_device=(.*)  storage_bdev_name=(.*)$", re.IGNORECASE)

    results = []
    passed = True
    for line in map_string.split('\n'):
        line = line.strip()
        m = node_pattern.match(line)
        if m:
            node_id, status = m.groups()
            data = {
                "Kind": "Node",
                "UUID": node_id,
                "Reported Status": status,
                "Actual Status": "",
                "Results": "",
            }
            nd = db_controller.get_storage_node_by_id(node_id)
            if nd:
                data["Actual Status"] = nd.status
                if nd.status == status:
                    data["Results"] = "ok"
                else:
                    data["Results"] = "failed"
                    passed = False
            else:
                data["Results"] = "not found"
                passed = False
            results.append(data)
        m = device_pattern.match(line)
        if m:
            storage_id, status, device_id, bdev_name = m.groups()
            data = {
                "Kind": "Device",
                "UUID": device_id,
                "Reported Status": status,
                "Actual Status": "",
                "Results": "",
            }
            sd = db_controller.get_storage_device_by_id(device_id)
            if sd:
                data["Actual Status"] = sd.status
                if sd.status == status:
                    data["Results"] = "ok"
                else:
                    data["Results"] = "failed"
                    passed = False
            else:
                data["Results"] = "not found"
                passed = False
            results.append(data)
    return results, passed


def send_cluster_map_to_node(node):
    db_controller = DBController()
    snodes = db_controller.get_storage_nodes_by_cluster_id(node.cluster_id)
    rpc_client = RPCClient(node.mgmt_ip, node.rpc_port, node.rpc_username, node.rpc_password)
    cluster_map_data = get_distr_cluster_map(snodes, node)
    cluster_map_data['UUID_node_target'] = node.get_id()
    ret = rpc_client.distr_send_cluster_map(cluster_map_data)
    if not ret:
        logger.error("Failed to send cluster map")
        logger.info(cluster_map_data)
        return False
    return True


def send_cluster_map_add_node(snode):
    db_controller = DBController()
    snodes = db_controller.get_storage_nodes_by_cluster_id(snode.cluster_id)
    for node in snodes:
        if node.status != node.STATUS_ONLINE:
            continue
        logger.info(f"Sending to: {node.get_id()}")
        rpc_client = RPCClient(node.mgmt_ip, node.rpc_port, node.rpc_username, node.rpc_password)

        cluster_map_data = get_distr_cluster_map([snode], node)
        cl_map = {
            "map_cluster": cluster_map_data['map_cluster'],
            "map_prob": cluster_map_data['map_prob']}
        ret = rpc_client.distr_add_nodes(cl_map)
        if not ret:
            logger.error("Failed to send cluster map")
            logger.info(cl_map)
            return False
    return True
