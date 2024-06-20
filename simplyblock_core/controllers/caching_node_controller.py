# coding=utf-8
import datetime
import json
import logging as lg
import math
import time
import uuid

import docker


from simplyblock_core import utils, scripts, constants
from simplyblock_core.cnode_client import CNodeClient
from simplyblock_core.kv_store import DBController
from simplyblock_core.models.caching_node import CachingNode, CachedLVol
from simplyblock_core.models.iface import IFace
from simplyblock_core.models.nvme_device import NVMeDevice
from simplyblock_core.models.pool import Pool
from simplyblock_core.rpc_client import RPCClient

logger = lg.getLogger()

db_controller = DBController()


def addNvmeDevices(cluster, rpc_client, devs, snode):
    sequential_number = 0
    devices = []
    ret = rpc_client.bdev_nvme_controller_list()
    ctr_map = {}
    try:
        ctr_map = {i["ctrlrs"][0]['trid']['traddr']: i["name"] for i in ret}
    except:
        pass

    for index, pcie in enumerate(devs):

        if pcie in ctr_map:
            nvme_bdev = ctr_map[pcie] + "n1"
        else:
            name = "nvme_%s" % pcie.split(":")[2].split(".")[0]
            ret, err = rpc_client.bdev_nvme_controller_attach(name, pcie)
            time.sleep(2)
            nvme_bdev = f"{name}n1"

        ret = rpc_client.get_bdevs(nvme_bdev)
        if ret:
            nvme_dict = ret[0]
            nvme_driver_data = nvme_dict['driver_specific']['nvme'][0]
            model_number = nvme_driver_data['ctrlr_data']['model_number']
            # if model_number not in cluster.model_ids:
            #     logger.warning("Device model ID is not recognized: %s, "
            #                    "skipping device: %s", model_number)
            #     continue
            size = nvme_dict['block_size'] * nvme_dict['num_blocks']
            device_partitions_count = int(size / (cluster.blk_size * cluster.page_size_in_blocks))
            devices.append(
                NVMeDevice({
                    'uuid': str(uuid.uuid4()),
                    'device_name': nvme_dict['name'],
                    'sequential_number': sequential_number,
                    'partitions_count': device_partitions_count,
                    'capacity': size,
                    'size': size,
                    'pcie_address': nvme_driver_data['pci_address'],
                    'model_id': model_number,
                    'serial_number': nvme_driver_data['ctrlr_data']['serial_number'],
                    'nvme_bdev': nvme_bdev,
                    'node_id': snode.get_id(),
                    'cluster_id': snode.cluster_id,
                    'status': 'online'
                }))
            sequential_number += device_partitions_count
    return devices


