# coding=utf-8
import logging

import time
import sys



from simplyblock_core import kv_store, constants, cluster_ops
from simplyblock_core.controllers import cluster_events
from simplyblock_core.models.cluster import Cluster

# Import the GELF logger
from graypy import GELFUDPHandler

# configure logging
logger_handler = logging.StreamHandler(stream=sys.stdout)
logger_handler.setFormatter(logging.Formatter('%(asctime)s: %(levelname)s: %(message)s'))
gelf_handler = GELFUDPHandler('0.0.0.0', constants.GELF_PORT)
logger = logging.getLogger()
logger.addHandler(gelf_handler)
logger.addHandler(logger_handler)
logger.setLevel(logging.DEBUG)

### script to test connection once connection is ascertain
# get DB controller
db_controller = kv_store.DBController()

logger.info("Starting capacity monitoring service...")
while True:
    clusters = db_controller.get_clusters()
    for cl in clusters:
        logger.info(f"Checking cluster: {cl.get_id()}")
        records = db_controller.get_cluster_capacity(cl, 1)
        if not records:
            logger.error("Cluster capacity record not found!")
            continue

        size_util = records[0].size_util
        size_prov = records[0].size_prov_util
        logger.debug(f"cluster abs util: {size_util}, prov util: {size_prov}")
        if cl.cap_crit:
            if cl.cap_crit < size_util:
                logger.warning(f"Cluster absolute cap critical, util: {size_util}% of cluster util: {cl.cap_crit}, "
                               f"putting the cluster in read_only mode")
                cluster_events.cluster_cap_crit(cl, size_util)
                cluster_ops.cluster_set_read_only(cl.get_id())
            else:
                if cl.status == Cluster.STATUS_READONLY:
                    cluster_ops.cluster_set_active(cl.get_id())

        if cl.cap_warn:
            if cl.cap_warn < size_util < cl.cap_crit:
                logger.warning(f"Cluster absolute cap warning, util: {size_util}% of cluster util: {cl.cap_warn}")
                cluster_events.cluster_cap_warn(cl, size_util)

        if cl.prov_cap_crit:
            if cl.prov_cap_crit < size_prov:
                logger.warning(f"Cluster provisioned cap critical, util: {size_prov}% of cluster util: {cl.prov_cap_crit}")
                cluster_events.cluster_prov_cap_crit(cl, size_prov)

        if cl.prov_cap_warn:
            if cl.prov_cap_warn < size_prov < cl.prov_cap_crit:
                logger.warning(f"Cluster provisioned cap warning, util: {size_prov}% of cluster util: {cl.prov_cap_warn}")
                cluster_events.cluster_prov_cap_warn(cl, size_prov)

    time.sleep(constants.CAP_MONITOR_INTERVAL_SEC)
