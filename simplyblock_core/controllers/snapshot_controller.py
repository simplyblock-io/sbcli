# coding=utf-8
import datetime
import json
import logging as lg
import time
import uuid

from simplyblock_core.controllers import lvol_controller, snapshot_events

from simplyblock_core import utils, distr_controller, constants
from simplyblock_core.kv_store import DBController
from simplyblock_core.models.pool import Pool
from simplyblock_core.models.snapshot import SnapShot
from simplyblock_core.models.lvol_model import LVol
from simplyblock_core.rpc_client import RPCClient
from simplyblock_core.snode_client import SNodeClient


logger = lg.getLogger()

db_controller = DBController()


def add(lvol_id, snapshot_name):
    lvol = db_controller.get_lvol_by_id(lvol_id)
    if not lvol:
        logger.error(f"LVol not found: {lvol_id}")
        return False

    pool = db_controller.get_pool_by_id(lvol.pool_uuid)
    if pool.status == Pool.STATUS_INACTIVE:
        logger.error(f"Pool is disabled")
        return False

    if lvol.cloned_from_snap:
        snap = db_controller.get_snapshot_by_id(lvol.cloned_from_snap)
        ref_count = snap.ref_count
        if snap.snap_ref_id:
            ref_snap = db_controller.get_snapshot_by_id(snap.snap_ref_id)
            ref_count = ref_snap.ref_count

        if ref_count >= constants.MAX_SNAP_COUNT:
            msg = f"Can not create more than {constants.MAX_SNAP_COUNT} snaps from this clone"
            logger.error(msg)
            return False, msg

    logger.info(f"Creating snapshot: {snapshot_name} from LVol: {lvol.id}")
    snode = db_controller.get_storage_node_by_id(lvol.node_id)

##############################################################################
    # Validate adding snap on storage node
    snode_api = SNodeClient(snode.api_endpoint)
    result, _ = snode_api.info()
    memory_free = result["memory_details"]["free"]
    huge_free = result["memory_details"]["huge_free"]
    total_node_capacity = db_controller.get_snode_size(snode.get_id())

    error = utils.validate_add_lvol_or_snap_on_node(
        memory_free,
        huge_free,
        snode.max_snap,
        lvol.size,
        total_node_capacity,
        len(db_controller.get_snapshots_by_node_id(lvol.node_id)))

    if error:
        logger.error(f"Failed to add snap on node {lvol.node_id}")
        logger.error(error)
        return False

##############################################################################

    rpc_client = RPCClient(snode.mgmt_ip, snode.rpc_port, snode.rpc_username, snode.rpc_password)
    spdk_mem_info_before = rpc_client.ultra21_util_get_malloc_stats()

    logger.info("Creating Snapshot bdev")
    ret = rpc_client.lvol_create_snapshot(lvol.top_bdev, snapshot_name)
    if not ret:
        return False, "Failed to create snapshot bdev"

##############################################################################
    snap = SnapShot()
    snap.uuid = str(uuid.uuid4())
    snap.snap_name = snapshot_name
    snap.snap_bdev = f"{lvol.lvs_name}/{snapshot_name}"
    snap.created_at = int(time.time())
    snap.lvol = lvol

    spdk_mem_info_after = rpc_client.ultra21_util_get_malloc_stats()
    logger.debug("ultra21_util_get_malloc_stats:")
    logger.debug(spdk_mem_info_after)
    diff = {}
    for key in spdk_mem_info_after.keys():
        diff[key] = spdk_mem_info_after[key] - spdk_mem_info_before[key]
    logger.info("spdk mem diff:")
    logger.info(json.dumps(diff, indent=2))
    snap.mem_diff = diff
    snap.write_to_db(db_controller.kv_store)

    if lvol.cloned_from_snap:
        original_snap = db_controller.get_snapshot_by_id(lvol.cloned_from_snap)
        if original_snap.snap_ref_id:
            original_snap = db_controller.get_snapshot_by_id(original_snap.snap_ref_id)

        original_snap.ref_count += 1
        original_snap.write_to_db(db_controller.kv_store)
        snap.snap_ref_id = original_snap.get_id()
        snap.write_to_db(db_controller.kv_store)

    logger.info("Done")
    snapshot_events.snapshot_create(snap)
    return snap.uuid


