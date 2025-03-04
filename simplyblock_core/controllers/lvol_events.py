# coding=utf-8
import logging

from simplyblock_core.controllers import events_controller as ec
from simplyblock_core.db_controller import DBController

logger = logging.getLogger()
db_controller = DBController()


def _lvol_event(lvol, message, caused_by, event):
    snode = db_controller.get_storage_node_by_id(lvol.node_id)
    ec.log_event_cluster(
        cluster_id=snode.cluster_id,
        domain=ec.DOMAIN_CLUSTER,
        event=event,
        db_object=lvol,
        caused_by=caused_by,
        message=message,
        node_id=lvol.node_id)


def lvol_create(lvol, caused_by=ec.CAUSED_BY_CLI):
    _lvol_event(lvol, f"LVol created: {lvol.get_id()}", caused_by, ec.EVENT_OBJ_CREATED)


def lvol_delete(lvol, caused_by=ec.CAUSED_BY_CLI):
    _lvol_event(lvol, f"LVol deleted: {lvol.get_id()}", caused_by, ec.EVENT_OBJ_DELETED)


def lvol_status_change(lvol, new_state, old_status, caused_by=ec.CAUSED_BY_CLI):
    _lvol_event(lvol, f"LVol {lvol.get_id()} status changed from: {old_status} to: {new_state}", caused_by, ec.EVENT_STATUS_CHANGE)


def lvol_migrate(lvol, old_node, new_node, caused_by=ec.CAUSED_BY_CLI):
    _lvol_event(lvol, f"LVol {lvol.get_id()} migrated from: {old_node}, \nto {new_node}", caused_by, ec.EVENT_STATUS_CHANGE)


def lvol_health_check_change(lvol, new_state, old_status, caused_by=ec.CAUSED_BY_CLI):
    _lvol_event(lvol, f"LVol {lvol.get_id()} health check changed from: {old_status} to: {new_state}", caused_by, ec.EVENT_STATUS_CHANGE)


def lvol_io_error_change(lvol, new_state, old_status, caused_by=ec.CAUSED_BY_CLI):
    _lvol_event(lvol, f"LVol {lvol.get_id()} IO Error changed from: {old_status} to: {new_state}", caused_by, ec.EVENT_STATUS_CHANGE)

