# coding=utf-8
import time
from datetime import datetime, timezone

from simplyblock_core import constants, db_controller, cluster_ops, storage_node_ops, utils
from simplyblock_core.controllers import health_controller, device_controller, tasks_controller
from simplyblock_core.models.cluster import Cluster
from simplyblock_core.models.job_schedule import JobSchedule
from simplyblock_core.models.nvme_device import NVMeDevice, JMDevice
from simplyblock_core.models.storage_node import StorageNode


logger = utils.get_logger(__name__)


# get DB controller
db_controller = db_controller.DBController()


def is_new_migrated_node(cluster_id, node):
    for dev in node.nvme_devices:
        if dev.status == NVMeDevice.STATUS_ONLINE:
            for item in node.lvstore_stack:
                if item["type"] == "bdev_distr":
                    if tasks_controller.get_new_device_mig_task(cluster_id, node.uuid, item["name"],dev.get_id()):
                        return True
    return False


def get_next_cluster_status(cluster_id):
    cluster = db_controller.get_cluster_by_id(cluster_id)
    if cluster.status == cluster.STATUS_UNREADY:
        return Cluster.STATUS_UNREADY
    snodes = db_controller.get_primary_storage_nodes_by_cluster_id(cluster_id)

    online_nodes = 0
    offline_nodes = 0
    affected_nodes = 0
    online_devices = 0
    offline_devices = 0

    for node in snodes:

        node_online_devices = 0
        node_offline_devices = 0

        if node.status == StorageNode.STATUS_IN_CREATION:
            continue

        if node.status == StorageNode.STATUS_ONLINE:
            if is_new_migrated_node(cluster_id, node):
                continue
            online_nodes += 1
        elif node.status == StorageNode.STATUS_REMOVED:
            pass
        else:
            offline_nodes += 1
        for dev in node.nvme_devices:
            if dev.status in [NVMeDevice.STATUS_ONLINE, NVMeDevice.STATUS_JM,
                              NVMeDevice.STATUS_READONLY, NVMeDevice.STATUS_CANNOT_ALLOCATE]:
                node_online_devices += 1
            elif dev.status == NVMeDevice.STATUS_FAILED_AND_MIGRATED:
                pass
            else:
                node_offline_devices += 1

        if node_offline_devices > 0 or (node_online_devices == 0 and node.status != StorageNode.STATUS_REMOVED):
            affected_nodes += 1

        online_devices += node_online_devices
        offline_devices += node_offline_devices


    logger.debug(f"online_nodes: {online_nodes}")
    logger.debug(f"offline_nodes: {offline_nodes}")
    logger.debug(f"affected_nodes: {affected_nodes}")
    logger.debug(f"online_devices: {online_devices}")
    logger.debug(f"offline_devices: {offline_devices}")
    # ndcs n = 2
    # npcs k = 1
    n = cluster.distr_ndcs
    k = cluster.distr_npcs

    # if number of devices in the cluster unavailable on DIFFERENT nodes > k --> I cannot read and in some cases cannot write (suspended)
    if affected_nodes > k:
        return Cluster.STATUS_SUSPENDED
    # if number of devices in the cluster available < n + k --> I cannot write and I cannot read (suspended)
    if online_devices < n + k:
        return Cluster.STATUS_SUSPENDED
    if cluster.strict_node_anti_affinity:
        # if (number of online nodes < number of total nodes - k) --> suspended
        if online_nodes < (len(snodes) - k):
            return Cluster.STATUS_SUSPENDED
        # if (number of online nodes < n+1) --> suspended
        if online_nodes < (n + 1):
            return Cluster.STATUS_SUSPENDED
        # if (number of online nodes < n+2 and k=2) --> suspended
        if online_nodes < (n + 2) and k == 2:
            return Cluster.STATUS_SUSPENDED
    else:
        # if (number of online nodes < number of total nodes - k) --> degraded
        if online_nodes < (len(snodes) - k):
            return Cluster.STATUS_DEGRADED
        # if (number of online nodes < n+1) --> degraded
        if online_nodes < (n + 1):
            return Cluster.STATUS_DEGRADED
        # if (number of online nodes < n+2 and k=2) --> degraded
        if online_nodes < (n + 2) and k == 2:
            return Cluster.STATUS_DEGRADED

    return Cluster.STATUS_ACTIVE


