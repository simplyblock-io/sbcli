# coding=utf-8
import logging

from simplyblock_core.controllers import events_controller as ec
from simplyblock_core.kv_store import DBController

logger = logging.getLogger()
db_controller = DBController()


def _task_event(task, message, caused_by, event):
    ec.log_event_cluster(
        cluster_id=task.cluster_id,
        domain=ec.DOMAIN_CLUSTER,
        event=event,
        db_object=task,
        caused_by=caused_by,
        message=message,
        node_id=task.node_id,
        status=task.status)


def task_create(task, caused_by=ec.CAUSED_BY_CLI):
    _task_event(task, f"task created: {task.uuid}", caused_by, ec.EVENT_OBJ_CREATED)


def task_updated(task, caused_by=ec.CAUSED_BY_CLI):
    _task_event(task, f"Task updated: {task.uuid}", caused_by, ec.EVENT_STATUS_CHANGE)


def task_status_change(task, new_state, old_status, caused_by=ec.CAUSED_BY_CLI):
    _task_event(task, f"task status changed from: {old_status} to: {new_state}", caused_by, ec.EVENT_STATUS_CHANGE)
