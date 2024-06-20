# coding=utf-8
import json
import logging
import os
import time
import uuid

import docker
import requests

from simplyblock_core import utils, scripts, constants, mgmt_node_ops, storage_node_ops
from simplyblock_core.controllers import cluster_events, device_controller
from simplyblock_core.kv_store import DBController
from simplyblock_core.models.cluster import Cluster
from simplyblock_core.models.nvme_device import NVMeDevice
from simplyblock_core.models.storage_node import StorageNode

logger = logging.getLogger()


def _add_grafana_dashboards(username, password, cluster_ip):
    url = f"http://{username}:{password}@{cluster_ip}/grafana/api/dashboards/import"
    headers = {'Content-Type': 'application/json'}
    dirpath, _, filenames = next(os.walk(os.path.join(constants.INSTALL_DIR, "scripts", "dashboards")))
    ret = True
    for filename in filenames:
        with open(os.path.join(dirpath, filename), 'r') as f:
            st = f.read()
            # st = st.replace("$Cluster", cluster_id)
            st = json.loads(st)
        payload = json.dumps(st)
        response = requests.post(url, headers=headers, data=payload)
        logger.debug(response.status_code)
        logger.debug(response.text)
        if response.status_code == 200:
            resp = response.json()
            logger.info(f"Dashboard: {resp['title']}, imported: {resp['imported']}")
        else:
            logger.error(f"Error importing dashboard, status code:{response.status_code} text:{response.text}")
            ret = False
    return ret


def _add_graylog_input(cluster_ip, password):
    url = f"http://{cluster_ip}/graylog/api/system/inputs"
    payload = json.dumps({
        "title": "spdk log input",
        "type": "org.graylog2.inputs.gelf.udp.GELFUDPInput",
        "configuration": {
            "bind_address": "0.0.0.0",
            "port": 12201,
            "recv_buffer_size": 262144,
            "number_worker_threads": 2,
            "override_source": None,
            "charset_name": "UTF-8",
            "decompress_size_limit": 8388608
        },
        "global": True
    })
    headers = {
        'X-Requested-By': '',
        'Content-Type': 'application/json',
    }
    session = requests.session()
    session.auth = ("admin", password)
    response = session.request("POST", url, headers=headers, data=payload)
    logger.debug(response.text)
    return response.status_code == 201


def create_cluster(blk_size, page_size_in_blocks, cli_pass,
                   cap_warn, cap_crit, prov_cap_warn, prov_cap_crit, ifname, log_del_interval, metrics_retention_period):
    logger.info("Installing dependencies...")
    ret = scripts.install_deps()
    logger.info("Installing dependencies > Done")

    if not ifname:
        ifname = "eth0"

    DEV_IP = utils.get_iface_ip(ifname)
    if not DEV_IP:
        logger.error(f"Error getting interface ip: {ifname}")
        return False

    logger.info(f"Node IP: {DEV_IP}")
    ret = scripts.configure_docker(DEV_IP)

    db_connection = f"{utils.generate_string(8)}:{utils.generate_string(32)}@{DEV_IP}:4500"
    ret = scripts.set_db_config(db_connection)

    logger.info("Configuring docker swarm...")
    c = docker.DockerClient(base_url=f"tcp://{DEV_IP}:2375", version="auto")
    try:
        if c.swarm.attrs and "ID" in c.swarm.attrs:
            logger.info("Docker swarm found, leaving swarm now")
            c.swarm.leave(force=True)
            time.sleep(3)

        c.swarm.init()
        logger.info("Configuring docker swarm > Done")
    except Exception as e:
        print(e)

    db_controller = DBController()
    if not cli_pass:
        cli_pass = utils.generate_string(10)

    # validate cluster duplicate
    logger.info("Adding new cluster object")
    c = Cluster()
    c.uuid = str(uuid.uuid4())
    c.blk_size = blk_size
    c.page_size_in_blocks = page_size_in_blocks
    c.nqn = f"{constants.CLUSTER_NQN}:{c.uuid}"
    c.cli_pass = cli_pass
    c.secret = utils.generate_string(20)
    c.db_connection = db_connection
    if cap_warn and cap_warn > 0:
        c.cap_warn = cap_warn
    if cap_crit and cap_crit > 0:
        c.cap_crit = cap_crit
    if prov_cap_warn and prov_cap_warn > 0:
        c.prov_cap_warn = prov_cap_warn
    if prov_cap_crit and prov_cap_crit > 0:
        c.prov_cap_crit = prov_cap_crit

    logger.info("Deploying swarm stack ...")
    ret = scripts.deploy_stack(cli_pass, DEV_IP, constants.SIMPLY_BLOCK_DOCKER_IMAGE, c.secret, c.uuid, log_del_interval, metrics_retention_period)
    logger.info("Deploying swarm stack > Done")

    logger.info("Configuring DB...")
    out = scripts.set_db_config_single()
    logger.info("Configuring DB > Done")

    _add_graylog_input(DEV_IP, c.secret)

    c.status = Cluster.STATUS_ACTIVE

    c.updated_at = int(time.time())
    c.write_to_db(db_controller.kv_store)

    cluster_events.cluster_create(c)

    mgmt_node_ops.add_mgmt_node(DEV_IP, c.uuid)

    logger.info("Applying dashboard...")
    ret = _add_grafana_dashboards("admin", c.secret, DEV_IP)
    logger.info(f"Applying dashboard > {ret}")

    logger.info("New Cluster has been created")
    logger.info(c.uuid)
    return c.uuid