def add_node(cluster_id, node_ip, iface_name, data_nics_list, spdk_cpu_mask, spdk_mem, spdk_image=None, namespace=None):
    db_controller = DBController()
    kv_store = db_controller.kv_store

    cluster = db_controller.get_cluster_by_id(cluster_id)
    if not cluster:
        logger.error("Cluster not found: %s", cluster_id)
        return False

    logger.info(f"Add Caching node: {node_ip}")
    snode_api = CNodeClient(node_ip)

    node_info, _ = snode_api.info()
    system_id = node_info['system_id']
    hostname = node_info['hostname']
    logger.info(f"Node found: {node_info['hostname']}")
    snode = db_controller.get_caching_node_by_system_id(system_id)
    if snode:
        logger.error("Node already exists, try remove it first.")
        return False

    node_info, _ = snode_api.info()
    results, err = snode_api.join_db(db_connection=cluster.db_connection)

    data_nics = []
    names = data_nics_list or [iface_name]
    for nic in names:
        device = node_info['network_interface'][nic]
        data_nics.append(
            IFace({
                'uuid': str(uuid.uuid4()),
                'if_name': device['name'],
                'ip4_address': device['ip'],
                'status': device['status'],
                'net_type': device['net_type']}))

    rpc_user, rpc_pass = utils.generate_rpc_user_and_pass()
    BASE_NQN = cluster.nqn.split(":")[0]
    subsystem_nqn = f"{BASE_NQN}:{hostname}"
    # creating storage node object
    snode = CachingNode()
    snode.uuid = str(uuid.uuid4())
    snode.status = CachingNode.STATUS_IN_CREATION
    # snode.baseboard_sn = node_info['system_id']
    snode.system_uuid = system_id
    snode.hostname = hostname
    snode.subsystem = subsystem_nqn
    snode.data_nics = data_nics
    snode.mgmt_ip = node_info['network_interface'][iface_name]['ip']
    snode.rpc_port = constants.RPC_HTTP_PROXY_PORT
    snode.rpc_username = rpc_user
    snode.rpc_password = rpc_pass
    snode.cluster_id = cluster_id
    snode.api_endpoint = node_ip
    snode.host_secret = utils.generate_string(20)
    snode.ctrl_secret = utils.generate_string(20)

    snode.cpu = node_info['cpu_count']
    snode.cpu_hz = node_info['cpu_hz']
    snode.memory = node_info['memory']
    # snode.hugepages = node_info['hugepages']

    # check for memory
    if "memory_details" in node_info and node_info['memory_details']:
        memory_details = node_info['memory_details']
        logger.info("Node Memory info")
        logger.info(f"Total: {utils.humanbytes(memory_details['total'])}")
        logger.info(f"Free: {utils.humanbytes(memory_details['free'])}")
        # logger.info(f"Hugepages Total: {utils.humanbytes(memory_details['huge_total'])}")
        # huge_free = memory_details['huge_free']
        # logger.info(f"Hugepages Free: {utils.humanbytes(huge_free)}")
        # if huge_free < 1 * 1024 * 1024:
        #     logger.warning(f"Free hugepages are less than 1G: {utils.humanbytes(huge_free)}")
        if not spdk_mem:
            spdk_mem = memory_details['free']

    logger.info(f"Trying to set hugepages for: {utils.humanbytes(spdk_mem)}")
    logger.info("Deploying SPDK")
    results, err = snode_api.spdk_process_start(
        spdk_cpu_mask, spdk_mem, spdk_image, snode.mgmt_ip,
        snode.rpc_port, snode.rpc_username, snode.rpc_password, namespace)
    if not results:
        logger.error(f"Failed to start spdk: {err}")
        return False

    retries = 20
    while retries > 0:
        resp, _ = snode_api.spdk_process_is_up()
        if resp:
            logger.info(f"Pod is up")
            break
        else:
            logger.info("Pod is not running, waiting...")
            time.sleep(3)
            retries -= 1

    if retries == 0:
        logger.error("Pod is not running, exiting")
        return False

    # creating RPCClient instance
    rpc_client = RPCClient(
        snode.mgmt_ip, snode.rpc_port,
        snode.rpc_username, snode.rpc_password,
        timeout=60*5, retry=5)

    # get new node info after starting spdk
    node_info, _ = snode_api.info()
    mem = node_info['memory_details']['huge_free']
    logger.info(f"Free Hugepages detected: {utils.humanbytes(mem)}")

    # adding devices
    nvme_devs = addNvmeDevices(cluster, rpc_client, node_info['spdk_pcie_list'], snode)
    if not nvme_devs:
        logger.error("No NVMe devices was found!")
        return False

    snode.nvme_devices = nvme_devs
    snode.write_to_db(db_controller.kv_store)
    ssd_dev = nvme_devs[0]

    if mem < 1024*1024:
        logger.error("Hugepages must be larger than 1G")
        return False

    mem = int(mem*constants.CACHING_NODE_MEMORY_FACTOR)
    snode.hugepages = mem
    logger.info(f"Hugepages to be used: {utils.humanbytes(mem)}")

    ssd_size = ssd_dev.size
    supported_ssd_size = mem * 100 / 2
    split_factor = math.ceil(ssd_size/supported_ssd_size)

    logger.info(f"Supported SSD size: {utils.humanbytes(supported_ssd_size)}")
    logger.info(f"SSD size: {utils.humanbytes(ssd_size)}")

    cache_size = 0
    cache_bdev = None
    if supported_ssd_size < ssd_size:
        logger.info(f"SSD size is bigger than the supported size, will use split bdev: {split_factor}")
        ret = rpc_client.bdev_split(ssd_dev.nvme_bdev, split_factor)
        cache_bdev = ret[0]
        cache_size = int(ssd_dev.size/split_factor)
        snode.cache_split_factor = split_factor
    else:
        snode.cache_split_factor = 0
        cache_bdev = ssd_dev.nvme_bdev
        cache_size = ssd_dev.size

    logger.info(f"Cache size: {utils.humanbytes(cache_size)}")

    snode.cache_bdev = cache_bdev
    snode.cache_size = cache_size

    # create tmp ocf
    logger.info(f"Creating first ocf bdev...")

    ret = rpc_client.bdev_malloc_create("malloc_tmp", 512, int((100*1024*1024)/512))
    if not ret:
        logger.error("Failed ot create tmp malloc")
        return False
    ret = rpc_client.bdev_ocf_create("ocf_tmp", 'wt', cache_bdev, "malloc_tmp")
    if not ret:
        logger.error("Failed ot create tmp OCF BDev")
        return False
    logger.info("Setting node status to Active")
    snode.status = CachingNode.STATUS_ONLINE
    snode.write_to_db(kv_store)

    logger.info("Done")
    return True


