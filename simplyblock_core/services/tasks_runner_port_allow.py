# coding=utf-8
import time


from simplyblock_core import db_controller, utils, storage_node_ops, distr_controller
from simplyblock_core.controllers import tasks_events, tcp_ports_events, health_controller
from simplyblock_core.fw_api_client import FirewallClient
from simplyblock_core.models.job_schedule import JobSchedule
from simplyblock_core.models.cluster import Cluster
from simplyblock_core.models.storage_node import StorageNode
from simplyblock_core.snode_client import SNodeClient

logger = utils.get_logger(__name__)

# get DB controller
db = db_controller.DBController()


logger.info("Starting Tasks runner...")
while True:

    clusters = db.get_clusters()
    if not clusters:
        logger.error("No clusters found!")
    else:
        for cl in clusters:
            if cl.status == Cluster.STATUS_IN_ACTIVATION:
                continue

            tasks = db.get_job_tasks(cl.get_id(), reverse=False)
            for task in tasks:

                if task.function_name == JobSchedule.FN_PORT_ALLOW:
                    if task.status != JobSchedule.STATUS_DONE:

                        # get new task object because it could be changed from cancel task
                        task = db.get_task_by_id(task.uuid)

                        if task.canceled:
                            task.function_result = "canceled"
                            task.status = JobSchedule.STATUS_DONE
                            task.write_to_db(db.kv_store)
                            continue

                        node = db.get_storage_node_by_id(task.node_id)

                        if not node:
                            task.function_result = "node not found"
                            task.status = JobSchedule.STATUS_DONE
                            task.write_to_db(db.kv_store)
                            continue

                        if node.status not in [StorageNode.STATUS_DOWN, StorageNode.STATUS_ONLINE]:
                            msg = f"Node is {node.status}, retry task"
                            logger.info(msg)
                            task.function_result = msg
                            task.status = JobSchedule.STATUS_SUSPENDED
                            task.write_to_db(db.kv_store)
                            continue

                        logger.info("connecting remote devices")
                        node.remote_devices = storage_node_ops._connect_to_remote_devs(
                            node, force_conect_restarting_nodes=True)
                        node.write_to_db()

                        logger.info("Sending device status event")
                        for db_dev in node.nvme_devices:
                            distr_controller.send_dev_status_event(db_dev, db_dev.status)

                        lvstore_check = True
                        if node.lvstore_status == "ready":
                            lvstore_check &= health_controller._check_node_lvstore(node.lvstore_stack, node, auto_fix=True)
                            if node.secondary_node_id:
                                lvstore_check &= health_controller._check_node_hublvol(node)

                        if lvstore_check is False:
                            msg = "Node LVolStore check fail, retry later"
                            logger.warning(msg)
                            task.function_result = msg
                            task.status = JobSchedule.STATUS_SUSPENDED
                            task.write_to_db(db.kv_store)
                            continue

                        if task.status != JobSchedule.STATUS_RUNNING:
                            task.status = JobSchedule.STATUS_RUNNING
                            task.write_to_db(db.kv_store)

                        port_number = task.function_params["port_number"]
                        snode_api = SNodeClient(f"{node.mgmt_ip}:5000", timeout=3, retry=2)

                        sec_node = db.get_storage_node_by_id(node.secondary_node_id)
                        if sec_node and sec_node.status == StorageNode.STATUS_ONLINE:
                            sec_rpc_client = sec_node.rpc_client()
                            sec_rpc_client.bdev_lvol_set_leader(node.lvstore, leader=False, bs_nonleadership=True)

                        logger.info(f"Allow port {port_number} on node {node.get_id()}")

                        fw_api = FirewallClient(f"{node.mgmt_ip}:5001", timeout=5, retry=2)
                        fw_api.firewall_set_port(port_number, "tcp", "allow", node.rpc_port)
                        tcp_ports_events.port_allowed(node, port_number)

                        task.function_result = f"Port {port_number} allowed on node"
                        task.status = JobSchedule.STATUS_DONE
                        task.write_to_db(db.kv_store)
                        tasks_events.task_updated(task)

    time.sleep(5)