# Deprecated
def deploy_spdk(node_docker, spdk_cpu_mask, spdk_mem):
    nodes = node_docker.containers.list(all=True)
    for node in nodes:
        if node.attrs["Name"] == "/spdk":
            logger.info("spdk container found, skip deploy...")
            return
    container = node_docker.containers.run(
        constants.SIMPLY_BLOCK_SPDK_ULTRA_IMAGE,
        f"/root/scripts/run_distr.sh {spdk_cpu_mask} {spdk_mem}",
        detach=True,
        privileged=True,
        name="spdk",
        network_mode="host",
        volumes=[
            '/var/tmp:/var/tmp',
            '/dev:/dev',
            '/lib/modules/:/lib/modules/',
            '/sys:/sys'],
        restart_policy={"Name": "on-failure", "MaximumRetryCount": 99}
    )
    container2 = node_docker.containers.run(
        constants.SIMPLY_BLOCK_SPDK_ULTRA_IMAGE,
        "python /root/scripts/spdk_http_proxy.py",
        name="spdk_proxy",
        detach=True,
        network_mode="host",
        volumes=[
            '/var/tmp:/var/tmp',
            '/etc/foundationdb:/etc/foundationdb'],
        restart_policy={"Name": "on-failure", "MaximumRetryCount": 99}
    )
    retries = 10
    while retries > 0:
        info = node_docker.containers.get(container.attrs['Id'])
        status = info.attrs['State']["Status"]
        is_running = info.attrs['State']["Running"]
        if not is_running:
            logger.info("Container is not running, waiting...")
            time.sleep(3)
            retries -= 1
        else:
            logger.info(f"Container status: {status}, Is Running: {is_running}")
            break


def add_cluster(blk_size, page_size_in_blocks, cap_warn, cap_crit, prov_cap_warn, prov_cap_crit):
    db_controller = DBController()
    clusters = db_controller.get_clusters()
    if not clusters:
        logger.error("No previous clusters found!")
        return False

    default_cluster = clusters[0]
    logger.info("Adding new cluster")
    cluster = Cluster()
    cluster.uuid = str(uuid.uuid4())
    cluster.blk_size = blk_size
    cluster.page_size_in_blocks = page_size_in_blocks
    cluster.ha_type = default_cluster.ha_type
    cluster.nqn = f"{constants.CLUSTER_NQN}:{cluster.uuid}"
    cluster.cli_pass = default_cluster.cli_pass
    cluster.secret = default_cluster.secret
    cluster.db_connection = default_cluster.db_connection
    if cap_warn and cap_warn > 0:
        cluster.cap_warn = cap_warn
    if cap_crit and cap_crit > 0:
        cluster.cap_crit = cap_crit
    if prov_cap_warn and prov_cap_warn > 0:
        cluster.prov_cap_warn = prov_cap_warn
    if prov_cap_crit and prov_cap_crit > 0:
        cluster.prov_cap_crit = prov_cap_crit

    cluster.status = Cluster.STATUS_ACTIVE
    cluster.updated_at = int(time.time())
    cluster.write_to_db(db_controller.kv_store)
    cluster_events.cluster_create(cluster)

    return cluster.get_id()