def recreate(node_id):
    db_controller = DBController()
    snode = db_controller.get_caching_node_by_id(node_id)
    if not snode:
        logger.error(f"Can not find caching node: {node_id}")
        return False

    cluster = db_controller.get_cluster_by_id(snode.cluster_id)
    if not cluster:
        logger.error("Cluster not found: %s", snode.cluster_id)
        return False

    logger.info(f"Recreating caching node: {node_id}, status: {snode.status}")
    snode_api = CNodeClient(f"{snode.mgmt_ip}:5000")

    # creating RPCClient instance
    rpc_client = RPCClient(
        snode.mgmt_ip, snode.rpc_port,
        snode.rpc_username, snode.rpc_password,
        timeout=60*5, retry=5)

    # get new node info after starting spdk
    node_info, _ = snode_api.info()
    # adding devices
    nvme_devs = addNvmeDevices(cluster, rpc_client, node_info['spdk_pcie_list'], snode)
    if not nvme_devs:
        logger.error("No NVMe devices was found!")
        return False

    snode.nvme_devices = nvme_devs
    # snode.write_to_db(db_controller.kv_store)

    ssd_dev = nvme_devs[0]

    if snode.cache_split_factor > 1:
        ret = rpc_client.bdev_split(ssd_dev.nvme_bdev, snode.cache_split_factor)

    logger.info(f"Cache size: {utils.humanbytes(snode.cache_size)}")

    # create tmp ocf
    logger.info(f"Creating first ocf bdev...")

    ret = rpc_client.bdev_malloc_create("malloc_tmp", 512, int((100 * 1024 * 1024) / 512))
    if not ret:
        logger.error("Failed ot create tmp malloc")
        return False
    ret = rpc_client.bdev_ocf_create("ocf_tmp", 'wt', snode.cache_bdev, "malloc_tmp")
    if not ret:
        logger.error("Failed ot create tmp OCF BDev")
        return False

    if snode.lvols:
        for lvol in snode.lvols:
            ret = connect(snode.get_id(), lvol.lvol_id)
            if ret:
                logger.info(f"connecting lvol {lvol.lvol_id} ... ok")
            else:
                logger.error(f"connecting lvol {lvol.lvol_id} .. error")

    logger.info("Setting node status to Active")
    snode.status = CachingNode.STATUS_ONLINE
    snode.write_to_db(db_controller.kv_store)

    logger.info("Done")
    return True