def update_cluster_status(cluster_id):
    cluster = db_controller.get_cluster_by_id(cluster_id)
    current_cluster_status = cluster.status
    logger.info("cluster_status: %s", current_cluster_status)
    if current_cluster_status in [Cluster.STATUS_UNREADY, Cluster.STATUS_IN_ACTIVATION]:
        return

    next_current_status = get_next_cluster_status(cluster_id)
    logger.info("cluster_new_status: %s", next_current_status)

    task_pending = 0
    for task in db_controller.get_job_tasks(cluster_id):
        if task.status != JobSchedule.STATUS_DONE:
            task_pending += 1

    cluster.is_re_balancing = task_pending  > 0
    cluster.write_to_db()

    if current_cluster_status == Cluster.STATUS_DEGRADED and next_current_status == Cluster.STATUS_ACTIVE:
    # if cluster.status not in [Cluster.STATUS_ACTIVE, Cluster.STATUS_UNREADY] and cluster_current_status == Cluster.STATUS_ACTIVE:
        # cluster_ops.cluster_activate(cluster_id, True)
        cluster_ops.set_cluster_status(cluster_id, Cluster.STATUS_ACTIVE)
        return
    elif current_cluster_status == Cluster.STATUS_READONLY and next_current_status in [
        Cluster.STATUS_ACTIVE, Cluster.STATUS_DEGRADED]:
        return
    elif current_cluster_status == Cluster.STATUS_SUSPENDED and next_current_status \
            in [Cluster.STATUS_ACTIVE, Cluster.STATUS_DEGRADED]:
        # needs activation
        # check node statuss, check auto restart for nodes
        can_activate = True
        for node in db_controller.get_storage_nodes_by_cluster_id(cluster_id):
            if node.status not in [StorageNode.STATUS_ONLINE, StorageNode.STATUS_REMOVED]:
                logger.error(f"can not activate cluster: node in not online {node.get_id()}: {node.status}")
                can_activate = False
                break
            if tasks_controller.get_active_node_restart_task(cluster_id, node.get_id()):
                logger.error(f"can not activate cluster: restart tasks found")
                can_activate = False
                break

            if node.online_since:
                diff = datetime.now(timezone.utc) - datetime.fromisoformat(node.online_since)
                if diff.total_seconds() < 30:
                    logger.error(f"can not activate cluster: node is online less than 30 seconds: {node.get_id()}")
                    can_activate = False
                    break

        if can_activate:
            cluster_ops.cluster_activate(cluster_id, force=True)
    else:
        cluster_ops.set_cluster_status(cluster_id, next_current_status)



def set_node_online(node):
    if node.status != StorageNode.STATUS_ONLINE:

        # set node online
        storage_node_ops.set_node_status(node.get_id(), StorageNode.STATUS_ONLINE)

        # set jm dev online
        if node.jm_device.status in [JMDevice.STATUS_UNAVAILABLE, JMDevice.STATUS_ONLINE]:
            device_controller.set_jm_device_state(node.jm_device.get_id(), JMDevice.STATUS_ONLINE)

        # set devices online
        for dev in node.nvme_devices:
            if dev.status == NVMeDevice.STATUS_UNAVAILABLE:
                device_controller.device_set_online(dev.get_id())


def set_node_offline(node):
    if node.status != StorageNode.STATUS_UNREACHABLE:
        # set node unavailable
        storage_node_ops.set_node_status(node.get_id(), StorageNode.STATUS_UNREACHABLE)

        # set devices unavailable
        for dev in node.nvme_devices:
            if dev.status in [NVMeDevice.STATUS_ONLINE, NVMeDevice.STATUS_READONLY]:
                device_controller.device_set_unavailable(dev.get_id())

        # # set jm dev offline
        # if node.jm_device.status != JMDevice.STATUS_UNAVAILABLE:
        #     device_controller.set_jm_device_state(node.jm_device.get_id(), JMDevice.STATUS_UNAVAILABLE)

def set_node_down(node, dev_status):
    if node.status != StorageNode.STATUS_DOWN:
        storage_node_ops.set_node_status(node.get_id(), StorageNode.STATUS_DOWN)
        #
        # if dev_status:
        #     # set devices status
        #     for dev in node.nvme_devices:
        #         if dev.status != dev_status:
        #             device_controller.device_set_state(dev.get_id(), dev_status)