def list(all=False):
    snaps = db_controller.get_snapshots()
    data = []
    for snap in snaps:
        logger.debug(snap)
        if snap.deleted is True and all is False:
            continue
        data.append({
            "UUID": snap.uuid,
            "Name": snap.snap_name,
            "Size": utils.humanbytes(snap.lvol.size),
            "BDev": snap.snap_bdev,
            "LVol ID": snap.lvol.get_id(),
            "Created At": time.strftime("%H:%M:%S, %d/%m/%Y", time.gmtime(snap.created_at)),
            "Health": snap.health_check,
        })
    return utils.print_table(data)


def delete(snapshot_uuid, force=False):
    snap = db_controller.get_snapshot_by_id(snapshot_uuid)
    if not snap:
        logger.error(f"Snapshot not found {snapshot_uuid}")
        return False

    snode = db_controller.get_storage_node_by_id(snap.lvol.node_id)
    if not snode:
        logger.error(f"Storage node not found {snap.lvol.node_id}")
        return False

    for lvol in db_controller.get_lvols(snode.cluster_id):
        if lvol.cloned_from_snap and lvol.cloned_from_snap == snapshot_uuid:
            logger.warning(f"Soft delete snapshot with clones, lvol ID: {lvol.get_id()}")
            snap.deleted = True
            snap.write_to_db(db_controller.kv_store)
            return True

    logger.info(f"Removing snapshot: {snapshot_uuid}")

    snode = db_controller.get_storage_node_by_id(snap.lvol.node_id)

    # creating RPCClient instance
    rpc_client = RPCClient(
        snode.mgmt_ip,
        snode.rpc_port,
        snode.rpc_username,
        snode.rpc_password)

    ret = rpc_client.delete_lvol(snap.snap_bdev)
    if not ret:
        logger.error(f"Failed to delete BDev {snap.snap_bdev}")
        if not force:
            return False
    snap.remove(db_controller.kv_store)

    base_lvol = db_controller.get_lvol_by_id(snap.lvol.get_id())
    if base_lvol.deleted is True:
        lvol_controller.delete_lvol(base_lvol.get_id())

    logger.info("Done")
    snapshot_events.snapshot_delete(snap)
    return True


