# coding=utf-8
import time

from simplyblock_core import constants, db_controller, utils
from simplyblock_core.models.nvme_device import NVMeDevice
from simplyblock_core.models.storage_node import StorageNode
from simplyblock_core.rpc_client import RPCClient
from simplyblock_core.models.stats import DeviceStatObject, NodeStatObject, ClusterStatObject

logger = utils.get_logger(__name__)


last_object_record: dict[str, DeviceStatObject] = {}


def add_device_stats(cl, device, capacity_dict, stats_dict):
    now = int(time.time())
    data = {
        "cluster_id": cl.get_id(),
        "uuid": device.get_id(),
        "date": now}

    if capacity_dict and capacity_dict['res'] == 1:
        size_total = int(capacity_dict['npages_nmax']*capacity_dict['pba_page_size'])
        size_used = int(capacity_dict['npages_used']*capacity_dict['pba_page_size'])
        size_free = size_total - size_used
        size_util = 0
        if size_total > 0:
            size_util = int((size_used / size_total) * 100)

        data.update({
            "size_total": size_total,
            "size_used": size_used,
            "size_free": size_free,
            "size_util": size_util,
            "capacity_dict": capacity_dict
        })
    else:
        logger.error(f"Error getting Alceml capacity, response={capacity_dict}")

    if stats_dict:
        stats = stats_dict
        data.update({
            "read_bytes": stats['bytes_read'],
            "read_io": stats['num_read_ops'],
            "read_latency_ticks": stats['read_latency_ticks'],

            "write_bytes": stats['bytes_written'],
            "write_io": stats['num_write_ops'],
            "write_latency_ticks": stats['write_latency_ticks'],

            "unmap_bytes": stats['bytes_unmapped'],
            "unmap_io": stats['num_unmap_ops'],
            "unmap_latency_ticks": stats['unmap_latency_ticks'],
        })

        if device.get_id() in last_object_record:
            last_record = last_object_record[device.get_id()]
        else:
            last_record = DeviceStatObject(data={"uuid": device.get_id(), "cluster_id": cl.get_id()}
                                           ).get_last(db.kv_store)
        if last_record:
            time_diff = (now - last_record.date)
            if time_diff > 0:
                data['read_bytes_ps'] = int((data['read_bytes'] - last_record['read_bytes']) / time_diff)
                data['read_io_ps'] = int((data['read_io'] - last_record['read_io']) / time_diff)
                data['read_latency_ps'] = int((data['read_latency_ticks'] - last_record['read_latency_ticks']) / time_diff)

                data['write_bytes_ps'] = int((data['write_bytes'] - last_record['write_bytes']) / time_diff)
                data['write_io_ps'] = int((data['write_io'] - last_record['write_io']) / time_diff)
                data['write_latency_ps'] = int((data['write_latency_ticks'] - last_record['write_latency_ticks']) / time_diff)

                data['unmap_bytes_ps'] = int((data['unmap_bytes'] - last_record['unmap_bytes']) / time_diff)
                data['unmap_io_ps'] = int((data['unmap_io'] - last_record['unmap_io']) / time_diff)
                data['unmap_latency_ps'] = int((data['unmap_latency_ticks'] - last_record['unmap_latency_ticks']) / time_diff)

        else:
            logger.warning("last record not found")
    else:
        logger.error("Error getting stats")

    stat_obj = DeviceStatObject(data=data)
    stat_obj.write_to_db(db.kv_store)
    last_object_record[device.get_id()] = stat_obj

    return stat_obj


def add_node_stats(node, records):
    size_used = 0
    size_total = 0
    data = {}
    if records:
        records_sum = utils.sum_records(records)
        size_total = records_sum.size_total
        size_used = records_sum.size_used
        data.update(records_sum.get_clean_dict())

    size_prov = 0
    for lvol in db.get_lvols_by_node_id(node.get_id()):
        size_prov += lvol.size

    size_util = 0
    size_prov_util = 0
    if size_total > 0:
        size_util = int((size_used / size_total) * 100)
        size_prov_util = int((size_prov / size_total) * 100)

    data.update({
        "cluster_id": cl.get_id(),
        "uuid": node.get_id(),
        "date": int(time.time()),
        "size_util": size_util,
        "size_prov": size_prov,
        "size_prov_util": size_prov_util
    })
    stat_obj = NodeStatObject(data=data)
    stat_obj.write_to_db(db.kv_store)

    return stat_obj


def add_cluster_stats(cl, records):

    if not records:
        return False

    records_sum = utils.sum_records(records)

    size_util = 0
    size_prov_util = 0
    if records_sum.size_total > 0:
        size_util = int((records_sum.size_used / records_sum.size_total) * 100)
        size_prov_util = int((records_sum.size_prov / records_sum.size_total) * 100)

    data = records_sum.get_clean_dict()
    data.update({
        "cluster_id": cl.get_id(),
        "uuid": cl.get_id(),
        "date": int(time.time()),

        "size_util": size_util,
        "size_prov_util": size_prov_util
    })

    stat_obj = ClusterStatObject(data=data)
    stat_obj.write_to_db(db.kv_store)

    return stat_obj



# get DB controller
db = db_controller.DBController()

logger.info("Starting capacity and stats collector...")
while True:

    clusters = db.get_clusters()
    for cl in clusters:
        snodes = db.get_storage_nodes_by_cluster_id(cl.get_id())
        if not snodes:
            logger.error(f"Cluster has no storage nodes: {cl.get_id()}")

        node_records = []
        for node in snodes:
            logger.info("Node: %s", node.get_id())
            if node.status != StorageNode.STATUS_ONLINE:
                logger.info("Node is not online, skipping")
                continue

            if not node.nvme_devices:
                logger.error("No devices found in node: %s", node.get_id())
                continue

            rpc_client = RPCClient(
                node.mgmt_ip, node.rpc_port,
                node.rpc_username, node.rpc_password,
                timeout=5, retry=2)

            node_devs_stats = {}
            ret = rpc_client.get_lvol_stats()
            if ret:
                node_devs_stats = {b['name']: b for b in ret['bdevs']}

            devices_records = []
            for device in node.nvme_devices:
                logger.info("Getting device stats: %s", device.uuid)
                if device.status not in [NVMeDevice.STATUS_ONLINE, NVMeDevice.STATUS_READONLY, NVMeDevice.STATUS_CANNOT_ALLOCATE]:
                    logger.info(f"Device is skipped: {device.get_id()} status: {device.status}")
                    continue
                capacity_dict = rpc_client.alceml_get_capacity(device.alceml_name)
                if device.nvme_bdev in node_devs_stats:
                    stats_dict = node_devs_stats[device.nvme_bdev]
                    record = add_device_stats(cl, device, capacity_dict, stats_dict)
                    if record:
                        devices_records.append(record)

            node_record = add_node_stats(node, devices_records)
            node_records.append(node_record)

        add_cluster_stats(cl, node_records)

    time.sleep(constants.DEV_STAT_COLLECTOR_INTERVAL_SEC)