def connect(caching_node_id, lvol_id):
    lvol = db_controller.get_lvol_by_id(lvol_id)
    if not lvol:
        logger.error(f"LVol not found: {lvol_id}")
        return False

    if lvol.status != lvol.STATUS_ONLINE:
        logger.error(f"LVol must be online, lvol status: {lvol.status}")
        return False

    pool = db_controller.get_pool_by_id(lvol.pool_uuid)
    if pool.status == Pool.STATUS_INACTIVE:
        logger.error(f"Pool is disabled")
        return False

    cnode = None
    if caching_node_id == 'this':
        hostn = utils.get_hostname()
        logger.info(f"Trying to get node by hostname: {hostn}")
        cnode = db_controller.get_caching_node_by_hostname(hostn)
    else:
        cnode = db_controller.get_caching_node_by_id(caching_node_id)
        if not cnode:
            logger.info(f"Caching node uuid not found: {caching_node_id}")
            cnode = db_controller.get_caching_node_by_hostname(caching_node_id)
            if not cnode:
                logger.error("Caching node not found")
                return False

    for clvol in cnode.lvols:
        if clvol.lvol_id == lvol_id:
            logger.info(f"Already connected, dev path: {clvol.device_path}")
            return False

    if cnode.cluster_id != pool.cluster_id:
        logger.error("Caching node and LVol are in different clusters")
        return False

    logger.info("Connecting to remote LVOL")
    mini_id = lvol.get_id().split("-")[0]
    rem_name = f"rem_{mini_id}"
    rpc_client = RPCClient(
        cnode.mgmt_ip, cnode.rpc_port, cnode.rpc_username, cnode.rpc_password,
        timeout=60*5, retry=5)
    # create nvmef connection
    if lvol.ha_type == 'single':
        snode = db_controller.get_storage_node_by_id(lvol.node_id)
        for nic in snode.data_nics:
            ret = rpc_client.bdev_nvme_attach_controller_tcp(rem_name, lvol.nqn, nic.ip4_address, "4420")
            logger.debug(ret)
            if not ret:
                logger.error("Failed to connect to LVol")
                return False

    elif lvol.ha_type == "ha":
        for nodes_id in lvol.nodes:
            snode = db_controller.get_storage_node_by_id(nodes_id)
            for nic in snode.data_nics:
                ret = rpc_client.bdev_nvme_attach_controller_tcp(rem_name, lvol.nqn, nic.ip4_address, "4420")
                logger.debug(ret)
                # if not ret:
                #     logger.error("Failed to connect to LVol")
                #     return False

    logger.info("Creating OCF BDev")
    # create ocf device
    cach_bdev = f"ocf_{mini_id}"
    dev = cnode.cache_bdev
    ret = rpc_client.bdev_ocf_create(cach_bdev, 'wt', dev, f"{rem_name}n1")
    logger.debug(ret)
    if not ret:
        logger.error("Failed to create OCF bdev")
        return False

    # logger.info("Creating local subsystem")
    # create subsystem (local)
    subsystem_nqn = lvol.nqn
    logger.info("Creating subsystem %s", subsystem_nqn)
    ret = rpc_client.subsystem_create(subsystem_nqn, 'sbcli-cn', lvol.get_id())
    ret = rpc_client.transport_list("TCP")
    if not ret:
        ret = rpc_client.transport_create("TCP")
    ret = rpc_client.listeners_create(subsystem_nqn, "TCP", '127.0.0.1', "4420")
    ret = rpc_client.nvmf_subsystem_listener_set_ana_state(subsystem_nqn, '127.0.0.1', "4420", is_optimized=True)

    # add cached device to subsystem
    # logger.info(f"Add {cach_bdev} to subsystem {subsystem_nqn}")
    # ret = rpc_client.nvmf_subsystem_add_ns(subsystem_nqn, cach_bdev)
    ret = rpc_client.nvmf_subsystem_add_ns(subsystem_nqn, cach_bdev, lvol.uuid, lvol.guid)

    if not ret:
        logger.error(f"Failed to add: {cach_bdev} to the subsystem: {subsystem_nqn}")
        return False

    logger.info("Connecting to local subsystem")
    # make nvme connect to nqn
    cnode_client = CNodeClient(cnode.api_endpoint)
    ret, _ = cnode_client.connect_nvme('127.0.0.1', "4420", subsystem_nqn)
    if not ret:
        logger.error("Failed to connect to local subsystem")
        return False

    snode = db_controller.get_storage_node_by_id(lvol.node_id)
    for nic in snode.data_nics:
        ip = nic.ip4_address
        ret, _ = cnode_client.connect_nvme(ip, "4420", subsystem_nqn)
        break

    cnode_info, _ = cnode_client.info()
    nvme_devs = cnode_info['nvme_devices']
    dev_path = None
    for dev in nvme_devs:
        if dev['model_id'] == lvol.get_id():
            dev_path = dev['device_path']
            break

    if not dev_path:
        logger.error(f"Device path was not found")
        return False

    logger.info(f"Device path: {dev_path}")

    cached_lvol = CachedLVol()
    cached_lvol.uuid = str(uuid.uuid4())
    cached_lvol.lvol_id = lvol.get_id()
    cached_lvol.lvol = lvol
    cached_lvol.hostname = cnode.hostname

    cached_lvol.local_nqn = subsystem_nqn
    cached_lvol.ocf_bdev = cach_bdev
    cached_lvol.device_path = dev_path

    tmp = []
    for lv in cnode.lvols:
        if lv.lvol_id != lvol.get_id():
            tmp.append(lv)

    tmp.append(cached_lvol)
    cnode.lvols = tmp
    cnode.write_to_db(db_controller.kv_store)

    return dev_path