def clone(snapshot_id, clone_name, new_size=0):
    snap = db_controller.get_snapshot_by_id(snapshot_id)
    if not snap:
        msg=f"Snapshot not found {snapshot_id}"
        logger.error(msg)
        return False, msg

    pool = db_controller.get_pool_by_id(snap.lvol.pool_uuid)
    if not pool:
        msg=f"Pool not found: {snap.lvol.pool_uuid}"
        logger.error(msg)
        return False, msg

    if pool.status == Pool.STATUS_INACTIVE:
        msg="Pool is disabled"
        logger.error(msg)
        return False, msg

    snode = db_controller.get_storage_node_by_id(snap.lvol.node_id)
    if not snode:
        msg=f"Storage node not found: {snap.lvol.node_id}"
        logger.error(msg)
        return False, msg

    if snode.status != snode.STATUS_ONLINE:
        msg = "Storage node in not Online"
        logger.error(msg)
        return False, msg

    ref_count = snap.ref_count
    if snap.snap_ref_id:
        ref_snap = db_controller.get_snapshot_by_id(snap.snap_ref_id)
        ref_count = ref_snap.ref_count

    if ref_count >= constants.MAX_SNAP_COUNT:
        msg = f"Can not create more than {constants.MAX_SNAP_COUNT} clones from this snapshot"
        logger.error(msg)
        return False, msg

    for lvol in db_controller.get_lvols():
        if lvol.pool_uuid == pool.get_id():
            if lvol.lvol_name == clone_name:
                msg=f"LVol name must be unique: {clone_name}"
                logger.error(msg)
                return False, msg

    # Validate cloning snap on storage node
    snode_api = SNodeClient(snode.api_endpoint)
    result, _ = snode_api.info()
    memory_free = result["memory_details"]["free"]
    huge_free = result["memory_details"]["huge_free"]
    total_node_capacity = db_controller.get_snode_size(snode.get_id())
    error = utils.validate_add_lvol_or_snap_on_node(
        memory_free, huge_free, snode.max_lvol, snap.lvol.size,  total_node_capacity, len(snode.lvols))
    if error:
        logger.error(error)
        return False, f"Failed to add lvol on node {snode.get_id()}"

    lvol = LVol()
    lvol.uuid = str(uuid.uuid4())
    lvol.lvol_name = clone_name
    lvol.size = snap.lvol.size
    lvol.max_size = snap.lvol.max_size
    lvol.base_bdev = snap.lvol.base_bdev
    lvol.lvol_bdev = f"CLN_{clone_name}"
    lvol.lvs_name = snap.lvol.lvs_name
    lvol.top_bdev = f"{lvol.lvs_name}/{lvol.lvol_bdev}"
    lvol.hostname = snode.hostname
    lvol.node_id = snode.get_id()
    lvol.mode = 'read-write'
    lvol.cloned_from_snap = snapshot_id
    lvol.nqn = snode.subsystem + ":lvol:" + lvol.uuid
    lvol.pool_uuid = pool.id
    lvol.ha_type = snap.lvol.ha_type

    lvol.ndcs = snap.lvol.ndcs
    lvol.npcs = snap.lvol.npcs
    lvol.distr_bs = snap.lvol.distr_bs
    lvol.distr_chunk_bs = snap.lvol.distr_chunk_bs
    lvol.distr_page_size = snap.lvol.distr_page_size
    lvol.guid = snap.lvol.guid
    lvol.vuid = snap.lvol.vuid

    lvol.status = LVol.STATUS_ONLINE
    lvol.bdev_stack = snap.lvol.bdev_stack
    lvol.bdev_stack = [
        {
            "type": "bdev_lvol_clone",
            "name": lvol.top_bdev
        }
    ]

    if new_size:
        if snap.lvol.size >= new_size:
            msg = f"New size {new_size} must be higher than the original size {snap.lvol.size}"
            logger.error(msg)
            return False, msg

        if snap.lvol.max_size < new_size:
            msg = f"New size {new_size} must be smaller than the max size {snap.lvol.max_size}"
            logger.error(msg)
            return False, msg
        lvol.size = new_size

    rpc_client = RPCClient(snode.mgmt_ip, snode.rpc_port, snode.rpc_username, snode.rpc_password)
    spdk_mem_info_before = rpc_client.ultra21_util_get_malloc_stats()

    logger.info("Creating clone bdev")
    ret = rpc_client.lvol_clone(snap.snap_bdev, lvol.lvol_bdev)
    if not ret:
        return False, "Failed to create clone lvol bdev"

    subsystem_nqn = lvol.nqn
    logger.info("creating subsystem %s", subsystem_nqn)
    ret = rpc_client.subsystem_create(subsystem_nqn, 'sbcli-cn', lvol.uuid)

    # add listeners
    logger.info("adding listeners")
    for iface in snode.data_nics:
        if iface.ip4_address:
            tr_type = iface.get_transport_type()
            ret = rpc_client.transport_create(tr_type)
            logger.info("adding listener for %s on IP %s" % (subsystem_nqn, iface.ip4_address))
            ret = rpc_client.listeners_create(subsystem_nqn, tr_type, iface.ip4_address, "4420")

    logger.info(f"add lvol {clone_name} to subsystem")
    ret = rpc_client.nvmf_subsystem_add_ns(subsystem_nqn, lvol.top_bdev)
    if not ret:
        return False, "Failed to add bdev to subsystem"

    spdk_mem_info_after = rpc_client.ultra21_util_get_malloc_stats()
    diff = {}
    for key in spdk_mem_info_after.keys():
        diff[key] = spdk_mem_info_after[key] - spdk_mem_info_before[key]

    logger.info("spdk mem diff:")
    logger.info(json.dumps(diff, indent=2))
    lvol.mem_diff = diff
    lvol.write_to_db(db_controller.kv_store)

    pool.lvols.append(lvol.uuid)
    pool.write_to_db(db_controller.kv_store)
    snode.lvols.append(lvol.uuid)
    snode.write_to_db(db_controller.kv_store)

    if snap.snap_ref_id:
        ref_snap = db_controller.get_snapshot_by_id(snap.snap_ref_id)
        ref_snap.ref_count += 1
        ref_snap.write_to_db(db_controller.kv_store)
    else:
        snap.ref_count += 1
        snap.write_to_db(db_controller.kv_store)

    logger.info("Done")
    snapshot_events.snapshot_clone(snap, lvol)
    if new_size:
        lvol_controller.resize_lvol(lvol.get_id(), new_size)
    return True, lvol.uuid
