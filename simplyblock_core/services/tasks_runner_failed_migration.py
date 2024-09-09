# coding=utf-8
import logging
import time
import sys


from simplyblock_core import constants, kv_store
from simplyblock_core.controllers import tasks_events, tasks_controller
from simplyblock_core.models.job_schedule import JobSchedule


# Import the GELF logger
from graypy import GELFUDPHandler

from simplyblock_core.models.nvme_device import NVMeDevice
from simplyblock_core.models.storage_node import StorageNode
from simplyblock_core.rpc_client import RPCClient


def task_runner(task):

    snode = db_controller.get_storage_node_by_id(task.node_id)
    rpc_client = RPCClient(snode.mgmt_ip, snode.rpc_port, snode.rpc_username, snode.rpc_password, timeout=5, retry=2)

    if task.canceled:
        task.function_result = "canceled"
        task.status = JobSchedule.STATUS_DONE
        task.write_to_db(db_controller.kv_store)
        return True

    if task.status == JobSchedule.STATUS_NEW:
        task.status = JobSchedule.STATUS_RUNNING
        task.write_to_db(db_controller.kv_store)
        tasks_events.task_updated(task)

    if snode.status != StorageNode.STATUS_ONLINE:
        task.function_result = "node is not online, retrying"
        task.retry += 1
        task.write_to_db(db_controller.kv_store)
        return False

    if "migration" not in task.function_params:
        # if task.retry >= 2:
        #     all_devs_online = True
        #     for node in db_controller.get_storage_nodes_by_cluster_id(task.cluster_id):
        #         for dev in node.nvme_devices:
        #             if dev.status != NVMeDevice.STATUS_ONLINE:
        #                 all_devs_online = False
        #                 break
        #
        #     if not all_devs_online:
        #         task.function_result = "Some devs are offline, retrying"
        #         task.retry += 1
        #         task.write_to_db(db_controller.kv_store)
        #         return False

        device = db_controller.get_storage_devices(task.device_id)
        lvol = db_controller.get_lvol_by_id(task.function_params["lvol_id"])
        rsp = rpc_client.distr_migration_failure_start(lvol.base_bdev, device.cluster_device_order)
        if not rsp:
            logger.error(f"Failed to start device migration task, storage_ID: {device.cluster_device_order}")
            task.function_result = "Failed to start device migration task"
            task.retry += 1
            task.write_to_db(db_controller.kv_store)
            return False

        task.function_params = {
            "migration": {
                "name": lvol.base_bdev}
        }
        task.write_to_db(db_controller.kv_store)
        time.sleep(3)

    if "migration" in task.function_params:

        mig_info = task.function_params["migration"]
        res = rpc_client.distr_migration_status(**mig_info)
        if res:
            migration_status = res[0]["status"]
            if migration_status == "completed":
                task.status = JobSchedule.STATUS_DONE
                task.function_result = migration_status
                task.write_to_db(db_controller.kv_store)
                return True

            elif migration_status == "failed":
                task.status = JobSchedule.STATUS_DONE
                task.function_result = migration_status
                task.write_to_db(db_controller.kv_store)
                return True

            else:
                task.function_result = f"Status: {migration_status}, progress:{res[0]['progress']}"
                task.write_to_db(db_controller.kv_store)
        else:
            logger.error("Failed to get mig status")

    task.retry += 1
    task.write_to_db(db_controller.kv_store)
    return False


# configure logging
logger_handler = logging.StreamHandler(stream=sys.stdout)
logger_handler.setFormatter(logging.Formatter('%(asctime)s: %(levelname)s: %(message)s'))
gelf_handler = GELFUDPHandler('0.0.0.0', constants.GELF_PORT)
logger = logging.getLogger()
logger.addHandler(gelf_handler)
logger.addHandler(logger_handler)
logger.setLevel(logging.DEBUG)

# get DB controller
db_controller = kv_store.DBController()
logger.info("Starting Tasks runner...")
while True:
    time.sleep(3)
    clusters = db_controller.get_clusters()
    if not clusters:
        logger.error("No clusters found!")
    else:
        for cl in clusters:
            tasks = db_controller.get_job_tasks(cl.get_id(), reverse=False)
            for task in tasks:
                delay_seconds = 5
                if task.function_name == JobSchedule.FN_FAILED_DEV_MIG:
                    if task.status == JobSchedule.STATUS_NEW:
                        active_task = tasks_controller.get_active_node_mig_task(task.cluster_id, task.node_id)
                        if active_task:
                            logger.info("task found on same node, retry")
                            continue
                    if task.status != JobSchedule.STATUS_DONE:
                        # get new task object because it could be changed from cancel task
                        task = db_controller.get_task_by_id(task.uuid)
                        res = task_runner(task)
                        if res:
                            tasks_events.task_updated(task)
                        else:
                            time.sleep(delay_seconds)