def show_cluster(cl_id, is_json=False):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cl_id)
    if not cluster:
        logger.error(f"Cluster not found {cl_id}")
        return False

    st = db_controller.get_storage_nodes_by_cluster_id(cl_id)
    data = []
    for node in st:
        for dev in node.nvme_devices:
            data.append({
                "UUID": dev.get_id(),
                "Storage ID": dev.cluster_device_order,
                "Size": utils.humanbytes(dev.size),
                "Hostname": node.hostname,
                "Status": dev.status,
                "IO Error": dev.io_error,
                "Health": dev.health_check
            })
    data = sorted(data, key=lambda x: x["Storage ID"])
    if is_json:
        return json.dumps(data, indent=2)
    else:
        return utils.print_table(data)


def set_cluster_status(cl_id, status):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cl_id)
    if not cluster:
        logger.error(f"Cluster not found {cl_id}")
        return False

    if cluster.status == status:
        return True

    old_status = cluster.status
    cluster.status = status
    cluster.write_to_db(db_controller.kv_store)
    cluster_events.cluster_status_change(cluster, cluster.status, old_status)
    return True


def suspend_cluster(cl_id):
    return set_cluster_status(cl_id, Cluster.STATUS_SUSPENDED)


def unsuspend_cluster(cl_id):
    return set_cluster_status(cl_id, Cluster.STATUS_ACTIVE)


def degrade_cluster(cl_id):
    return set_cluster_status(cl_id, Cluster.STATUS_DEGRADED)


def cluster_set_read_only(cl_id):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cl_id)
    if not cluster:
        logger.error(f"Cluster not found {cl_id}")
        return False

    if cluster.status == Cluster.STATUS_READONLY:
        return True

    ret = set_cluster_status(cl_id, Cluster.STATUS_READONLY)
    if ret:
        st = db_controller.get_storage_nodes_by_cluster_id(cl_id)
        for node in st:
            for dev in node.nvme_devices:
                if dev.status == NVMeDevice.STATUS_ONLINE:
                    device_controller.device_set_read_only(dev.get_id())
    return True


def cluster_set_active(cl_id):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cl_id)
    if not cluster:
        logger.error(f"Cluster not found {cl_id}")
        return False

    if cluster.status == Cluster.STATUS_ACTIVE:
        return True

    ret = set_cluster_status(cl_id, Cluster.STATUS_ACTIVE)
    if ret:
        st = db_controller.get_storage_nodes_by_cluster_id(cl_id)
        for node in st:
            for dev in node.nvme_devices:
                if dev.status == NVMeDevice.STATUS_READONLY:
                    device_controller.device_set_online(dev.get_id())
    return True


def list():
    db_controller = DBController()
    cls = db_controller.get_clusters()
    mt = db_controller.get_mgmt_nodes()

    data = []
    for cl in cls:
        st = db_controller.get_storage_nodes_by_cluster_id(cl.get_id())
        data.append({
            "UUID": cl.id,
            "NQN": cl.nqn,
            "ha_type": cl.ha_type,
            "tls": cl.tls,
            "mgmt nodes": len(mt),
            "storage nodes": len(st),
            "Status": cl.status,
        })
    return utils.print_table(data)