def disconnect(caching_node_id, lvol_id):
    lvol = db_controller.get_lvol_by_id(lvol_id)
    if not lvol:
        logger.error(f"LVol not found: {lvol_id}")
        return False

    cnode = None
    if caching_node_id == 'this':
        hostn = utils.get_hostname()
        logger.info(f"Trying to get node by hostname: {hostn}")
        cnode = db_controller.get_caching_node_by_hostname(hostn)
    else:
        cnode = db_controller.get_caching_node_by_id(caching_node_id)
        if not cnode:
            logger.info(f"Caching node uuid not found: {caching_node_id}")
            cnode = db_controller.get_caching_node_by_hostname(caching_node_id)
            if not cnode:
                logger.error("Caching node not found")
                return False

    caching_lvol = None
    for clvol in cnode.lvols:
        if clvol.lvol_id == lvol_id:
            caching_lvol = clvol
            break
    if not caching_lvol:
        logger.error("LVol is not connected.")
        return False

    # logger.info("Disconnecting LVol")
    # disconnect local nvme
    cnode_client = CNodeClient(cnode.api_endpoint)
    subsystem_nqn = lvol.nqn

    try:
        ret, _ = cnode_client.disconnect_nqn(subsystem_nqn)
    except:
        pass
    # if not ret:
    #     logger.error("failed to disconnect local connecting")
    #     return False

    # remove subsystem
    rpc_client = RPCClient(
        cnode.mgmt_ip, cnode.rpc_port, cnode.rpc_username, cnode.rpc_password, timeout=120)

    ret = rpc_client.subsystem_delete(subsystem_nqn)

    # remove ocf bdev
    mini_id = lvol.get_id().split("-")[0]
    rem_name = f"rem_{mini_id}"
    cach_bdev = f"ocf_{mini_id}"

    ret = rpc_client.bdev_ocf_delete(cach_bdev)
    if not ret:
        logger.error("failed to delete ocf bdev")
        return False

    # disconnect lvol controller/s
    ret = rpc_client.bdev_nvme_detach_controller(rem_name)
    if not ret:
        logger.error("failed to disconnect remote connecting")
        return False

    # remove lvol id from node lvol list
    n_list = []
    for clvol in cnode.lvols:
        if clvol.lvol_id != lvol_id:
            n_list.append(clvol)
    cnode.lvols = n_list
    cnode.write_to_db(db_controller.kv_store)

    # if lvol.ha_type == 'single':
    #     ret = rpc_client.bdev_nvme_detach_controller(lvol.get_id())
    #
    # elif lvol.ha_type == "ha":
    #     for nodes_id in lvol.nodes:
    #         snode = db_controller.get_storage_node_by_id(nodes_id)
    #         ret = rpc_client.bdev_nvme_attach_controller_tcp(lvol.get_id(), lvol.nqn, nic.ip4_address, "4420")
    return True


