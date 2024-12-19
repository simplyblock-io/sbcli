# coding=utf-8
import datetime
import json
import logging
import time
import uuid

from simplyblock_core import db_controller, constants, utils
from simplyblock_core.controllers import tasks_events, device_controller
from simplyblock_core.models.job_schedule import JobSchedule
from simplyblock_core.models.storage_node import StorageNode

logger = logging.getLogger()
db_controller = db_controller.DBController()


def _validate_new_task_dev_restart(cluster_id, node_id, device_id):
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        if task.function_name == JobSchedule.FN_DEV_RESTART and task.device_id == device_id and task.canceled is False:
            if task.status != JobSchedule.STATUS_DONE:
                logger.info(f"Task found, skip adding new task: {task.get_id()}")
                return False
        elif task.function_name == JobSchedule.FN_NODE_RESTART and task.node_id == node_id and task.canceled is False:
            if task.status != JobSchedule.STATUS_DONE:
                logger.info(f"Task found, skip adding new task: {task.get_id()}")
                return False
    return True


def _validate_new_task_node_restart(cluster_id, node_id):
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        if task.function_name == JobSchedule.FN_NODE_RESTART and task.node_id == node_id and task.canceled is False:
            if task.status != JobSchedule.STATUS_DONE:
                return task.get_id()
    return False


def _add_task(function_name, cluster_id, node_id, device_id,
              max_retry=constants.TASK_EXEC_RETRY_COUNT, function_params=None):

    if function_name in [JobSchedule.FN_DEV_RESTART, JobSchedule.FN_DEV_MIG, JobSchedule.FN_FAILED_DEV_MIG]:
        if not _validate_new_task_dev_restart(cluster_id, node_id, device_id):
            return False
    elif function_name == JobSchedule.FN_NODE_RESTART:
        task_id = _validate_new_task_node_restart(cluster_id, node_id)
        if task_id:
            logger.info(f"Task found, skip adding new task: {task_id}")
            return False
    elif function_name == JobSchedule.FN_NEW_DEV_MIG:
        task_id = get_new_device_mig_task(cluster_id, node_id, function_params['distr_name'])
        if task_id:
            logger.info(f"Task found, skip adding new task: {task_id}")
            return False
    elif function_name == JobSchedule.FN_DEV_MIG:
        task_id = get_device_mig_task(cluster_id, node_id, function_params['distr_name'])
        if task_id:
            logger.info(f"Task found, skip adding new task: {task_id}")
            return False

    task_obj = JobSchedule()
    task_obj.uuid = str(uuid.uuid4())
    task_obj.cluster_id = cluster_id
    task_obj.node_id = node_id
    task_obj.device_id = device_id
    task_obj.date = int(time.time())
    task_obj.function_name = function_name
    if function_params and type(function_params) is dict:
        task_obj.function_params = function_params
    task_obj.max_retry = max_retry
    task_obj.status = JobSchedule.STATUS_NEW
    task_obj.write_to_db(db_controller.kv_store)
    tasks_events.task_create(task_obj)
    return task_obj.uuid


def add_device_mig_task(device_id):
    device = db_controller.get_storage_device_by_id(device_id)
    for node in db_controller.get_storage_nodes_by_cluster_id(device.cluster_id):
        if node.status == StorageNode.STATUS_REMOVED:
            continue
        if not node.lvols:
            continue
        for bdev in node.lvstore_stack:
            if bdev['type'] == "bdev_distr":
                _add_task(JobSchedule.FN_DEV_MIG, device.cluster_id, node.get_id(), device.get_id(),
                          max_retry=-1, function_params={'distr_name': bdev['name']})
    return True


def add_device_to_auto_restart(device):
    return _add_task(JobSchedule.FN_DEV_RESTART, device.cluster_id, device.node_id, device.get_id())


def add_node_to_auto_restart(node):
    return _add_task(JobSchedule.FN_NODE_RESTART, node.cluster_id, node.get_id(), "")


def list_tasks(cluster_id, is_json=False):
    cluster = db_controller.get_cluster_by_id(cluster_id)
    if not cluster:
        logger.error("Cluster not found: %s", cluster_id)
        return False

    data = []
    tasks = db_controller.get_job_tasks(cluster_id, reverse=False)
    if tasks and is_json is True:
        for t in tasks:
            data.append(t.get_clean_dict())
        return json.dumps(data, indent=2)

    for task in tasks:
        if task.max_retry > 0:
            retry = f"{task.retry}/{task.max_retry}"
        else:
            retry = f"{task.retry}"

        data.append({
            "Task ID": task.uuid,
            "Node ID / Device ID": f"{task.node_id}\n{task.device_id}",
            "Function": task.function_name,
            "Retry": retry,
            "Status": task.status,
            "Result": task.function_result,
            "Updated At": datetime.datetime.strptime(task.updated_at, "%Y-%m-%d %H:%M:%S.%f").strftime(
                "%H:%M:%S, %d/%m/%Y"),
        })
    return utils.print_table(data)