def get_capacity(cluster_id, history, records_count=20, is_json=False):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cluster_id)
    if not cluster:
        logger.error(f"Cluster not found {cluster_id}")
        return False

    if history:
        records_number = utils.parse_history_param(history)
        if not records_number:
            logger.error(f"Error parsing history string: {history}")
            return False
    else:
        records_number = 20

    records = db_controller.get_cluster_capacity(cluster, records_number)

    new_records = utils.process_records(records, records_count)

    if is_json:
        return json.dumps(new_records, indent=2)

    out = []
    for record in new_records:
        out.append({
            "Date": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(record['date'])),
            "Absolut": utils.humanbytes(record['size_total']),
            "Provisioned": utils.humanbytes(record['size_prov']),
            "Used": utils.humanbytes(record['size_used']),
            "Free": utils.humanbytes(record['size_free']),
            "Util %": f"{record['size_util']}%",
            "Prov Util %": f"{record['size_prov_util']}%",
        })
    return out


def get_iostats_history(cluster_id, history_string, records_count=20, parse_sizes=True):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cluster_id)
    if not cluster:
        logger.error(f"Cluster not found {cluster_id}")
        return False

    nodes = db_controller.get_storage_nodes_by_cluster_id(cluster_id)
    if not nodes:
        logger.error("no nodes found")
        return False

    if history_string:
        records_number = utils.parse_history_param(history_string)
        if not records_number:
            logger.error(f"Error parsing history string: {history_string}")
            return False
    else:
        records_number = 20

    records = db_controller.get_cluster_stats(cluster, records_number)

    # combine records
    new_records = utils.process_records(records, records_count)

    if not parse_sizes:
        return new_records

    out = []
    for record in new_records:
        out.append({
            "Date": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(record['date'])),
            "Read speed": utils.humanbytes(record['read_bytes_ps']),
            "Read IOPS": record["read_io_ps"],
            "Read lat": record["read_latency_ps"],
            "Write speed": utils.humanbytes(record["write_bytes_ps"]),
            "Write IOPS": record["write_io_ps"],
            "Write lat": record["write_latency_ps"],
        })
    return out


def get_ssh_pass(cluster_id):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cluster_id)
    if not cluster:
        logger.error(f"Cluster not found {cluster_id}")
        return False
    return cluster.cli_pass


def get_secret(cluster_id):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cluster_id)
    if not cluster:
        logger.error(f"Cluster not found {cluster_id}")
        return False
    return cluster.secret


def set_secret(cluster_id, secret):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cluster_id)
    if not cluster:
        logger.error(f"Cluster not found {cluster_id}")
        return False

    secret = secret.strip()
    if len(secret) < 20:
        return "Secret must be at least 20 char"

    cluster.secret = secret
    cluster.write_to_db(db_controller.kv_store)
    return "Done"


def get_logs(cluster_id, is_json=False):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cluster_id)
    if not cluster:
        logger.error(f"Cluster not found {cluster_id}")
        return False

    events = db_controller.get_events(cluster_id)
    out = []
    for record in events:
        logger.debug(record)
        Storage_ID = None
        if 'storage_ID' in record.object_dict:
            Storage_ID = record.object_dict['storage_ID']

        vuid = None
        if 'vuid' in record.object_dict:
            vuid = record.object_dict['vuid']

        out.append({
            "Date": record.get_date_string(),
            "NodeId": record.node_id,
            "Event": record.event,
            "Level": record.event_level,
            "Message": record.message,
            "Storage_ID": str(Storage_ID),
            "VUID": str(vuid),
            "Status": record.status,
        })
    if is_json:
        return json.dumps(out, indent=2)
    else:
        return utils.print_table(out)


def get_cluster(cl_id):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cl_id)
    if not cluster:
        logger.error(f"Cluster not found {cl_id}")
        return False

    return json.dumps(cluster.get_clean_dict(), indent=2)


