import logging
import os

KVD_DB_VERSION = 730
KVD_DB_FILE_PATH = '/etc/foundationdb/fdb.cluster'
KVD_DB_TIMEOUT_MS = 10000
SPK_DIR = '/home/ec2-user/spdk'
RPC_HTTP_PROXY_PORT = 8080
LOG_LEVEL = logging.INFO
LOG_WEB_DEBUG = True

FILE_DIR = os.path.dirname(os.path.realpath(__file__))
INSTALL_DIR = os.path.dirname(FILE_DIR)
TOP_DIR = os.path.dirname(INSTALL_DIR)

NODE_MONITOR_INTERVAL_SEC = 3
DEVICE_MONITOR_INTERVAL_SEC = 5
STAT_COLLECTOR_INTERVAL_SEC = 60*5  # 5 minutes
LVOL_STAT_COLLECTOR_INTERVAL_SEC = 2
LVOL_MONITOR_INTERVAL_SEC = 60
DEV_MONITOR_INTERVAL_SEC = 10
DEV_STAT_COLLECTOR_INTERVAL_SEC = 2
PROT_STAT_COLLECTOR_INTERVAL_SEC = 2
DISTR_EVENT_COLLECTOR_INTERVAL_SEC = 2
CAP_MONITOR_INTERVAL_SEC = 30
SSD_VENDOR_WHITE_LIST = ["1d0f:cd01", "1d0f:cd00"]

PMEM_DIR = '/tmp/pmem'

NVME_PROGRAM_FAIL_COUNT = 50
NVME_ERASE_FAIL_COUNT = 50
NVME_CRC_ERROR_COUNT = 50
DEVICE_OVERLOAD_STDEV_VALUE = 50
DEVICE_OVERLOAD_CAPACITY_THRESHOLD = 50

CLUSTER_NQN = "nqn.2023-02.io.simplyblock"

weights = {
    "lvol": 50,
    "cpu": 10,
    "r_io": 10,
    "w_io": 10,
    "r_b": 10,
    "w_b": 10
}

# To use 75% of hugepages to calculate ssd size to use for the ocf bdev
CACHING_NODE_MEMORY_FACTOR = 0.75

HEALTH_CHECK_INTERVAL_SEC = 60


SIMPLY_BLOCK_DOCKER_IMAGE = "simplyblock/simplyblock:dev"
SIMPLY_BLOCK_SPDK_CORE_IMAGE = "simplyblock/spdk-core:latest"
SIMPLY_BLOCK_SPDK_ULTRA_IMAGE = "simplyblock/spdk:main-latest"

GELF_PORT = 12201
