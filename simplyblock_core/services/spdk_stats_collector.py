from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
import time
from simplyblock_core.services.spdk import client as spdk_client
from simplyblock_core import constants, db_controller,utils
from simplyblock_core.rpc_client import RPCClient

logger = utils.get_logger(__name__)


PUSHGATEWAY_URL = "http://pushgateway:9091"

db_controller = db_controller.DBController()

def parse_cpu_cores(cpumask):
    core_mask_int = int(cpumask, 16)
    return [i for i in range(64) if (core_mask_int & (1 << i))]


def push_metrics(ret,cluster_id,snode):
    """Formats and pushes SPDK metrics to Prometheus Pushgateway."""
    registry = CollectorRegistry()
    tick_rate_gauge = Gauge('tick_rate', 'SPDK Tick Rate', ['cluster', 'snode', 'node_ip'], registry=registry)
    cpu_busy_gauge = Gauge('cpu_busy_percentage', 'Per-thread CPU Busy Percentage', ['cluster', 'snode', 'node_ip', 'thread_name'], registry=registry)
    pollers_count_gauge = Gauge('pollers_count', 'Number of pollers', ['cluster', 'snode', 'node_ip', 'poller_type', 'thread_name'], registry=registry)
    cpu_utilization_gauge = Gauge('cpu_core_utilization', 'Per-core CPU Utilization', ['cluster', 'snode', 'node_ip', 'core_id'], registry=registry)


    snode_id = snode.id
    snode_ip = snode.mgmt_ip
    tick_rate = ret.get("tick_rate")
    if tick_rate is not None:
        tick_rate_gauge.labels(cluster=cluster_id, snode=snode_id, node_ip=snode_ip).set(tick_rate)
    
    core_utilization = {}

    for thread in ret.get("threads", []):
        thread_name = thread.get("name")
        busy = thread.get("busy", 0)
        idle = thread.get("idle", 0)
        cpumask = thread.get("cpumask", "0")

        total_cycles = busy + idle
        cpu_usage_percent = (busy / total_cycles) * 100 if total_cycles > 0 else 0
        cpu_busy_gauge.labels(cluster=cluster_id, snode=snode_id, node_ip=snode_ip, thread_name=thread_name).set(cpu_usage_percent)

        core_list = parse_cpu_cores(cpumask)
        for core_id in core_list:
            if core_id not in core_utilization:
                core_utilization[core_id] = []
            core_utilization[core_id].append(cpu_usage_percent)

        pollers_count_gauge.labels(cluster=cluster_id, snode=snode_id, node_ip=snode_ip, poller_type="active", thread_name=thread_name).set(thread.get("active_pollers_count", 0))
        pollers_count_gauge.labels(cluster=cluster_id, snode=snode_id, node_ip=snode_ip, poller_type="timed", thread_name=thread_name).set(thread.get("timed_pollers_count", 0))
        pollers_count_gauge.labels(cluster=cluster_id, snode=snode_id, node_ip=snode_ip, poller_type="paused", thread_name=thread_name).set(thread.get("paused_pollers_count", 0))


    for core_id, usage_list in core_utilization.items():
        avg_utilization = sum(usage_list) / len(usage_list)
        cpu_utilization_gauge.labels(cluster=cluster_id, snode=snode_id, node_ip=snode_ip, core_id=str(core_id)).set(avg_utilization)


    
    push_to_gateway(PUSHGATEWAY_URL, job='metricsgateway', registry=registry)
    logger.info("Metrics pushed successfully")

logger.info("Starting spdk stats collector...")
while True:
    clusters = db_controller.get_clusters()
    for cluster in clusters:
        cluster_id = cluster.get_id()
        nodes = db_controller.get_storage_nodes_by_cluster_id(cluster_id)
        for snode in nodes:
            if snode.nvme_devices > 0:
                rpc_client = RPCClient(
                snode.mgmt_ip, snode.rpc_port,
                snode.rpc_username, snode.rpc_password, timeout=3*60, retry=10)
                ret = rpc_client.thread_get_stats()
                if ret and "threads" in ret:
                    push_metrics(ret, cluster_id, snode)
            else:
                logger.info(f"Skipping snode {snode.mgmt_ip} as it has no NVMe devices.")

    time.sleep(constants.SPDK_STAT_COLLECTOR_INTERVAL_SEC)