def deploy(ifname):
    if not ifname:
        ifname = "eth0"

    logger.info("Installing dependencies...")
    ret = scripts.install_deps()

    dev_ip = utils.get_iface_ip(ifname)
    if not dev_ip:
        logger.error(f"Error getting interface ip: {ifname}")
        return False

    logger.info(f"Node IP: {dev_ip}")
    ret = scripts.configure_docker(dev_ip)

    node_docker = docker.DockerClient(base_url=f"tcp://{dev_ip}:2375", version="auto", timeout=60 * 5)
    # create the api container
    nodes = node_docker.containers.list(all=True)
    for node in nodes:
        if node.attrs["Name"] == "/CachingNodeAPI":
            logger.info("CachingNodeAPI container found, removing...")
            node.stop()
            node.remove(force=True)
            time.sleep(2)

    logger.info("Creating CachingNodeAPI container")
    container = node_docker.containers.run(
        constants.SIMPLY_BLOCK_DOCKER_IMAGE,
        "python simplyblock_web/caching_node_app.py",
        detach=True,
        privileged=True,
        name="CachingNodeAPI",
        network_mode="host",
        volumes=[
            '/etc/foundationdb:/etc/foundationdb',
            '/var/tmp:/var/tmp',
            '/var/run:/var/run',
            '/dev:/dev',
            '/lib/modules/:/lib/modules/',
            '/sys:/sys'],
        restart_policy={"Name": "always"},
        environment=[
            f"DOCKER_IP={dev_ip}"
        ]
    )
    logger.info("Pulling SPDK images")
    node_docker.images.pull(constants.SIMPLY_BLOCK_SPDK_CORE_IMAGE)
    node_docker.images.pull(constants.SIMPLY_BLOCK_SPDK_ULTRA_IMAGE)
    return f"{dev_ip}:5000"


def list_nodes(is_json=False):
    db_controller = DBController()
    nodes = db_controller.get_caching_nodes()
    data = []
    output = ""

    for node in nodes:
        logger.debug(node)
        logger.debug("*" * 20)
        data.append({
            "UUID": node.uuid,
            "Hostname": node.hostname,
            "Management IP": node.mgmt_ip,
            "LVOLs": f"{len(node.lvols)}",
            "Cache": utils.humanbytes(node.cache_size),
            "Ram": utils.humanbytes(node.memory),
            "HugeP": utils.humanbytes(node.hugepages),
            "Status": node.status,
            # "Updated At": datetime.datetime.strptime(node.updated_at, "%Y-%m-%d %H:%M:%S.%f").strftime(
            #     "%H:%M:%S, %d/%m/%Y"),
        })

    if not data:
        return output

    if is_json:
        output = json.dumps(data, indent=2)
    else:
        output = utils.print_table(data)
    return output


def list_lvols(caching_node_id):
    db_controller = DBController()
    cnode = None
    if caching_node_id == 'this':
        hostn = utils.get_hostname()
        logger.info(f"Trying to get node by hostname: {hostn}")
        cnode = db_controller.get_caching_node_by_hostname(hostn)
    else:
        cnode = db_controller.get_caching_node_by_id(caching_node_id)
        if not cnode:
            logger.info(f"Caching node uuid not found: {caching_node_id}")
            cnode = db_controller.get_caching_node_by_hostname(caching_node_id)
            if not cnode:
                logger.error("Caching node not found")
                return False

    data = []

    for clvol in cnode.lvols:
        lvol = clvol.lvol
        logger.debug(clvol)
        logger.debug("*" * 20)
        data.append({
            "UUID": lvol.get_id(),
            "Hostname": lvol.hostname,
            "Size": utils.humanbytes(lvol.size),
            "Path": clvol.device_path,
            "Status": lvol.status,
        })

    if not data:
        return ""

    return utils.print_table(data)


def remove_node(node_id, force=False):
    db_controller = DBController()
    snode = db_controller.get_caching_node_by_id(node_id)
    if not snode:
        logger.error(f"Can not find caching node: {node_id}")
        return False

    if snode.lvols:
        if force:
            for clvol in snode.lvols:
                logger.info(f"Disconnecting LVol {clvol.lvol_id}")
                disconnect(node_id, clvol.lvol_id)
        else:
            logger.error("Connected LVols found on the node, use --force to disconnect all")
            return False

    logger.info("Removing node")


    try:
        snode_api = CNodeClient(snode.api_endpoint)
        results, err = snode_api.spdk_process_kill()
    except:
        pass
    snode.remove(db_controller.kv_store)

    # storage_events.snode_remove(snode)
    logger.info("done")