logger.info("Starting node monitor")
while True:
    clusters = db_controller.get_clusters()
    for cluster in clusters:
        cluster_id = cluster.get_id()
        if cluster.status == Cluster.STATUS_IN_ACTIVATION:
            logger.info(f"Cluster status is: {cluster.status}, skipping monitoring")
            continue

        nodes = db_controller.get_storage_nodes_by_cluster_id(cluster_id)
        for snode in nodes:
            if snode.status not in [StorageNode.STATUS_ONLINE, StorageNode.STATUS_UNREACHABLE,
                                    StorageNode.STATUS_SCHEDULABLE, StorageNode.STATUS_DOWN]:
                logger.info(f"Node status is: {snode.status}, skipping")
                continue

            if snode.status == StorageNode.STATUS_ONLINE and snode.lvstore_status == "in_creation":
                logger.info(f"Node lvstore is in creation: {snode.get_id()}, skipping")
                continue

            logger.info(f"Checking node {snode.hostname}")

            # 1- check node ping
            ping_check = health_controller._check_node_ping(snode.mgmt_ip)
            logger.info(f"Check: ping mgmt ip {snode.mgmt_ip} ... {ping_check}")
            if not ping_check:
                time.sleep(1)
                ping_check = health_controller._check_node_ping(snode.mgmt_ip)
                logger.info(f"Check 2: ping mgmt ip {snode.mgmt_ip} ... {ping_check}")

            # 2- check node API
            node_api_check = health_controller._check_node_api(snode.mgmt_ip)
            logger.info(f"Check: node API {snode.mgmt_ip}:5000 ... {node_api_check}")

            if snode.status == StorageNode.STATUS_SCHEDULABLE and not ping_check and not node_api_check:
                continue

            spdk_process = False
            if node_api_check:
                # 3- check spdk_process
                spdk_process = health_controller._check_spdk_process_up(snode.mgmt_ip)
            logger.info(f"Check: spdk process {snode.mgmt_ip}:5000 ... {spdk_process}")

                # 4- check rpc
            node_rpc_check = health_controller._check_node_rpc(
                snode.mgmt_ip, snode.rpc_port, snode.rpc_username, snode.rpc_password, timeout=5, retry=3)
            logger.info(f"Check: node RPC {snode.mgmt_ip}:{snode.rpc_port} ... {node_rpc_check}")

            node_port_check = True
            down_ports = []
            if spdk_process and snode.lvstore_status == "ready":
                if snode.is_secondary_node:
                    ports = [4420]
                    for n in db_controller.get_primary_storage_nodes_by_secondary_node_id(snode.get_id()):
                        if n.lvstore_status == "ready":
                            ports.append(n.lvol_subsys_port)
                else:
                    ports = [snode.lvol_subsys_port, 4420]

                for port in ports:
                    ret = health_controller._check_port_on_node(snode, port)
                    logger.info(f"Check: node port {snode.mgmt_ip}, {port} ... {ret}")
                    node_port_check &= ret
                    if not ret and port == 4420:
                        down_ports.append(port)

                for data_nic in snode.data_nics:
                    if data_nic.ip4_address:
                        data_ping_check = health_controller._check_node_ping(data_nic.ip4_address)
                        logger.info(f"Check: ping data nic {data_nic.ip4_address} ... {data_ping_check}")
                        if not data_ping_check:
                            node_port_check = False
                            down_ports.append(data_nic.ip4_address)

            is_node_online = ping_check and node_api_check and spdk_process and node_rpc_check and node_port_check
            if is_node_online:
                set_node_online(snode)

                # check JM device
                if snode.jm_device:
                    if snode.jm_device.status in [JMDevice.STATUS_ONLINE, JMDevice.STATUS_UNAVAILABLE]:
                        ret = health_controller.check_jm_device(snode.jm_device.get_id())
                        if ret:
                            logger.info(f"JM bdev is online: {snode.jm_device.get_id()}")
                            if snode.jm_device.status != JMDevice.STATUS_ONLINE:
                                device_controller.set_jm_device_state(snode.jm_device.get_id(), JMDevice.STATUS_ONLINE)
                        else:
                            logger.error(f"JM bdev is offline: {snode.jm_device.get_id()}")
                            if snode.jm_device.status != JMDevice.STATUS_UNAVAILABLE:
                                device_controller.set_jm_device_state(snode.jm_device.get_id(),
                                                                      JMDevice.STATUS_UNAVAILABLE)
            else:

                cluster = db_controller.get_cluster_by_id(cluster.get_id())
                if not ping_check and not node_api_check and not spdk_process:
                    # restart on new node
                    storage_node_ops.set_node_status(snode.get_id(), StorageNode.STATUS_SCHEDULABLE)
                    # set devices unavailable
                    for dev in snode.nvme_devices:
                        if dev.status in [NVMeDevice.STATUS_ONLINE, NVMeDevice.STATUS_READONLY]:
                            device_controller.device_set_unavailable(dev.get_id())

                elif ping_check and node_api_check and (not spdk_process or not node_rpc_check):
                    # add node to auto restart
                    if cluster.status in [Cluster.STATUS_ACTIVE, Cluster.STATUS_DEGRADED,
                                          Cluster.STATUS_SUSPENDED, Cluster.STATUS_READONLY]:
                        set_node_offline(snode)
                        tasks_controller.add_node_to_auto_restart(snode)
                elif not node_port_check:
                    if cluster.status in [Cluster.STATUS_ACTIVE, Cluster.STATUS_DEGRADED, Cluster.STATUS_READONLY]:
                        logger.error(f"Port check failed")
                        if down_ports:
                            set_node_down(snode, dev_status=NVMeDevice.STATUS_UNAVAILABLE)
                        else:
                            set_node_down(snode, dev_status=NVMeDevice.STATUS_ONLINE)

                else:
                    set_node_offline(snode)

        update_cluster_status(cluster_id)

    logger.info(f"Sleeping for {constants.NODE_MONITOR_INTERVAL_SEC} seconds")
    time.sleep(constants.NODE_MONITOR_INTERVAL_SEC)