def update_cluster(cl_id):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cl_id)
    if not cluster:
        logger.error(f"Cluster not found {cl_id}")
        return False

    # try:
    #     out, _, ret_code = shell_utils.run_command("pip install sbcli-dev --upgrade")
    #     if ret_code == 0:
    #         logger.info("sbcli-dev is upgraded")
    # except Exception as e:
    #     logger.error(e)

    try:
        logger.info("Updating mgmt cluster")
        cluster_docker = utils.get_docker_client(cl_id)
        logger.info(f"Pulling image {constants.SIMPLY_BLOCK_DOCKER_IMAGE}")
        cluster_docker.images.pull(constants.SIMPLY_BLOCK_DOCKER_IMAGE)
        for service in cluster_docker.services.list():
            if service.attrs['Spec']['Labels']['com.docker.stack.image'] == constants.SIMPLY_BLOCK_DOCKER_IMAGE:
                logger.info(f"Updating service {service.name}")
                service.update(image=constants.SIMPLY_BLOCK_DOCKER_IMAGE, force_update=True)
        logger.info("Done")
    except Exception as e:
        print(e)

    for node in db_controller.get_storage_nodes_by_cluster_id(cl_id):
        node_docker = docker.DockerClient(base_url=f"tcp://{node.mgmt_ip}:2375", version="auto")
        logger.info(f"Pulling image {constants.SIMPLY_BLOCK_SPDK_ULTRA_IMAGE}")
        node_docker.images.pull(constants.SIMPLY_BLOCK_SPDK_ULTRA_IMAGE)
        if node.status == StorageNode.STATUS_ONLINE:
            storage_node_ops.shutdown_storage_node(node.get_id(), force=True)
            time.sleep(3)
        storage_node_ops.restart_storage_node(node.get_id())

    logger.info("Done")
    return True


def list_tasks(cluster_id):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cluster_id)
    if not cluster:
        logger.error("Cluster not found: %s", cluster_id)
        return False

    data = []
    tasks = db_controller.get_job_tasks(cluster_id)
    for task in tasks:
        data.append({
            "Task ID": task.uuid,
            "Target ID": task.device_id or task.node_id,
            "Function": task.function_name,
            "Retry": f"{task.retry}/{constants.TASK_EXEC_RETRY_COUNT}",
            "Status": task.status,
            "Result": task.function_result,
            "Date": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(task.date)),
        })
    return utils.print_table(data)

 
def cluster_grace_startup(cl_id):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cl_id)
    if not cluster:
        logger.error(f"Cluster not found {cl_id}")
        return False
    logger.info(f"Unsuspending cluster: {cl_id}")
    unsuspend_cluster(cl_id)

    st = db_controller.get_storage_nodes_by_cluster_id(cl_id)
    for node in st:
        logger.info(f"Restarting node: {node.get_id()}")
        storage_node_ops.restart_storage_node(node.get_id())
        time.sleep(5)
        get_node = db_controller.get_storage_node_by_id(node.get_id())
        if get_node.status != StorageNode.STATUS_ONLINE:
            logger.error("failed to restart node")
    
    return True


def cluster_grace_shutdown(cl_id):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cl_id)
    if not cluster:
        logger.error(f"Cluster not found {cl_id}")
        return False

    st = db_controller.get_storage_nodes_by_cluster_id(cl_id)
    for node in st:
        logger.info(f"Suspending node: {node.get_id()}")
        storage_node_ops.suspend_storage_node(node.get_id())
        logger.info(f"Shutting down node: {node.get_id()}")
        storage_node_ops.shutdown_storage_node(node.get_id())
       
    logger.info(f"Suspending cluster: {cl_id}")
    suspend_cluster(cl_id)
    return True


def delete_cluster(cl_id):
    db_controller = DBController()
    cluster = db_controller.get_cluster_by_id(cl_id)
    if not cluster:
        logger.error(f"Cluster not found {cl_id}")
        return False

    nodes = db_controller.get_storage_nodes_by_cluster_id(cl_id)
    if nodes:
        logger.error("Can only remove Empty cluster, Storage nodes found")
        return False

    pools = db_controller.get_pools(cl_id)
    if pools:
        logger.error("Can only remove Empty cluster, Pools found")
        return False

    logger.info(f"Deleting Cluster {cl_id}")
    cluster_events.cluster_delete(cluster)
    cluster.remove(db_controller.kv_store)
    logger.info("Done")