def cancel_task(task_id):
    task = db_controller.get_task_by_id(task_id)
    if not task:
        logger.error("Task not found: %s", task_id)
        return False

    if task.device_id:
        device_controller.device_set_retries_exhausted(task.device_id, True)

    task.canceled = True
    task.write_to_db(db_controller.kv_store)
    tasks_events.task_canceled(task)
    return True


def get_active_node_restart_task(cluster_id, node_id):
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        if task.function_name == JobSchedule.FN_NODE_RESTART and task.node_id == node_id:
            if task.status == JobSchedule.STATUS_RUNNING and task.canceled is False:
                return task.uuid
    return False


def get_active_dev_restart_task(cluster_id, device_id):
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        if task.function_name == JobSchedule.FN_DEV_RESTART and task.device_id == device_id:
            if task.status == JobSchedule.STATUS_RUNNING and task.canceled is False:
                return task.uuid
    return False


def get_active_node_mig_task(cluster_id, node_id):
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        if task.function_name in [JobSchedule.FN_FAILED_DEV_MIG, JobSchedule.FN_DEV_MIG,
                                  JobSchedule.FN_NEW_DEV_MIG] and task.node_id == node_id:
            if task.status == JobSchedule.STATUS_RUNNING and task.canceled is False:
                return task.uuid
    return False


def add_device_failed_mig_task(device_id):
    device = db_controller.get_storage_device_by_id(device_id)
    for node in db_controller.get_storage_nodes_by_cluster_id(device.cluster_id):
        if node.status == StorageNode.STATUS_REMOVED:
            continue
        for bdev in node.lvstore_stack:
            if bdev['type'] == "bdev_distr":
                _add_task(JobSchedule.FN_FAILED_DEV_MIG, device.cluster_id, node.get_id(), device.get_id(),
                          max_retry=-1, function_params={'distr_name': bdev['name']})
    return True


def add_new_device_mig_task(device_id):
    device = db_controller.get_storage_device_by_id(device_id)
    for node in db_controller.get_storage_nodes_by_cluster_id(device.cluster_id):
        if node.status == StorageNode.STATUS_REMOVED:
            continue
        for bdev in node.lvstore_stack:
            if bdev['type'] == "bdev_distr":
                _add_task(JobSchedule.FN_NEW_DEV_MIG, device.cluster_id, node.get_id(), device.get_id(),
                          max_retry=-1, function_params={'distr_name': bdev['name']})
    return True


def add_node_add_task(cluster_id, function_params):
    return _add_task(JobSchedule.FN_NODE_ADD, cluster_id, "", "", function_params=function_params)


def get_active_node_task(cluster_id, node_id):
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        if task.node_id == node_id:
            if task.status != JobSchedule.STATUS_DONE and task.canceled is False:
                return task.uuid
    return False


def get_new_device_mig_task(cluster_id, node_id, distr_name, dev_id=None):
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        if task.function_name == JobSchedule.FN_NEW_DEV_MIG and task.node_id == node_id:
            if dev_id:
                if task.device_id != dev_id:
                    continue
            if task.status != JobSchedule.STATUS_DONE and task.canceled is False \
                    and "distr_name" in task.function_params and task.function_params["distr_name"] == distr_name:
                return task.uuid
    return False


def get_device_mig_task(cluster_id, node_id, distr_name):
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        if task.function_name == JobSchedule.FN_DEV_MIG and task.node_id == node_id:
            if task.status != JobSchedule.STATUS_DONE and task.canceled is False \
                    and "distr_name" in task.function_params and task.function_params["distr_name"] == distr_name:
                return task.uuid
    return False


def get_new_device_mig_task_for_device(cluster_id):
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        if task.function_name == JobSchedule.FN_NEW_DEV_MIG:
            if task.status != JobSchedule.STATUS_DONE and task.canceled is False:
                return task.uuid
    return False


def get_failed_device_mig_task(cluster_id, device_id):
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        if task.function_name == JobSchedule.FN_FAILED_DEV_MIG and task.device_id == device_id:
            if task.status != JobSchedule.STATUS_DONE and task.canceled is False:
                return task.uuid
    return False
